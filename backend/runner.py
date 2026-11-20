"""
runner.py — 模擬的「開始 / 停止 / 每 5 秒跑一次」狀態管理

- 同一時間只允許一個模擬在跑（避免全班一起按「開始」重複啟動）
- 每一輪：檢查 event 表有沒有新 insert → 讀資料 → simulate() → 結果寫回 sim_results
- 超過 MAX_MINUTES 會自動停止（保護免費額度）
- Render 重啟後，recover() 會依照 sim_status 決定要接續還是標記停止
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from database import DatabaseError, SupabaseDB
from simulation import build_message, simulate, taipei_now

log = logging.getLogger("runner")


class SimulationRunner:
    def __init__(self, db: SupabaseDB, interval: float = 5.0, max_minutes: float = 120,
                 only_on_new_data: bool = False):
        self.db = db
        self.interval = interval
        self.max_minutes = max_minutes
        self.only_on_new_data = only_on_new_data

        self._control_lock = threading.Lock()  # 保護 start / stop
        self._step_lock = threading.Lock()     # 保護 step（背景迴圈和「跑一次」不會同時算）
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self.run_id: str | None = None
        self.tick = 0
        self.started_at: datetime | None = None
        self.last_count = 0                       # 上一輪看到的事件筆數
        self.last_signature: tuple | None = None  # (筆數, 最新完工時間)
        self.last_tick_at: datetime | None = None
        self.last_message: str | None = None
        self.last_error: str | None = None
        self.stop_reason: str | None = None

    # ------------------------------------------------------------------ 狀態
    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop_event.is_set())

    def status(self) -> dict:
        return {
            "running": self.running,
            "run_id": self.run_id,
            "tick": self.tick,
            "interval_seconds": self.interval,
            "only_on_new_data": self.only_on_new_data,
            "max_minutes": self.max_minutes,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "last_tick_at": self.last_tick_at.isoformat() if self.last_tick_at else None,
            "event_count": self.last_count,
            "last_message": self.last_message,
            "last_error": self.last_error,
            "stop_reason": self.stop_reason,
        }

    # ------------------------------------------------------------------ 開始 / 停止
    def start(self, *, resume_run_id: str | None = None, resume_started_at: datetime | None = None,
              resume_tick: int = 0, resume_count: int | None = None) -> tuple[bool, str]:
        with self._control_lock:
            if self.running:
                return False, f"模擬已經在執行中（{self.run_id}），不會重複啟動"

            self.run_id = resume_run_id or taipei_now().strftime("run-%Y%m%d-%H%M%S")
            self.started_at = resume_started_at or datetime.now(timezone.utc)
            self.tick = resume_tick
            self.stop_reason = None
            self.last_error = None
            # 從「現在的筆數」開始算新資料；接續時沿用之前記錄的筆數
            self.last_signature = None
            self.last_count = (resume_count if resume_count is not None
                               else self.db.event_signature()[0])
            self.db.update_status(running=True, run_id=self.run_id,
                                  started_at=self.started_at.isoformat(),
                                  last_event_count=self.last_count,
                                  message="接續先前的模擬" if resume_run_id else "模擬啟動")

            self._stop_event = threading.Event()
            self._thread = threading.Thread(target=self._loop, args=(self._stop_event,),
                                            daemon=True, name="sim-loop")
            self._thread.start()
            verb = "接續" if resume_run_id else "啟動"
            return True, f"模擬已{verb}（{self.run_id}），每 {self.interval:g} 秒執行一次"

    def stop(self, reason: str = "使用者按下停止") -> tuple[bool, str]:
        with self._control_lock:
            if not self.running:
                return False, "目前沒有在執行的模擬"
            self._stop_event.set()
            self.stop_reason = reason
            thread = self._thread
        if thread:
            thread.join(timeout=self.interval + 15)
        self._mark_stopped(reason)
        return True, f"模擬已停止：{reason}"

    def _mark_stopped(self, reason: str) -> None:
        try:
            self.db.update_status(running=False, message=f"已停止：{reason}")
        except DatabaseError as e:
            self.last_error = str(e)

    # ------------------------------------------------------------------ 背景迴圈
    def _loop(self, stop_event: threading.Event) -> None:
        log.info("模擬迴圈開始 %s", self.run_id)
        while not stop_event.is_set():
            t0 = time.monotonic()
            if self.max_minutes and self.started_at:
                elapsed_min = (datetime.now(timezone.utc) - self.started_at).total_seconds() / 60
                if elapsed_min >= self.max_minutes:
                    stop_event.set()
                    self.stop_reason = f"已執行超過 {self.max_minutes:g} 分鐘，自動停止"
                    self._mark_stopped(self.stop_reason)
                    break
            try:
                self.step()
                self.last_error = None
            except Exception as e:  # 一輪失敗不要讓整個迴圈掛掉，下一輪再試
                log.exception("模擬這一輪失敗")
                self.last_error = str(e)
            # 5 秒是「目標」週期：扣掉這一輪花掉的時間再等待
            stop_event.wait(max(0.5, self.interval - (time.monotonic() - t0)))
        log.info("模擬迴圈結束 %s", self.run_id)

    # ------------------------------------------------------------------ 單一輪計算
    def step(self, force: bool = False) -> dict | None:
        with self._step_lock:
            # 先只問資料庫「幾筆、最新完工時間」，很省流量
            sig = self.db.event_signature()
            changed = sig != self.last_signature
            self.last_tick_at = datetime.now(timezone.utc)

            # 選用：資料庫沒有新的 insert 就不重算（第一輪一定會算）
            if self.only_on_new_data and not changed and not force and self.tick > 0:
                self.last_message = f"[{taipei_now():%H:%M:%S}] 沒有新資料，略過這一輪"
                return None

            rows = self.db.fetch_events()
            new_count = max(0, len(rows) - self.last_count)
            self.last_count = len(rows)
            self.last_signature = sig

            self.tick += 1
            result = simulate(rows)
            message = build_message(self.tick, result, new_count)
            saved = self.db.insert_result(self.run_id or "manual", self.tick, message, result)
            self.db.update_status(last_event_count=self.last_count, message=message)
            self.last_message = message
            return saved

    # ------------------------------------------------------------------ 服務重啟後的恢復
    def recover(self) -> str:
        st = self.db.get_status()
        if not st or not st.get("running"):
            return "沒有需要恢復的模擬"
        started = st.get("started_at")
        started_at = datetime.fromisoformat(started.replace("Z", "+00:00")) if started else None
        too_old = (not started_at or (self.max_minutes and
                   (datetime.now(timezone.utc) - started_at).total_seconds() / 60 >= self.max_minutes))
        if too_old:
            self._mark_stopped("服務重新啟動時已超過時限")
            return "先前的模擬已超過時限，標記為停止"
        last = self.db.fetch_results(limit=1, run_id=st.get("run_id"), with_payload=False)
        ok, msg = self.start(resume_run_id=st.get("run_id"), resume_started_at=started_at,
                             resume_tick=int(last[0]["tick"]) if last else 0,
                             resume_count=int(st.get("last_event_count") or 0))
        return msg

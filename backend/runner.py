"""
runner.py — 模擬的「開始 / 暫停 / 停止」狀態管理（每一次 Test 各自控制）

- 每一次 Test（TEST1～TEST10…）各有自己的狀態：執行中 / 已暫停 / 已停止
- 背景迴圈每 5 秒只計算「執行中」的 Test，其他 Test 不會跟著跑
- 開始：已停止 → 開一場新的模擬（從第 1 輪算起）；已暫停 → 接著上次繼續
- 暫停：先停下來，保留輪數、執行時間；之後按開始會「繼續」
- 停止：這一場模擬結束；下次按開始會重新開一場
- 每次 Test 的資料只屬於那次 Test，不會跟其他 Test 混在一起
- 同一個 Test 只保留最新一份計算結果（舊版本的計算結果會被覆蓋；原始資料不會自動刪）
- 空間用量超過上限時「只提示、不自動刪除」（刪除見 main.py 的 /api/delete-test）
- 某個 Test 累計執行超過 MAX_MINUTES 會自動停止（保護免費額度；暫停的時間不算）
- 各 Test 的狀態存在 sim_status.tests；Render 重啟後會自動接續（需要 update_v4.sql）
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from database import DatabaseError, SupabaseDB
from simulation import analyze_test, batch_of, taipei_now

log = logging.getLogger("runner")

RUNNING, PAUSED, STOPPED = "running", "paused", "stopped"
STATE_TEXT = {RUNNING: "執行中", PAUSED: "已暫停", STOPPED: "已停止"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TestRun:
    """一次 Test 的模擬狀態"""

    def __init__(self, batch: str):
        self.batch = batch
        self.state = STOPPED
        self.run_id: str | None = None
        self.tick = 0
        self.started_at: datetime | None = None
        self.active_sec = 0.0                 # 之前累計的執行秒數（不含暫停）
        self.resumed_at: datetime | None = None   # 這一段「執行中」從什麼時候開始
        self.last_tick_at: datetime | None = None
        self.last_message: str | None = None
        self.last_error: str | None = None
        self.stop_reason: str | None = None
        self.signature: tuple | None = None   # 上次計算時的（筆數, 最新完工時間）
        self.baseline_count: int | None = None   # 計算「新增幾筆」的基準

    def active_seconds(self) -> float:
        extra = (_now() - self.resumed_at).total_seconds() if self.state == RUNNING and self.resumed_at else 0
        return self.active_sec + extra

    def to_public(self) -> dict:
        return {
            "batch": self.batch,
            "state": self.state,
            "state_text": STATE_TEXT[self.state],
            "run_id": self.run_id,
            "tick": self.tick,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "active_seconds": round(self.active_seconds()),
            "last_tick_at": self.last_tick_at.isoformat() if self.last_tick_at else None,
            "last_message": self.last_message,
            "last_error": self.last_error,
            "stop_reason": self.stop_reason,
        }

    def to_saved(self) -> dict:
        return {"state": self.state, "run_id": self.run_id, "tick": self.tick,
                "started_at": self.started_at.isoformat() if self.started_at else None,
                "active_sec": round(self.active_seconds(), 1), "stop_reason": self.stop_reason,
                "baseline_count": self.baseline_count}

    @classmethod
    def from_saved(cls, batch: str, d: dict) -> "TestRun":
        t = cls(batch)
        t.state = d.get("state") if d.get("state") in STATE_TEXT else STOPPED
        t.run_id = d.get("run_id")
        t.tick = int(d.get("tick") or 0)
        s = d.get("started_at")
        t.started_at = datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None
        t.active_sec = float(d.get("active_sec") or 0)
        t.stop_reason = d.get("stop_reason")
        t.baseline_count = d.get("baseline_count")
        return t


class SimulationRunner:
    def __init__(self, db: SupabaseDB, interval: float = 5.0, max_minutes: float = 120,
                 only_on_new_data: bool = False, storage_limit_mb: float = 400):
        self.db = db
        self.interval = interval
        self.max_minutes = max_minutes
        self.only_on_new_data = only_on_new_data
        self.storage_limit_bytes = int(storage_limit_mb * 1024 * 1024)
        self.storage: dict[str, dict] = {}
        self.storage_checked_at: datetime | None = None

        self.tests: dict[str, TestRun] = {}
        self.persist_ok = db.has_test_state_column()   # 沒執行 update_v4.sql → 照樣能跑，只是重啟後不會自動接續
        self.last_count = 0                             # 整張 event 表的筆數（資料卡用）

        self._control_lock = threading.Lock()   # 保護 開始／暫停／停止
        self._step_lock = threading.Lock()      # 同一時間只算一個 Test
        self._quit = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="sim-loop")

    # ------------------------------------------------------------------ 背景迴圈（服務啟動時開，一直在）
    def start_loop(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def shutdown(self) -> None:
        """服務關閉（例如 Render 重新部署）：存下各 Test 狀態，不標記停止，重啟後會接續"""
        self._quit.set()
        self._save()

    def _loop(self) -> None:
        log.info("模擬迴圈開始（每 %g 秒檢查一次執行中的 Test）", self.interval)
        n = 0
        while not self._quit.is_set():
            t0 = time.monotonic()
            for batch in [b for b, t in list(self.tests.items()) if t.state == RUNNING]:
                t = self.tests[batch]
                if self.max_minutes and t.active_seconds() >= self.max_minutes * 60:
                    self._set_stopped(t, f"累計執行超過 {self.max_minutes:g} 分鐘，自動停止")
                    continue
                try:
                    self.step(batch)
                    t.last_error = None
                except Exception as e:   # 一輪失敗不要讓整個迴圈掛掉，下一輪再試
                    log.exception("%s 這一輪失敗", batch)
                    t.last_error = str(e)
            n += 1
            if n % 6 == 1 or self.storage_checked_at is None:   # 約每 30 秒量一次空間
                self.check_storage()
            self._quit.wait(max(0.5, self.interval - (time.monotonic() - t0)))
        log.info("模擬迴圈結束")

    # ------------------------------------------------------------------ 狀態
    def _get(self, batch: str) -> TestRun:
        if batch not in self.tests:
            self.tests[batch] = TestRun(batch)
        return self.tests[batch]

    def status(self) -> dict:
        tests = {b: t.to_public() for b, t in sorted(self.tests.items())
                 if t.state != STOPPED or t.run_id}
        return {
            "tests": tests,
            "running_tests": [b for b, t in sorted(self.tests.items()) if t.state == RUNNING],
            "paused_tests": [b for b, t in sorted(self.tests.items()) if t.state == PAUSED],
            "interval_seconds": self.interval,
            "max_minutes": self.max_minutes,
            "event_count": self.last_count,
            "persist_ok": self.persist_ok,
            **self.storage_summary(),
        }

    def test_status(self, batch: str) -> dict:
        return self._get(batch).to_public()

    def _save(self) -> None:
        if not self.persist_ok:
            return
        try:
            states = {b: t.to_saved() for b, t in self.tests.items() if t.state != STOPPED or t.run_id}
            self.db.save_test_states(states, any(t.state == RUNNING for t in self.tests.values()))
        except DatabaseError as e:
            log.warning("無法儲存 Test 狀態：%s", e)

    # ------------------------------------------------------------------ 開始 / 暫停 / 停止（都只針對一個 Test）
    def start(self, batch: str) -> tuple[bool, str]:
        with self._control_lock:
            t = self._get(batch)
            if t.state == RUNNING:
                return False, f"{batch} 已經在執行中（{t.run_id}），不會重複啟動"
            if t.state == PAUSED:
                t.state, t.resumed_at, t.stop_reason = RUNNING, _now(), None
                msg = f"{batch} 繼續模擬（{t.run_id}，從第 {t.tick + 1} 輪接著算）"
            else:
                t.run_id = f"{batch}-{taipei_now():%m%d-%H%M%S}"
                t.tick, t.active_sec, t.started_at = 0, 0.0, _now()
                t.state, t.resumed_at, t.stop_reason, t.last_error = RUNNING, _now(), None, None
                t.signature = None
                t.baseline_count = self.db.event_signature(batch)[0]   # 「新增幾筆」從現在開始算
                msg = f"{batch} 開始模擬（{t.run_id}），每 {self.interval:g} 秒計算一次"
            self._save()
        self.start_loop()
        return True, msg

    def pause(self, batch: str) -> tuple[bool, str]:
        with self._control_lock:
            t = self._get(batch)
            if t.state != RUNNING:
                return False, f"{batch} 目前{STATE_TEXT[t.state]}，不需要暫停"
            t.active_sec = t.active_seconds()
            t.state, t.resumed_at = PAUSED, None
            t.last_message = f"[{taipei_now():%H:%M:%S}] {batch} 已暫停（第 {t.tick} 輪）"
            self._save()
        return True, f"{batch} 已暫停（停在第 {t.tick} 輪），按「繼續模擬」會接著算"

    def stop(self, batch: str, reason: str = "使用者按下停止") -> tuple[bool, str]:
        with self._control_lock:
            t = self._get(batch)
            if t.state == STOPPED:
                return False, f"{batch} 目前沒有在模擬"
            self._set_stopped(t, reason, lock=False)
        return True, f"{batch} 模擬已停止（共 {t.tick} 輪）。下次按「開始模擬」會重新開一場"

    def _set_stopped(self, t: TestRun, reason: str, lock: bool = True) -> None:
        def do():
            t.active_sec = t.active_seconds()
            t.state, t.resumed_at, t.stop_reason = STOPPED, None, reason
            t.last_message = f"[{taipei_now():%H:%M:%S}] {t.batch} 已停止：{reason}"
            self._save()
        if lock:
            with self._control_lock:
                do()
        else:
            do()

    # ------------------------------------------------------------------ 單一 Test 的一輪計算
    def step(self, batch: str, force: bool = False) -> dict | None:
        with self._step_lock:
            t = self._get(batch)
            run_id = t.run_id or f"{batch}-manual"
            sig = self.db.event_signature(batch)          # 只問「這次 Test 幾筆、最新完工時間」
            changed = sig != t.signature
            t.last_tick_at = _now()

            if self.only_on_new_data and not changed and not force and t.tick > 0:
                t.last_message = f"[{taipei_now():%H:%M:%S}] {batch} 沒有新資料，略過這一輪"
                return None

            t.tick += 1
            base = t.baseline_count if t.baseline_count is not None else sig[0]
            new = max(0, sig[0] - base)
            head = f"[{taipei_now():%H:%M:%S}] {batch} 第 {t.tick} 次執行："
            if sig[0] == 0:
                message = head + "目前還沒有資料，等待學生開始生產"
            elif changed or force:
                rows = [r for r in self.db.fetch_events(batch=batch) if batch_of(r.get("product_id")) == batch]
                result = analyze_test(batch, rows)
                saved_t = self.db.insert_result(run_id, t.tick, f"{batch} 計算結果", result, batch=batch)
                if saved_t:   # 這個 Test 只留最新一份計算結果
                    self.db.delete_old_results(batch, int(saved_t["id"]))
                o = result["overall"]
                message = (head + f"讀到 {o['events']} 筆（本場累計新增 {new} 筆），{o['groups']} 組，"
                           f"完成 {o['completed']} 件，已重新計算")
            else:
                message = head + f"共 {sig[0]} 筆（本場累計新增 {new} 筆），這一輪沒有新資料，結果維持不變"
            t.signature = sig

            saved = self.db.insert_result(run_id, t.tick, message, {"test": batch}, batch=None)
            t.last_message = message
            if t.tick % 6 == 1:
                self._save()   # 定期存一下輪數與執行時間，重啟後才接得上
            return saved

    # ------------------------------------------------------------------ 空間管理（只提示，不自動刪除）
    def check_storage(self) -> dict:
        """量一次空間用量（event 原始資料＋計算結果）"""
        try:
            self.storage = self.db.storage_usage()
            self.last_count = self.storage.get("event", {}).get("rows", self.last_count)
            self.storage_checked_at = _now()
        except DatabaseError as e:
            log.warning("無法取得空間用量：%s", e)
        return self.storage_summary()

    def storage_summary(self) -> dict:
        used = sum(v["bytes"] for v in self.storage.values()) if self.storage else None
        limit = self.storage_limit_bytes
        return {
            "storage_used_mb": None if used is None else round(used / 1048576, 2),
            "storage_limit_mb": round(limit / 1048576, 2),
            "storage_pct": None if used is None or not limit else round(used / limit * 100, 1),
            "storage_warning": bool(used is not None and used >= limit),   # 空間不夠 → 網頁顯示警告
            "storage_detail": {k: round(v["bytes"] / 1048576, 2) for k, v in self.storage.items()},
        }

    def forget_test(self, batch: str) -> None:
        """某個 Test 被刪除後：停止它的模擬、清掉記錄"""
        with self._step_lock:
            t = self.tests.get(batch)
            if t and t.state != STOPPED:
                self._set_stopped(t, "這個 Test 的資料已被刪除")
            if t:
                t.signature, t.baseline_count = None, None
        self.check_storage()

    # ------------------------------------------------------------------ 服務重啟後的恢復
    def recover(self) -> str:
        if not self.persist_ok:
            return "尚未執行 update_v4.sql，重啟後不會自動接續"
        saved = self.db.load_test_states()
        msgs = []
        for batch, d in saved.items():
            t = TestRun.from_saved(batch, d)
            if t.state == RUNNING:
                if self.max_minutes and t.active_sec >= self.max_minutes * 60:
                    t.state, t.stop_reason = STOPPED, "服務重新啟動時已超過時限"
                    msgs.append(f"{batch} 超過時限→停止")
                else:
                    t.resumed_at = _now()
                    msgs.append(f"{batch} 接續執行")
            elif t.state == PAUSED:
                msgs.append(f"{batch} 維持暫停")
            self.tests[batch] = t
        self._save()
        return "、".join(msgs) or "沒有需要恢復的模擬"

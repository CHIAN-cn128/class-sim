"""
main.py — FastAPI 路由（部署在 Render）

本地測試：
    pip install -r requirements.txt
    uvicorn main:app --reload
然後打開 http://127.0.0.1:8000/docs 可以直接按按鈕測試每個 API。
"""
from __future__ import annotations

import hmac
import logging
import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from classes import CLASSES, PRACTICE_ID
from database import DatabaseError, SupabaseDB
from runner import SimulationRunner, make_key, split_key
import csv
import io

from simulation import batch_of, hello_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------- 設定（全部從環境變數讀）
INTERVAL_SECONDS = float(os.getenv("INTERVAL_SECONDS", "5"))
MAX_MINUTES = float(os.getenv("MAX_MINUTES", "120"))
ONLY_ON_NEW_DATA = _env_bool("ONLY_ON_NEW_DATA", False)
AUTO_RESUME = _env_bool("AUTO_RESUME", True)
STORAGE_LIMIT_MB = float(os.getenv("STORAGE_LIMIT_MB", "400"))  # 超過就提示「空間不夠」（免費方案上限 500 MB）
DELETE_PASSWORD = os.getenv("DELETE_PASSWORD", "")              # 刪除整個 Test 要輸入的密碼（沒設定 = 不能刪）
CONTROL_TOKEN = os.getenv("CONTROL_TOKEN", "").strip()  # 有設定時，開始/停止需要輸入這組控制碼
ALLOWED_ORIGINS = [o.strip().rstrip("/") for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

runner: SimulationRunner | None = None
class_errors: dict[str, str] = {}          # 班級設定有問題時的訊息（例如 key 格式不對）
_count_cache: dict[str, tuple[float, int | None, str | None]] = {}   # 各班 event 筆數（快取 30 秒）
setup_error: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global runner, setup_error
    try:
        own = SupabaseDB()
        sources, names = {}, {}
        for c in CLASSES:                       # 各班級的資料庫：老師的用 publishable key 唯讀；練習資料用自己的
            names[c["id"]] = c["name"]
            if c.get("own"):
                sources[c["id"]] = own
            elif c.get("url") and c.get("key"):
                try:
                    sources[c["id"]] = SupabaseDB(c["url"], c["key"], read_only=True,
                                                  event_table=c.get("table", "event"))
                except DatabaseError as e:
                    class_errors[c["id"]] = str(e)
        runner = SimulationRunner(own, interval=INTERVAL_SECONDS, max_minutes=MAX_MINUTES,
                                  only_on_new_data=ONLY_ON_NEW_DATA, storage_limit_mb=STORAGE_LIMIT_MB,
                                  sources=sources, class_names=names)
        runner.db.check_results_schema()
        if AUTO_RESUME:
            log.info("恢復檢查：%s", runner.recover())
        runner.start_loop()
    except DatabaseError as e:
        setup_error = str(e)
        log.error("Supabase 尚未設定好：%s", e)
    if ALLOWED_ORIGINS == ["*"]:
        log.warning("ALLOWED_ORIGINS 是 *（允許任何網站呼叫）。正式上課前請改成你的 GitHub Pages 網址。")
    yield
    if runner:
        runner.shutdown()   # 只存狀態、不標記停止：Render 重新部署後會自動接續


app = FastAPI(title="課堂 Python 模擬 API", version="1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Control-Token"],
)


def _runner() -> SimulationRunner:
    if runner is None:
        raise HTTPException(503, detail=f"後端還沒接上 Supabase：{setup_error}")
    return runner


def _check_token(token: str | None) -> None:
    if CONTROL_TOKEN and token != CONTROL_TOKEN:
        raise HTTPException(403, detail="控制碼錯誤：只有老師／組長可以開始、暫停或停止模擬")


def _class(class_id: str) -> str:
    """確認班級存在而且已設定好資料庫"""
    r = _runner()
    c = next((c for c in CLASSES if c["id"] == class_id), None)
    if not c:
        raise HTTPException(404, detail=f"沒有「{class_id}」這個班級")
    if class_id not in r.sources:
        raise HTTPException(400, detail=class_errors.get(class_id) or
                            f"{c['name']}的資料庫還沒設定：請在 backend/classes.py 填好網址和 publishable key")
    return class_id


def _key(class_id: str, batch: str) -> str:
    return make_key(_class(class_id), batch)


def _db_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except DatabaseError as e:
        raise HTTPException(502, detail=str(e))


# ---------------------------------------------------------------- 路由
@app.get("/")
def root():
    return {"service": "課堂 Python 模擬 API", "docs": "/docs", "hello": "/api/hello"}


@app.get("/health")
def health():
    return {"ok": True, "database_ready": runner is not None, "setup_error": setup_error,
            "event_table": runner.db.event_table if runner else None}


@app.get("/api/hello")
def hello():
    """目標一：不需要資料庫，確認『網頁 ↔ Python』這條路是通的"""
    return hello_text()


@app.get("/api/classes")
def classes():
    """班級清單（網頁上方的「班級」下拉選單）＋各班 event 表目前幾筆"""
    r = _runner()
    out = []
    for c in CLASSES:
        cid = c["id"]
        ok = cid in r.sources
        events, err = None, class_errors.get(cid)
        if ok:
            ts, n, e = _count_cache.get(cid, (0, None, None))
            if time.time() - ts > 30:
                try:
                    n, e = r.sources[cid].event_signature()[0], None
                except DatabaseError as ex:
                    n, e = None, str(ex)
                _count_cache[cid] = (time.time(), n, e)
            events, err = n, e
        out.append({"id": cid, "name": c["name"], "configured": ok, "read_only": not c.get("own"),
                    "events": events, "error": err})
    return {"classes": out, "default": next((x["id"] for x in out if x["configured"]), PRACTICE_ID)}


@app.get("/api/status")
def status():
    r = _runner()
    return {**r.status(), "control_token_required": bool(CONTROL_TOKEN)}


class TestControl(BaseModel):
    batch: str = Field(..., min_length=1, max_length=40, description="要控制的 Test，例如 TEST3")
    class_id: str = Field(PRACTICE_ID, min_length=1, max_length=20, description="班級，例如 A（甲班）")


@app.post("/api/start")
def start(req: TestControl, x_control_token: str | None = Header(default=None)):
    """開始（或繼續）某一次 Test 的模擬；其他 Test 不受影響"""
    _check_token(x_control_token)
    key = _key(req.class_id, req.batch)
    ok, msg = _db_call(_runner().start, key)
    return {"ok": ok, "message": msg, "test": _runner().test_status(key)}


@app.post("/api/pause")
def pause(req: TestControl, x_control_token: str | None = Header(default=None)):
    """暫停某一次 Test 的模擬（保留輪數與執行時間，之後可以繼續）"""
    _check_token(x_control_token)
    key = _key(req.class_id, req.batch)
    ok, msg = _runner().pause(key)
    return {"ok": ok, "message": msg, "test": _runner().test_status(key)}


@app.post("/api/stop")
def stop(req: TestControl, x_control_token: str | None = Header(default=None)):
    """停止某一次 Test 的模擬（這一場結束，下次開始會重新開一場）"""
    _check_token(x_control_token)
    key = _key(req.class_id, req.batch)
    ok, msg = _runner().stop(key, "使用者按下停止")
    return {"ok": ok, "message": msg, "test": _runner().test_status(key)}


@app.post("/api/run-once")
def run_once(req: TestControl, x_control_token: str | None = Header(default=None)):
    """不啟動迴圈，只替某一次 Test 跑一輪（測試用，網頁上沒有這個按鈕）"""
    _check_token(x_control_token)
    key = _key(req.class_id, req.batch)
    saved = _db_call(_runner().step, key, force=True)
    return {"ok": True, "result": saved, "test": _runner().test_status(key)}


@app.get("/api/results")
def results(limit: int = Query(15, ge=1, le=100), run_id: str | None = None,
            batch: str | None = Query(None, max_length=40),
            class_id: str = Query(PRACTICE_ID, max_length=20)):
    """最近幾輪的文字紀錄（Python 輸出紀錄）；給 batch 就只看那一班的那一次 Test"""
    key = make_key(class_id, batch) if batch else None
    return {"results": _db_call(_runner().db.fetch_log, limit=limit, run_id=run_id, batch=key)}


@app.get("/api/tests")
def tests(class_id: str = Query(PRACTICE_ID, max_length=20)):
    """這一班有計算結果的 Test 清單，以及各自最新一筆結果的 id（網頁用來判斷要不要重新下載）"""
    out = []
    for t in _db_call(_runner().db.fetch_test_index):
        c, b = split_key(t["batch"])
        if c == class_id:
            out.append({**t, "key": t["batch"], "batch": b})
    return {"tests": out}


@app.get("/api/test-result")
def test_result(batch: str = Query(..., min_length=1, max_length=40),
                class_id: str = Query(PRACTICE_ID, max_length=20)):
    """某一班某一次 Test 最新的完整計算結果（只含該次 Test 的資料）"""
    row = _db_call(_runner().db.fetch_test_result, make_key(class_id, batch))
    if not row:
        raise HTTPException(404, detail=f"{batch} 還沒有計算結果")
    return {**row, "key": row["batch"], "batch": batch, "class_id": class_id}


# ---------------------------------------------------------------- 空間管理：刪除一整個 Test
@app.get("/api/storage")
def storage(class_id: str = Query(PRACTICE_ID, max_length=20)):
    """自己資料庫的空間用量＋這一班每一次 Test 各佔多少（由最舊排到最新），刪除視窗用
    班級（甲班、乙班）：只列出存在自己資料庫的「計算結果」，原始資料在老師的資料庫，不會刪"""
    r = _runner()
    summary = r.check_storage()
    rows = []
    for t in _db_call(r.db.test_usage):
        c, b = split_key(t["batch"])
        if c != class_id:
            continue
        rows.append({"batch": b, "event_rows": t["event_rows"], "result_rows": t["result_rows"],
                     "size_mb": round((int(t["event_bytes"] or 0) + int(t["result_bytes"] or 0)) / 1048576, 3),
                     "first_start": t["first_start"]})
    return {**summary, "delete_enabled": bool(DELETE_PASSWORD), "read_only": class_id != PRACTICE_ID,
            "tests": rows}


EXPORT_COLUMNS = ["id", "group_id", "product_id", "station_id", "part_no", "wip_before_start",
                  "start_time", "finish_time", "duration_sec", "wip_after_finish", "status"]


@app.get("/api/test-export")
def test_export(batch: str = Query(..., min_length=1, max_length=40),
                class_id: str = Query(PRACTICE_ID, max_length=20)):
    """下載某一班某一次 Test 的原始資料（CSV，可用 Excel 開啟）。刪除前建議先下載"""
    src = _runner().sources[_class(class_id)]
    rows = [r for r in _db_call(src.fetch_events, batch=batch) if batch_of(r.get("product_id")) == batch]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    data = "\ufeff" + buf.getvalue()          # 加 BOM，Excel 開中文才不會亂碼
    return Response(content=data.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{class_id}_{batch}_backup.csv"'})


class DeleteTestRequest(BaseModel):
    batch: str = Field(..., min_length=1, max_length=40)
    class_id: str = Field(PRACTICE_ID, min_length=1, max_length=20)
    password: str = Field(..., min_length=1, max_length=200)


_fail_lock = threading.Lock()
_fail_times: list[float] = []      # 最近輸錯密碼的時間
MAX_FAILS, LOCK_SECONDS = 5, 600   # 10 分鐘內錯 5 次 → 鎖 10 分鐘


@app.post("/api/delete-test")
def delete_test(req: DeleteTestRequest):
    """輸入正確密碼後，刪除一整個 Test 的所有資料（event 原始資料＋計算結果），無法復原"""
    r = _runner()
    if not DELETE_PASSWORD:
        raise HTTPException(403, detail="刪除功能未開啟：請先在 Render 的 Environment 設定 DELETE_PASSWORD")
    now = time.time()
    with _fail_lock:
        _fail_times[:] = [t for t in _fail_times if now - t < LOCK_SECONDS]
        if len(_fail_times) >= MAX_FAILS:
            wait = int(LOCK_SECONDS - (now - _fail_times[0])) // 60 + 1
            raise HTTPException(429, detail=f"密碼錯誤太多次，請 {wait} 分鐘後再試")
        if not hmac.compare_digest(req.password.encode(), DELETE_PASSWORD.encode()):
            _fail_times.append(now)
            left = MAX_FAILS - len(_fail_times)
            if left <= 0:
                raise HTTPException(403, detail="密碼錯誤，已連續錯 5 次，刪除功能鎖定 10 分鐘")
            raise HTTPException(403, detail=f"密碼錯誤（再錯 {left} 次會鎖定 10 分鐘）")
        _fail_times.clear()

    key = make_key(req.class_id, req.batch)
    existing = {t["batch"] for t in _db_call(r.db.test_usage)}
    if key not in existing:
        raise HTTPException(404, detail=f"找不到 {r.label(key)} 的資料")
    r.forget_test(key)   # 先停掉這個 Test 的模擬（等正在算的那一輪結束），刪完才不會又被寫回來
    if req.class_id != PRACTICE_ID:
        # 班級：只刪自己資料庫裡的計算結果與輸出紀錄；老師資料庫裡的原始資料完全不動
        n = _db_call(r.db.delete_results_for, key)
        log.warning("已刪除 %s 的計算結果 %s 筆", key, n)
        return {"ok": True, "batch": req.batch, "event_deleted": 0, "result_deleted": n,
                "message": f"已刪除 {r.label(key)} 的計算結果 {n} 筆（老師資料庫裡的原始紀錄沒有動）",
                "storage": r.check_storage()}
    counts = _db_call(r.db.delete_test_data, req.batch)
    log.warning("已刪除 %s：event %s 筆、計算結果 %s 筆", req.batch, counts["event_deleted"], counts["result_deleted"])
    return {"ok": True, "batch": req.batch, **counts,
            "message": f"已刪除 {req.batch}：原始資料 {counts['event_deleted']} 筆、計算結果 {counts['result_deleted']} 筆",
            "storage": r.check_storage()}

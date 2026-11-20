"""
main.py — FastAPI 路由（部署在 Render）

本地測試：
    pip install -r requirements.txt
    uvicorn main:app --reload
然後打開 http://127.0.0.1:8000/docs 可以直接按按鈕測試每個 API。
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from database import DatabaseError, SupabaseDB
from runner import SimulationRunner
from simulation import hello_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------- 設定（全部從環境變數讀）
INTERVAL_SECONDS = float(os.getenv("INTERVAL_SECONDS", "5"))
MAX_MINUTES = float(os.getenv("MAX_MINUTES", "120"))
ONLY_ON_NEW_DATA = _env_bool("ONLY_ON_NEW_DATA", False)
AUTO_RESUME = _env_bool("AUTO_RESUME", True)
CONTROL_TOKEN = os.getenv("CONTROL_TOKEN", "").strip()  # 有設定時，開始/停止需要輸入這組控制碼
ALLOWED_ORIGINS = [o.strip().rstrip("/") for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

runner: SimulationRunner | None = None
setup_error: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global runner, setup_error
    try:
        runner = SimulationRunner(SupabaseDB(), interval=INTERVAL_SECONDS, max_minutes=MAX_MINUTES,
                                  only_on_new_data=ONLY_ON_NEW_DATA)
        if AUTO_RESUME:
            log.info("恢復檢查：%s", runner.recover())
    except DatabaseError as e:
        setup_error = str(e)
        log.error("Supabase 尚未設定好：%s", e)
    if ALLOWED_ORIGINS == ["*"]:
        log.warning("ALLOWED_ORIGINS 是 *（允許任何網站呼叫）。正式上課前請改成你的 GitHub Pages 網址。")
    yield
    if runner and runner.running:
        runner.stop("服務關閉")


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
        raise HTTPException(403, detail="控制碼錯誤：只有老師／組長可以開始或停止模擬")


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


@app.get("/api/status")
def status():
    r = _runner()
    return {**r.status(), "control_token_required": bool(CONTROL_TOKEN)}


@app.post("/api/start")
def start(x_control_token: str | None = Header(default=None)):
    _check_token(x_control_token)
    ok, msg = _db_call(_runner().start)
    return {"ok": ok, "message": msg, "status": _runner().status()}


@app.post("/api/stop")
def stop(x_control_token: str | None = Header(default=None)):
    _check_token(x_control_token)
    ok, msg = _runner().stop("使用者按下停止")
    return {"ok": ok, "message": msg, "status": _runner().status()}


@app.post("/api/run-once")
def run_once(x_control_token: str | None = Header(default=None)):
    """不啟動迴圈，只跑一輪（測試用）"""
    _check_token(x_control_token)
    saved = _db_call(_runner().step, force=True)
    return {"ok": True, "result": saved, "status": _runner().status()}


@app.get("/api/results")
def results(limit: int = Query(15, ge=1, le=100), run_id: str | None = None):
    """results：最近幾輪的文字紀錄（不含大資料）；latest：最新一輪的完整計算結果"""
    db = _runner().db
    log_rows = _db_call(db.fetch_results, limit=limit, run_id=run_id, with_payload=False)
    latest = _db_call(db.fetch_results, limit=1, run_id=run_id) if log_rows else []
    return {"results": log_rows, "latest": latest[0] if latest else None}

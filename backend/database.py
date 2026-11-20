"""
database.py — Supabase 存取（透過 Supabase 的 REST API，不需要額外 SDK）

讀取：老師前端寫入的生產事件表 event（唯讀，不會改動它）
寫入：sim_results（每輪計算結果）、sim_status（模擬狀態）

重要：這裡用的是「secret key」（sb_secret_...），只能放在 Render 的環境變數，
絕對不能寫進 GitHub 上的 HTML / JavaScript。
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import requests

PAGE_SIZE = 1000  # Supabase 每次最多回傳 1000 筆，超過要分頁


class DatabaseError(RuntimeError):
    """Supabase 讀寫失敗時丟出的錯誤（訊息會顯示在網頁上，方便除錯）"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SupabaseDB:
    def __init__(self, url: str | None = None, key: str | None = None, timeout: float = 15.0,
                 event_table: str | None = None):
        self.url = (url or os.getenv("SUPABASE_URL", "")).strip().rstrip("/")
        self.key = (key or os.getenv("SUPABASE_SECRET_KEY", "")).strip()
        self.event_table = (event_table or os.getenv("EVENT_TABLE", "event")).strip()
        if not self.url or not self.key:
            raise DatabaseError("尚未設定環境變數 SUPABASE_URL 或 SUPABASE_SECRET_KEY")
        if self.key.startswith("sb_publishable_"):
            raise DatabaseError("後端要用 secret key（sb_secret_...），不是 publishable key")

        self.base = f"{self.url}/rest/v1"
        self.timeout = timeout
        self.session = requests.Session()
        # 新版金鑰（sb_secret_ / sb_publishable_）只能放在 apikey header
        self.session.headers.update(
            {"apikey": self.key, "Content-Type": "application/json", "Accept": "application/json"}
        )
        # 舊版 service_role 金鑰是 JWT（eyJ 開頭），才需要再放 Authorization
        if self.key.startswith("eyJ"):
            self.session.headers["Authorization"] = f"Bearer {self.key}"

    # ------------------------------------------------------------------ 基本請求
    def _send(self, method: str, table: str, params: dict | None = None,
              body: Any = None, prefer: str | None = None) -> requests.Response:
        headers = {"Prefer": prefer} if prefer else None
        try:
            r = self.session.request(method, f"{self.base}/{table}", params=params, json=body,
                                     headers=headers, timeout=self.timeout)
        except requests.RequestException as e:
            raise DatabaseError(f"連不到 Supabase：{e}") from e
        if r.status_code >= 400:
            raise DatabaseError(f"Supabase {method} {table} 失敗（HTTP {r.status_code}）：{r.text[:300]}")
        return r

    def _request(self, *args, **kwargs) -> Any:
        r = self._send(*args, **kwargs)
        return r.json() if r.content else None

    # ------------------------------------------------------------------ 生產事件（老師前端寫入的表）
    def event_signature(self) -> tuple[int, str | None]:
        """回傳（總筆數, 最新完工時間）。只抓 1 筆，用來快速判斷資料庫有沒有新的 insert"""
        r = self._send("GET", self.event_table, params={
            "select": "finish_time", "order": "finish_time.desc.nullslast", "limit": "1",
        }, prefer="count=exact")
        total = 0
        cr = r.headers.get("Content-Range", "")  # 例如 "0-0/288"
        if "/" in cr and cr.split("/")[-1].isdigit():
            total = int(cr.split("/")[-1])
        rows = r.json() if r.content else []
        return total, (rows[0].get("finish_time") if rows else None)

    def fetch_events(self, max_rows: int = 20000) -> list[dict]:
        """抓整張 event 表（自動分頁），依開工時間排序"""
        out: list[dict] = []
        offset = 0
        while offset < max_rows:
            page = self._request("GET", self.event_table, params={
                "select": "*", "order": "start_time.asc,id.asc",
                "limit": str(PAGE_SIZE), "offset": str(offset),
            }) or []
            out.extend(page)
            if len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        return out

    def upsert_events(self, rows: list[dict], batch: int = 500) -> int:
        """匯入用：同一個 id 已存在就更新，不會重複"""
        n = 0
        for i in range(0, len(rows), batch):
            chunk = rows[i:i + batch]
            self._send("POST", self.event_table, params={"on_conflict": "id"}, body=chunk,
                       prefer="resolution=merge-duplicates,return=minimal")
            n += len(chunk)
        return n

    # ------------------------------------------------------------------ 模擬結果
    def insert_result(self, run_id: str, tick: int, message: str, payload: dict) -> dict | None:
        rows = self._request("POST", "sim_results", body={
            "run_id": run_id, "tick": tick, "message": message, "payload": payload,
        }, prefer="return=representation")
        return rows[0] if rows else None

    def fetch_results(self, limit: int = 20, run_id: str | None = None,
                      with_payload: bool = True) -> list[dict]:
        cols = "*" if with_payload else "id,run_id,tick,message,created_at"
        params = {"select": cols, "order": "id.desc", "limit": str(limit)}
        if run_id:
            params["run_id"] = f"eq.{run_id}"
        return self._request("GET", "sim_results", params=params) or []

    # ------------------------------------------------------------------ 模擬狀態（只有一列，id = 1）
    def get_status(self) -> dict | None:
        rows = self._request("GET", "sim_status", params={"select": "*", "id": "eq.1"}) or []
        return rows[0] if rows else None

    def update_status(self, **fields: Any) -> dict | None:
        fields["updated_at"] = utc_now_iso()
        rows = self._request("PATCH", "sim_status", params={"id": "eq.1"}, body=fields,
                             prefer="return=representation")
        if rows:
            return rows[0]
        rows = self._request("POST", "sim_status", body={"id": 1, **fields},
                             prefer="return=representation,resolution=merge-duplicates")
        return rows[0] if rows else None

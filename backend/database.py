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
                 event_table: str | None = None, read_only: bool = False):
        self.url = (url or os.getenv("SUPABASE_URL", "")).strip().rstrip("/")
        self.key = (key or os.getenv("SUPABASE_SECRET_KEY", "")).strip()
        self.event_table = (event_table or os.getenv("EVENT_TABLE", "event")).strip()
        if not self.url or not self.key:
            raise DatabaseError("尚未設定環境變數 SUPABASE_URL 或 SUPABASE_SECRET_KEY")
        # read_only：班級資料庫（老師的），只用 publishable key 讀 event 表，不寫入任何東西
        self.read_only = read_only
        if self.key.startswith("sb_publishable_") and not read_only:
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
        if self.read_only and method != "GET":
            raise DatabaseError("班級資料庫是唯讀的，不會寫入或刪除任何資料")   # 保護老師的資料庫
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
    @staticmethod
    def _test_filter(batch: str | None) -> dict:
        """只抓某一次 Test 的資料：TEST3 → 產品編號 TEST3-*；正式 → 產品編號裡沒有「-」"""
        if batch is None:
            return {}
        if batch == "正式":
            return {"product_id": "not.like.*-*"}
        return {"product_id": f"like.{batch}-*"}

    def event_signature(self, batch: str | None = None) -> tuple[int, str | None]:
        """回傳（筆數, 最新完工時間）。只抓 1 筆，用來快速判斷有沒有新的 insert；
        給 batch 就只看那一次 Test"""
        r = self._send("GET", self.event_table, params={
            "select": "finish_time", "order": "finish_time.desc.nullslast", "limit": "1",
            **self._test_filter(batch),
        }, prefer="count=exact")
        total = 0
        cr = r.headers.get("Content-Range", "")  # 例如 "0-0/288"
        if "/" in cr and cr.split("/")[-1].isdigit():
            total = int(cr.split("/")[-1])
        rows = r.json() if r.content else []
        return total, (rows[0].get("finish_time") if rows else None)

    def fetch_events(self, max_rows: int = 20000, batch: str | None = None) -> list[dict]:
        """抓 event 表（自動分頁），依開工時間排序；給 batch 就只抓那一次 Test"""
        out: list[dict] = []
        offset = 0
        while offset < max_rows:
            page = self._request("GET", self.event_table, params={
                "select": "*", "order": "start_time.asc,id.asc",
                "limit": str(PAGE_SIZE), "offset": str(offset), **self._test_filter(batch),
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
    # sim_results 有兩種列：
    #   batch = 'TEST3' 之類 → 該次 Test 的完整計算結果（只有那次 Test 有新資料時才新增）
    #   batch = null         → 每一輪的文字紀錄（很小）
    def insert_result(self, run_id: str, tick: int, message: str, payload: dict,
                      batch: str | None = None) -> dict | None:
        rows = self._request("POST", "sim_results", body={
            "run_id": run_id, "tick": tick, "message": message, "payload": payload, "batch": batch,
        }, prefer="return=representation")
        return rows[0] if rows else None

    def fetch_log(self, limit: int = 15, run_id: str | None = None, batch: str | None = None) -> list[dict]:
        """最近幾輪的文字紀錄（不含大資料）；給 batch 就只看那一次 Test 的紀錄"""
        params = {"select": "id,run_id,tick,message,created_at", "batch": "is.null",
                  "order": "id.desc", "limit": str(limit)}
        if run_id:
            params["run_id"] = f"eq.{run_id}"
        if batch:
            params["payload->>test"] = f"eq.{batch}"
        return self._request("GET", "sim_results", params=params) or []

    def fetch_test_index(self) -> list[dict]:
        """每個 Test 最新一筆結果的 id（用來做下拉選單、判斷網頁要不要重新下載）"""
        rows = self._request("GET", "sim_results", params={
            "select": "id,batch,tick,created_at", "batch": "not.is.null",
            "order": "id.desc", "limit": "2000",
        }) or []
        seen: dict[str, dict] = {}
        for r in rows:
            seen.setdefault(r["batch"], r)
        return list(seen.values())

    def fetch_test_result(self, batch: str) -> dict | None:
        rows = self._request("GET", "sim_results", params={
            "select": "*", "batch": f"eq.{batch}", "order": "id.desc", "limit": "1",
        }) or []
        return rows[0] if rows else None

    def check_results_schema(self) -> None:
        """確認已執行 update_v3.sql（sim_results 要有 batch 欄位、要有空間量測函式）"""
        try:
            self._request("GET", "sim_results", params={"select": "id,batch", "limit": "1"})
            self.storage_usage()
        except DatabaseError as e:
            raise DatabaseError("資料表還沒更新：請到 Supabase SQL Editor 執行 supabase/update_v3.sql"
                                f"（{e}）") from e

    # ------------------------------------------------------------------ 空間管理（需要 update_v3.sql 的函式）
    def storage_usage(self) -> dict[str, dict]:
        """{"event": {"bytes":..,"rows":..}, "sim_results": {...}} — 原始資料與計算結果各佔多少空間"""
        rows = self._request("POST", "rpc/storage_usage", body={}) or []
        return {r["table_name"]: {"bytes": int(r["total_bytes"] or 0), "rows": int(r["row_count"] or 0)}
                for r in rows}

    def test_usage(self) -> list[dict]:
        """每一次 Test 各佔多少空間，由最舊的 Test 排到最新"""
        return self._request("POST", "rpc/test_usage", body={}) or []

    def delete_test_data(self, batch: str) -> dict:
        """刪除一整個 Test 的所有資料（event 原始資料＋計算結果）。只能在密碼驗證通過後呼叫"""
        rows = self._request("POST", "rpc/delete_test_data", body={"p_batch": batch}) or []
        row = rows[0] if rows else {}
        return {"event_deleted": int(row.get("event_deleted") or 0),
                "result_deleted": int(row.get("result_deleted") or 0)}

    def delete_old_results(self, batch: str, keep_id: int) -> None:
        """同一個 Test 有新的計算結果後，舊版本的計算結果就用不到了（只刪計算結果，不碰原始資料）"""
        self._send("DELETE", "sim_results", params={"batch": f"eq.{batch}", "id": f"lt.{int(keep_id)}"},
                   prefer="return=minimal")

    def delete_results_for(self, key: str) -> int:
        """只刪除某個 Test 的「計算結果」與輸出紀錄（班級的原始資料在老師的資料庫，不會動到）"""
        n = 0
        for params in ({"batch": f"eq.{key}"}, {"batch": "is.null", "payload->>test": f"eq.{key}"}):
            rows = self._request("DELETE", "sim_results", params={**params, "select": "id"},
                                 prefer="return=representation") or []
            n += len(rows)
        return n

    # ------------------------------------------------------------------ 每個 Test 的模擬狀態（存在 sim_status.tests）
    def has_test_state_column(self) -> bool:
        """確認已執行 update_v4.sql（sim_status 要有 tests 欄位）"""
        try:
            self._request("GET", "sim_status", params={"select": "tests", "id": "eq.1"})
            return True
        except DatabaseError:
            return False

    def load_test_states(self) -> dict:
        rows = self._request("GET", "sim_status", params={"select": "tests", "id": "eq.1"}) or []
        return (rows[0].get("tests") if rows else None) or {}

    def save_test_states(self, states: dict, any_running: bool) -> None:
        self.update_status(tests=states, running=any_running)

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

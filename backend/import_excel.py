"""
import_excel.py — 把 ie_chair_data.xlsx（每組一個工作表）匯入 Supabase 的 event 表

只在「自己建的 Supabase 專案」需要用；如果是連老師原本的資料庫（event 已經有資料），不用執行。
同一個 id 重複匯入只會更新、不會變兩筆，可以放心重跑。

用法（在自己電腦）：
    pip install requests openpyxl
    # Windows PowerShell
    $env:SUPABASE_URL="https://xxxx.supabase.co"; $env:SUPABASE_SECRET_KEY="sb_secret_..."
    python import_excel.py ../data/ie_chair_data.xlsx
"""
from __future__ import annotations

import sys
from datetime import datetime

COLUMNS = ["id", "group_id", "product_id", "station_id", "part_no", "wip_before_start",
           "start_time", "finish_time", "duration_sec", "wip_after_finish", "status"]


def read_excel(path: str) -> list[dict]:
    import openpyxl  # 只有匯入時才需要

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows: list[dict] = []
    for ws in wb.worksheets:
        it = ws.iter_rows(values_only=True)
        header = next(it, None)
        if not header or header[0] != "id":   # 跳過「欄位名稱」說明頁
            continue
        idx = {name: i for i, name in enumerate(header) if name}
        for r in it:
            if not r or not r[0]:
                continue
            row = {}
            for c in COLUMNS:
                v = r[idx[c]] if c in idx else None
                if isinstance(v, datetime):
                    v = v.isoformat()
                row[c] = v
            row["status"] = row["status"] or "COMPLETED"
            rows.append(row)
        print(f"  {ws.title}: 累計 {len(rows)} 筆")
    return rows


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    rows = read_excel(sys.argv[1])
    print(f"共讀到 {len(rows)} 筆，開始匯入…")

    from database import DatabaseError, SupabaseDB
    try:
        db = SupabaseDB()
        n = db.upsert_events(rows)
        total, latest = db.event_signature()
        print(f"✔ 匯入 {n} 筆完成。{db.event_table} 表目前共 {total} 筆，最新完工時間 {latest}")
    except DatabaseError as e:
        print("✘", e)
        sys.exit(1)


if __name__ == "__main__":
    main()

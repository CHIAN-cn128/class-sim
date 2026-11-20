"""
check_setup.py — 在自己電腦上檢查 Supabase 有沒有設定好（不需要 FastAPI）

用法（Windows PowerShell）：
    pip install requests
    $env:SUPABASE_URL="https://xxxx.supabase.co"
    $env:SUPABASE_SECRET_KEY="sb_secret_..."
    python check_setup.py
"""
from database import DatabaseError, SupabaseDB
from simulation import build_message, hello_text, simulate


def main() -> None:
    print("目標一：", hello_text()["message"])
    try:
        db = SupabaseDB()
        print("✔ 金鑰格式正確，連線到", db.url)
        total, latest = db.event_signature()
        print(f"✔ {db.event_table} 表可讀取：共 {total} 筆，最新完工時間 {latest}")
        rows = db.fetch_events()
        result = simulate(rows)
        o = result["overall"]
        print(f"✔ Python 分析完成：{o['groups']} 組、完成 {o['completed']} 件、終點站 {o['final_station']}")
        for ln in result["lines"][:5]:
            print(f"   {ln['batch']} {ln['group_id']}：完成 {ln['completed']} 件，瓶頸 {ln['bottleneck']}"
                  f"（{ln['bottleneck_ct']} 秒），平衡效率 {ln['balance_pct']}%")
        print("✔ sim_status 可讀取：", db.get_status() is not None)
        saved = db.insert_result("check-setup", 0, build_message(0, result, 0), result)
        print("✔ sim_results 可寫入：", saved and saved.get("message"))
        print("\n全部通過！可以部署到 Render 了。")
    except DatabaseError as e:
        print("✘", e)
        print("請確認：1) 已在 Supabase SQL Editor 執行 supabase/results_tables.sql"
              "  2) event 表存在（自己的專案要先執行 event_table.sql 並匯入 Excel）  3) 用的是 sb_secret_ 金鑰")


if __name__ == "__main__":
    main()

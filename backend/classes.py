"""
classes.py — 班級（資料來源）設定

每個班級是一個 Supabase 資料庫，裡面有學生掃碼寫入的 event 表。
  - 甲班、乙班：老師的資料庫。只用 publishable key「讀取」event 表，絕對不會寫入或刪除。
  - 練習資料：你自己的資料庫（Render 環境變數 SUPABASE_URL / SUPABASE_SECRET_KEY），
              放老師 Excel 匯入的 288 筆練習資料。
所有班級的「計算結果」都存在你自己的資料庫，用班級代號分開，不會混在一起。

★ 拿到乙班的網址和 publishable key 後，填進下面乙班那一列的 url 和 key 就好。
  publishable key（sb_publishable_ 開頭）本來就是設計給網頁公開使用的，可以放在這裡；
  secret key（sb_secret_ 開頭）絕對不能寫在這裡。
"""

CLASSES = [
    {
        "id": "A",
        "name": "甲班",
        "url": "https://iomsrjkrvuzrsluirtdj.supabase.co",
        "key": "sb_publishable_QmLToEOs4wEEPBnASz0dxg_o0hhtgLO",
    },
    {
        "id": "B",
        "name": "乙班",
        "url": "",   # ← 填乙班的網址，例如 "https://xxxxxxxx.supabase.co"
        "key": "",   # ← 填乙班的 publishable key，例如 "sb_publishable_..."
    },
    {
        "id": "practice",
        "name": "練習資料",
        "own": True,  # 用自己的資料庫（Render 的 SUPABASE_URL / SUPABASE_SECRET_KEY）
    },
]

PRACTICE_ID = "practice"

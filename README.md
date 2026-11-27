# 椅子產線 Python 模擬（GitHub Pages + Render + Supabase）

```
學生掃碼前端 ──寫入──► Supabase  event 表（生產事件：哪組、哪件產品、哪個工站、開工／完工時間）
                              │
                              ▼ 每 5 秒讀取（secret key 只放在 Render）
                     Render：FastAPI + Python（backend/）
                              │ 計算結果寫回 sim_results
                              ▼
學生瀏覽器 ◄──HTTPS API── GitHub Pages（frontend/）顯示文字與產線指標
```

- **目標一**：網頁按「呼叫 Python」→ 顯示 Python 回傳的文字（不需要資料庫）。
- **目標二**：Python 每 5 秒讀取 `event` 表 → 算出各組產線指標 → 寫回 `sim_results` → 網頁顯示。

## Python 目前計算的指標（`backend/simulation.py`）

每一次 Test 各自計算，依組別（每組＝一條產線）分開；選「全部組別」時，比例類取各組平均、件數類加總。
網頁上：數字用卡片顯示，比例用圓餅圖顯示（滑鼠移到扇形上看數值）。

| 類別 | 指標 | 算法 |
|---|---|---|
| 時間 | 週期時間（產線） | 各站平均作業秒數中最慢的一站 |
| 時間 | 完工時間 Makespan | 該組第一筆開工 → 最後一張在終點站完工 |
| 時間 | 流程時間 | 每張椅子從投料（最早開工）到終點站完工；沿著「讓它能開工的前一道工序」往回追，拆成「加工＋等待」 |
| 產能 | 產出率 | 完成件數 ÷ 完工時間 × 3600（件/時） |
| 產能 | 瓶頸站 | 利用率最高的站（同分時看佇列較長者）；另外標出「排隊最長站」 |
| 效率 | 稼動率／閒置率 | 每站：實際作業秒數總和 ÷ 可用時間（該組第一筆開工到最後一筆完工）；閒置率＝1 − 稼動率 |
| 效率 | 平衡效率／平衡延遲 | 各站平均作業秒數總和 ÷（站數 × 週期時間）；平衡延遲＝1 − 平衡效率 |
| 效率 | OEE | 在瓶頸站衡量：可用率（實際作業 ÷ 可用時間）× 性能效率（理想週期 × 件數 ÷ 實際作業時間；理想週期＝該站最快 10% 的作業秒數）× 良率 |
| 在製品 | 目前 WIP／平均 WIP | 資料最後一刻還在線上的件數／時間加權平均在線件數 |
| 在製品 | 平均等待時間 | 每站開工前，等前一道工序完工的時間（起點站不算） |
| 在製品 | 平均佇列長度 | 開工時站前齊套數（`wip_before_start`，起點站的 9999 不算） |
| 在製品 | Little's Law 檢查 | 比較平均 WIP 與 產出率 × 流程時間；差 10% 以內標「合理」 |
| 品質 | 良率／不良率 | 終點站的 `status`：COMPLETED／OK／PASS 算良品；NG／DEFECT／SCRAP／FAIL 算不良 |
| 品質 | 重工率 | 有 `status = REWORK` 紀錄的產品 ÷ 全部產品 |

> 資料沒有停機紀錄與標準工時，所以 OEE 的可用率、性能效率是用上面的近似算法；
> 目前 Excel 資料的 status 全是 COMPLETED，良率會顯示 100%。

## 班級（甲班、乙班、練習資料）

網頁最上面的「班級」選單決定看哪一班。設定在 `backend/classes.py`：

| 班級 | 資料從哪來 | 權限 |
|---|---|---|
| 甲班、乙班 | 老師的 Supabase（`url` ＋ `publishable key`） | **只讀取** event 表，不會寫入或刪除 |
| 練習資料 | 自己的 Supabase（Render 的 `SUPABASE_URL`／`SUPABASE_SECRET_KEY`） | 可匯入、可刪除 |

- 所有班級的計算結果、模擬狀態都存在**自己的** Supabase，用班級代號分開（例如 `A:TEST3`），不會混在一起。
- 各班的 Test 各自開始／暫停／停止，互不影響。
- 甲班、乙班按「刪除整個 Test」只會刪掉系統算出來的結果；老師資料庫裡的原始紀錄不會動。
- 新增班級：在 `classes.py` 照格式多加一列（`id`、`name`、`url`、`key`），上傳到 GitHub，Render 會自動重新部署。

## 檔案配置

| 檔案 | 用途 |
|---|---|
| `frontend/index.html`、`style.css`、`app.js` | 網頁（GitHub Pages） |
| `frontend/config.js` | **部署後要改的設定**（Render 網址） |
| `backend/main.py` | FastAPI 路由 |
| `backend/runner.py` | 每個 Test 各自開始／暫停／停止、每 5 秒迴圈、防重複啟動、自動停止、重啟恢復 |
| `backend/simulation.py` | **模擬計算（之後老師下新指令，主要改這裡）** |
| `backend/database.py` | Supabase 存取 |
| `backend/classes.py` | **班級設定**（甲班、乙班的網址與 publishable key） |
| `backend/import_excel.py` | 把 Excel 匯入 `event` 表 |
| `backend/check_setup.py` | 在自己電腦檢查 Supabase 是否設定好 |
| `supabase/event_table.sql` | 建立 `event` 表（**只在自己的新專案執行**） |
| `supabase/results_tables.sql` | 建立 `sim_results`、`sim_status`（不會動到 event） |
| `supabase/update_v3.sql` | 更新：每個 Test 獨立存放＋空間用量＋刪除整個 Test 的函式（舊專案要補跑一次） |
| `supabase/update_v4.sql` | 更新：存每個 Test 的模擬狀態（重啟後自動接續；舊專案要補跑一次） |
| `data/ie_chair_data.xlsx` / `.csv` | 老師提供的資料（6 組、288 筆） |
| `render.yaml` | Render 一鍵建立服務 |
| `.github/workflows/pages.yml` | 自動把 `frontend/` 發佈到 GitHub Pages |

---

## 部署步驟

### 步驟 1：準備 Supabase（二選一）

**路線 A：自己開一個 Supabase 專案（建議先用這個測試）**

1. <https://supabase.com> → New project（免費）。
2. 左側 **SQL Editor** → 貼上 `supabase/event_table.sql` → Run。
3. 再貼上 `supabase/results_tables.sql` → Run。
4. 到 **Project Settings → Data API** 複製 **Project URL**；到 **Project Settings → API Keys** 複製 **secret key**（`sb_secret_...`）。
5. 在自己電腦匯入 Excel：
   ```powershell
   cd backend
   pip install requests openpyxl
   $env:SUPABASE_URL="https://xxxx.supabase.co"; $env:SUPABASE_SECRET_KEY="sb_secret_..."
   python import_excel.py ../data/ie_chair_data.xlsx
   python check_setup.py
   ```
   看到「匯入 288 筆完成」和「全部通過！」就 OK。（重複匯入不會變兩倍。）

**路線 B：接老師原本的 Supabase（老師的掃碼前端寫入的那個）**

需要向老師拿 **Project URL** 和 **secret key**。只執行 `supabase/results_tables.sql`，
**不要**執行 event_table.sql、也不需要匯入 Excel（老師的 event 表已經有資料）。
Python 對 event 表只會讀取，不會修改。表名如果不是 `event`，在 Render 設 `EVENT_TABLE`。

⚠ secret key 只能貼到 Render，不可以放進 GitHub、也不要傳給別人。

### 步驟 2：上傳到 GitHub

建立一個新的 repository（例如 `class-sim`），把整個資料夾內容上傳（保留 `.github` 資料夾）。

### 步驟 3：Render 部署 Python 後端

1. 登入 <https://render.com>（可用 GitHub 帳號登入）→ **New → Blueprint** → 選剛剛的 repo。
2. 填環境變數：

   | 名稱 | 填什麼 |
   |---|---|
   | `SUPABASE_URL` | 步驟 1 的 Project URL |
   | `SUPABASE_SECRET_KEY` | 步驟 1 的 `sb_secret_...` |
   | `ALLOWED_ORIGINS` | `https://你的GitHub帳號.github.io`（結尾不要 `/`） |
   | `CONTROL_TOKEN` | 自訂控制碼（留空 = 任何人都能按開始/停止） |

3. 部署完成後用瀏覽器測試：
   - `https://你的服務.onrender.com/api/hello` → 看到 `Hello from Python！…` ✅ 目標一的後端完成
   - `https://你的服務.onrender.com/health` → `"database_ready": true` ✅ Supabase 接上
   - `https://你的服務.onrender.com/docs` → 可以直接按按鈕測試每支 API

> 不用 Blueprint 的話：New → Web Service，Root Directory `backend`，
> Build `pip install -r requirements.txt`，Start `uvicorn main:app --host 0.0.0.0 --port $PORT`，再手動加環境變數。

### 步驟 4：前端發佈到 GitHub Pages

1. 編輯 `frontend/config.js`，把 `API_BASE` 改成 Render 網址（例如 `https://class-sim-api.onrender.com`）。
2. GitHub repo → **Settings → Pages → Source 選「GitHub Actions」**。
3. Commit 後 Actions 會自動發佈，網址是 `https://你的帳號.github.io/class-sim/`。

### 步驟 5：驗收

1. 打開網頁，右上角顯示「Python 後端已連線」。
2. 按 **呼叫 Python** → 黑框出現 Python 的文字。**（目標一）**
3. 在控制卡的「控制的 Test」選一個 Test，按 **開始模擬** → 約 5 秒內出現該 Test 的各組產線表現、各站長條圖、輸出紀錄。**（目標二）**
4. 切換 TEST1／TEST2，點某一組看該組各站作業秒數（紅色＝瓶頸）。
5. 有新事件寫入時，輸出紀錄會顯示「本場累計新增 N 筆」。按 **暫停** 先停下（之後按「繼續模擬」接著算），按 **停止模擬** 結束這一場。

---

## 已內建的安全與穩定設計（對應文件第六節）

- **secret key 只在後端**；網頁只呼叫 Render API，不放任何 Supabase 金鑰。結果表網頁端只能讀。
- **CORS**：`ALLOWED_ORIGINS` 只允許你的 GitHub Pages 網域呼叫。
- **權限控管**：設 `CONTROL_TOKEN` 後，開始／停止要輸入控制碼。
- **每個 Test 各自控制**：開始／暫停／停止只影響「控制的 Test」選到的那一個，其他 Test 不會一起跑。
- **不重複啟動**：同一個 Test 已在執行時再按「開始」會被拒絕。
- **5 秒是目標週期**：扣掉計算時間再等待；網路慢時順延，不會疊在一起。
- **偵測新 insert**：每輪先只查「筆數＋最新完工時間」（很省流量）。`ONLY_ON_NEW_DATA=true` 時，沒有新事件就不重算。
- **自動停止**：某個 Test 累計執行超過 `MAX_MINUTES`（預設 120 分鐘，暫停時間不算）自動停止。
- **重啟恢復**：Render 重啟或重新部署後，執行中的 Test 會自動接續同一場、暫停中的維持暫停（需執行 `update_v4.sql`）。
- **每次 Test 各自獨立**：TEST1～TEST10 的資料各算各的、各存各的，不會混在一起；哪一次 Test 有新資料，就只重算那一次。
- **空間管理（不會自動刪除）**：每個 Test 只保留一份最新的計算結果。資料空間（event 原始資料＋計算結果）
  超過 `STORAGE_LIMIT_MB`（預設 400 MB，免費方案上限 500 MB）時，網頁上方會出現紅色「空間不夠」提示。
  按「選擇要刪除的 Test」→ 選 Test（由最舊排到最新）→（建議先下載 CSV 備份）→ 輸入密碼 → 確認刪除，
  會一次刪掉那個 Test 的**所有資料**（原始紀錄＋計算結果），無法復原。
  密碼設在 Render 的 `DELETE_PASSWORD`；沒設定就不能刪。密碼連錯 5 次會鎖定 10 分鐘。
  （需先在 Supabase SQL Editor 執行 `supabase/update_v3.sql`）

## 免費方案注意事項

- Render 免費服務約 15 分鐘沒人連線會休眠，喚醒要 30–60 秒；上課前先打開網頁暖機。網頁開著會每 5 秒查詢，讓它保持清醒。
- 要全天候穩定執行，需改用付費的 Background Worker。

## 之後要換成新的模擬

改 `backend/simulation.py` 的 `simulate(events)`：輸入是 event 表的資料列，回傳一個 dict（存進 `sim_results.payload`）。
回傳結構改了的話，再調整 `frontend/app.js` 的 `renderResult()`。

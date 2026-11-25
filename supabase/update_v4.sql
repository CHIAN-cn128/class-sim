-- =====================================================================
-- update_v4.sql — 第四版更新：每個 Test 各自開始／暫停／停止
-- 在 sim_status 加一個欄位，存每個 Test 的模擬狀態（Render 重啟後才能自動接續）
-- 貼到 Supabase 後台 → SQL Editor → Run（可以重複執行，不會動到任何資料）
-- =====================================================================
alter table public.sim_status add column if not exists tests jsonb not null default '{}'::jsonb;

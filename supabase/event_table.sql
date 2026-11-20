-- =====================================================================
-- event_table.sql — 只有「自己新建的 Supabase 專案」才需要執行
-- （老師的資料庫已經有 event 表，請不要在那邊執行這個檔案）
-- 欄位依照 ie_chair_data.xlsx 的「欄位名稱」頁建立
-- =====================================================================

create table if not exists public.event (
  id                uuid primary key default gen_random_uuid(),  -- 事件唯一識別碼
  group_id          text not null,          -- 組別代號 G01~G12
  station_id        text not null,          -- 工站代號 ST01~ST09
  product_id        text not null,          -- 產品序號，TEST1-P01（練習）/ P01（正式）
  part_no           text,
  start_time        timestamptz,            -- 開工時間
  finish_time       timestamptz,            -- 完工時間
  duration_sec      numeric,                -- 實際作業秒數（Cycle Time）
  wip_before_start  integer,                -- 開工瞬間齊套數（起點站 9999）
  wip_after_finish  integer,                -- 完工後累計件數
  status            text not null default 'COMPLETED'
);
create index if not exists event_group_idx  on public.event (group_id, start_time);
create index if not exists event_finish_idx on public.event (finish_time desc);

-- 網頁（publishable key）可以讀取與新增事件
alter table public.event enable row level security;
drop policy if exists "public read event"   on public.event;
drop policy if exists "public insert event" on public.event;
create policy "public read event"   on public.event for select to anon, authenticated using (true);
create policy "public insert event" on public.event for insert to anon, authenticated
  with check (char_length(group_id) <= 10 and char_length(station_id) <= 10 and char_length(product_id) <= 40);

grant select, insert on public.event to anon, authenticated;
grant all on public.event to service_role;

-- =====================================================================
-- results_tables.sql — Python 模擬要用的兩張新表（不會動到 event 表）
-- 貼到 Supabase 後台 → SQL Editor → Run（可以重複執行）
--   sim_results  Python 每一輪的文字輸出與計算結果
--   sim_status   模擬目前狀態（只有一列，id = 1）
-- =====================================================================

create table if not exists public.sim_results (
  id          bigint generated always as identity primary key,
  run_id      text not null,
  tick        integer not null,
  message     text not null,                         -- Python 印出來的文字（目標一）
  payload     jsonb not null default '{}'::jsonb,    -- 計算結果（目標二）
  created_at  timestamptz not null default now()
);
create index if not exists sim_results_run_idx on public.sim_results (run_id, id desc);

create table if not exists public.sim_status (
  id                integer primary key default 1 check (id = 1),
  running           boolean not null default false,
  run_id            text,
  started_at        timestamptz,
  last_event_count  integer not null default 0,
  message           text,
  updated_at        timestamptz not null default now()
);
insert into public.sim_status (id) values (1) on conflict (id) do nothing;

-- 網頁（publishable key）只能讀；寫入只有後端（secret key）可以
alter table public.sim_results enable row level security;
alter table public.sim_status  enable row level security;
drop policy if exists "public read results" on public.sim_results;
drop policy if exists "public read status"  on public.sim_status;
create policy "public read results" on public.sim_results for select to anon, authenticated using (true);
create policy "public read status"  on public.sim_status  for select to anon, authenticated using (true);

grant select on public.sim_results, public.sim_status to anon, authenticated;
grant all on public.sim_results, public.sim_status to service_role;
grant usage, select on all sequences in schema public to service_role;

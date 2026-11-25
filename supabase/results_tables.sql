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
  batch       text,                                  -- 屬於哪一次 Test（null = 每輪的文字紀錄）
  created_at  timestamptz not null default now()
);
create index if not exists sim_results_run_idx on public.sim_results (run_id, id desc);
create index if not exists sim_results_batch_idx on public.sim_results (batch, id desc);

create table if not exists public.sim_status (
  id                integer primary key default 1 check (id = 1),
  running           boolean not null default false,
  run_id            text,
  started_at        timestamptz,
  last_event_count  integer not null default 0,
  tests             jsonb not null default '{}'::jsonb,   -- 每個 Test 的模擬狀態
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

-- ---------------------------------------------------------------------
-- 空間管理與刪除整個 Test 的函式（與 update_v3.sql 相同）
-- 4) 產品編號 → 屬於哪一次 Test（和 Python 的 batch_of() 規則相同）
--    TEST3-P01 → TEST3；P01（沒有前綴）→ 正式
create or replace function public.batch_of(product_id text)
returns text language sql immutable
as $$
  select case when position('-' in coalesce(product_id, '')) > 0
              then regexp_replace(product_id, '-[^-]*$', '')
              else '正式' end;
$$;

-- 5) 整體空間用量：event 原始資料＋計算結果
create or replace function public.storage_usage()
returns table (table_name text, total_bytes bigint, row_count bigint)
language sql stable security definer set search_path = public
as $$
  select 'event'::text, coalesce(sum(pg_column_size(e.*)), 0)::bigint, count(*)::bigint from public.event e
  union all
  select 'sim_results'::text, coalesce(sum(pg_column_size(r.*)), 0)::bigint, count(*)::bigint from public.sim_results r;
$$;

-- 6) 每一次 Test 各佔多少空間（由最舊的 Test 排到最新）
create or replace function public.test_usage()
returns table (batch text, event_rows bigint, event_bytes bigint,
               result_rows bigint, result_bytes bigint, first_start timestamptz)
language sql stable security definer set search_path = public
as $$
  with ev as (
    select public.batch_of(e.product_id) as b, count(*) as n,
           sum(pg_column_size(e.*)) as sz, min(e.start_time) as fs
    from public.event e group by 1
  ), rs as (
    select r.batch as b, count(*) as n, sum(pg_column_size(r.*)) as sz
    from public.sim_results r where r.batch is not null group by 1
  )
  select coalesce(ev.b, rs.b), coalesce(ev.n, 0)::bigint, coalesce(ev.sz, 0)::bigint,
         coalesce(rs.n, 0)::bigint, coalesce(rs.sz, 0)::bigint, ev.fs
  from ev full outer join rs on ev.b = rs.b
  order by ev.fs nulls last, 1;
$$;

-- 7) 刪除一整個 Test 的所有資料（event 原始資料＋該 Test 的計算結果）
create or replace function public.delete_test_data(p_batch text)
returns table (event_deleted bigint, result_deleted bigint)
language plpgsql security definer set search_path = public
as $$
declare
  n_ev bigint;
  n_rs bigint;
begin
  if p_batch is null or length(trim(p_batch)) = 0 then
    raise exception '必須指定要刪除的 Test';
  end if;
  delete from public.event e where public.batch_of(e.product_id) = p_batch;
  get diagnostics n_ev = row_count;
  delete from public.sim_results r where r.batch = p_batch;
  get diagnostics n_rs = row_count;
  return query select n_ev, n_rs;
end;
$$;

-- 這些函式只給後端（secret key）用，網頁端不能呼叫
revoke all on function public.storage_usage() from public, anon, authenticated;
revoke all on function public.test_usage() from public, anon, authenticated;
revoke all on function public.delete_test_data(text) from public, anon, authenticated;
grant execute on function public.storage_usage() to service_role;
grant execute on function public.test_usage() to service_role;
grant execute on function public.delete_test_data(text) to service_role;
grant delete on public.sim_results to service_role;
grant delete on public.event to service_role;

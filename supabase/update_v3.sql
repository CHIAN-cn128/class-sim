-- =====================================================================
-- update_v3.sql — 第三版更新（可以重複執行；沒跑過 update_v2.sql 也沒關係，這份已包含）
-- 貼到 Supabase 後台 → SQL Editor → Run
--
--   - 每個 Test 的計算結果分開存放（sim_results.batch）
--   - 量測空間用量（event 原始資料＋計算結果）
--   - 一次刪除「一整個 Test」的所有資料（只有後端輸入正確密碼後才會呼叫）
-- =====================================================================

-- 1) 計算結果加上「屬於哪一次 Test」的欄位
alter table public.sim_results add column if not exists batch text;
create index if not exists sim_results_batch_idx on public.sim_results (batch, id desc);

-- 2) 舊版把所有 Test 混在一起算的結果，直接清掉（之後會依 Test 重新計算）
delete from public.sim_results where batch is null and payload ? 'lines';

-- 3) 拿掉第二版「自動逐筆刪除」用的函式
drop function if exists public.sim_results_oldest(integer, bigint[]);
drop function if exists public.sim_results_usage();

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

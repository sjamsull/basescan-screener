-- Basescan schema — jalankan di Supabase SQL Editor (atau via Management API).
create extension if not exists "pgcrypto";

create table if not exists scans (
  id bigint generated always as identity primary key,
  chain text not null,
  mode text not null default 'accumulation',
  token_count int not null default 0,
  scanned_at timestamptz not null default now(),
  payload jsonb not null default '[]'::jsonb
);

create index if not exists scans_chain_at_idx on scans (chain, scanned_at desc);

create table if not exists reject_log (
  id bigint generated always as identity primary key,
  token_address text,
  chain text,
  mode text,
  reason text,
  rejected_at timestamptz not null default now()
);

create index if not exists reject_log_chain_idx on reject_log (chain, rejected_at desc);

create table if not exists signals (
  token_address text primary key,
  chain text not null,
  verdict text not null,
  alpha float8 not null default 0,
  risk float8 not null default 0,
  prepared_data jsonb,
  signal_at timestamptz not null default now(),
  status text not null default 'ACTIVE',
  exit_price float8,
  exit_at timestamptz,
  pnl_pct float8,
  note text
);

create index if not exists signals_verdict_idx on signals (verdict);
create index if not exists signals_at_idx on signals (signal_at desc);

-- RLS: dashboard baca dengan anon key; tulis service_role (bypass RLS).
alter table scans enable row level security;
alter table reject_log enable row level security;
alter table signals enable row level security;

drop policy if exists scans_read on scans;
create policy scans_read on scans for select using (true);

drop policy if exists reject_log_read on reject_log;
create policy reject_log_read on reject_log for select using (true);

drop policy if exists signals_read on signals;
create policy signals_read on signals for select using (true);
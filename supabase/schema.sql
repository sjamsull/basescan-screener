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

-- Backtest tracker: riwayat mcap per sinyal + hasil evaluasi.
create table if not exists signal_tracks (
  id bigint generated always as identity primary key,
  token_address text not null,
  chain text not null,
  tracked_at timestamptz not null default now(),
  mcap float8,
  price float8,
  liquidity float8
);

create index if not exists signal_tracks_token_idx on signal_tracks (token_address, tracked_at asc);

create table if not exists backtest_reports (
  id bigint generated always as identity primary key,
  generated_at timestamptz not null default now(),
  signals int not null default 0,
  tracked_ticks int not null default 0,
  no_quote int not null default 0,
  states jsonb not null default '{}'::jsonb
);

alter table signals add column if not exists best_tp int not null default 0;
alter table signals add column if not exists tp1_at timestamptz;
alter table signals add column if not exists tp2_at timestamptz;
alter table signals add column if not exists tp3_at timestamptz;

-- RLS: dashboard baca dengan anon key; tulis service_role (bypass RLS).
alter table scans enable row level security;
alter table reject_log enable row level security;
alter table signals enable row level security;
alter table signal_tracks enable row level security;
alter table backtest_reports enable row level security;

drop policy if exists scans_read on scans;
create policy scans_read on scans for select using (true);

drop policy if exists reject_log_read on reject_log;
create policy reject_log_read on reject_log for select using (true);

drop policy if exists signals_read on signals;
create policy signals_read on signals for select using (true);

drop policy if exists signal_tracks_read on signal_tracks;
create policy signal_tracks_read on signal_tracks for select using (true);

drop policy if exists backtest_reports_read on backtest_reports;
create policy backtest_reports_read on backtest_reports for select using (true);
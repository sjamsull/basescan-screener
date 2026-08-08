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
  states jsonb not null default '{}'::jsonb,
  metrics jsonb not null default '{}'::jsonb
);

alter table signals add column if not exists best_tp int not null default 0;
alter table signals add column if not exists tp1_at timestamptz;
alter table signals add column if not exists tp2_at timestamptz;
alter table signals add column if not exists tp3_at timestamptz;
alter table signals add column if not exists time_to_tp1_h float8;  -- ditulis backtest (baseline track pertama, konsisten dengan simulate)
alter table signals add column if not exists symbol text;           -- nama/ticker token (dashboard track record)

-- Dead-whale detection: posisi whale per token (ledger state utk deteksi beli cicil/hold).
create table if not exists dead_token_universe (
  token_address text primary key,
  chain text not null,
  symbol text,
  name text,
  decimals int not null default 18,
  total_supply text,
  holders int not null default 0,
  created_at timestamptz,
  volume_24h float8 not null default 0,
  market_cap float8 not null default 0,
  risk_flags text,
  security_json text,  -- GMGN /v1/token/security (top10, rug, tax, honeypot, flags) sebagai JSON mini
  verrow_json text,    -- VERROW /api/scan report (chain 4663 robinhood) — risk, findings, ownership, liquidity lock
  first_seen timestamptz not null default now(),
  last_seen timestamptz not null default now()
);

-- Uncomment untuk menambahkan kolom pada DB yang sudah ada (jalankan di Supabase SQL Editor):
-- alter table dead_token_universe add column if not exists verrow_json text;

create index if not exists dead_universe_chain_idx on dead_token_universe (chain, last_seen desc);
create index if not exists dead_universe_created_idx on dead_token_universe (first_seen);

create table if not exists whale_positions (
  id bigint generated always as identity primary key,
  token_address text not null,
  chain text not null,
  wallet text not null,
  first_buy_at timestamptz,
  last_buy_at timestamptz,
  buy_count int not null default 0,
  sell_count int not null default 0,
  net_position float8 not null default 0,
  total_buy float8 not null default 0,
  total_sell float8 not null default 0,
  buy_usd float8 not null default 0,
  sell_usd float8 not null default 0,
  hold_days float8 not null default 0,
  last_balance_raw text,
  last_balance_usd float8,
  wallet_balance_usd float8 not null default 0,
  is_contract boolean not null default false,
  is_scam boolean not null default false,
  status text not null default 'WATCH',  -- WATCH / CONFIRM / SIGNAL / DUMPED
  updated_at timestamptz not null default now(),
  unique (token_address, wallet)
);

create index if not exists whale_positions_token_idx on whale_positions (token_address, status);
create index if not exists whale_positions_wallet_idx on whale_positions (wallet);

-- RLS: dashboard baca dengan anon key; tulis service_role (bypass RLS).
alter table scans enable row level security;
alter table reject_log enable row level security;
alter table signals enable row level security;
alter table signal_tracks enable row level security;
alter table backtest_reports enable row level security;
alter table dead_token_universe enable row level security;
alter table whale_positions enable row level security;

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

drop policy if exists dead_universe_read on dead_token_universe;
create policy dead_universe_read on dead_token_universe for select using (true);

drop policy if exists whale_positions_read on whale_positions;
create policy whale_positions_read on whale_positions for select using (true);
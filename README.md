# Basescan — Multi-Chain Token Screener

Alpha signal finder: deteksi akumulasi smart money sebelum pump, dengan layer anti-rug
dan honest backtest. Data persisten di Supabase, collector berjalan via GitHub Actions cron.

## Arsitektur

```
GMGN / GoPlus / Gecko / DexScreener / Explorer(Etherscan|Basescan|...)
        │
        ▼
collector/  (pipeline Python)
  ├── scanners/      fetch data mentah
  ├── processors/    security gate → deepdive (survivor-only) → risk → scoring
  ├── storage/       Supabase + local JSONL (reject log permanen)
  └── main.py        CLI entry
        │
        ▼
Supabase (persisten)  ──►  api/ (FastAPI)  ──►  dashboard/ (Next.js, optional)
```

## Install lokal

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows; Linux/mac: source .venv/bin/activate
pip install -r collector/requirements.txt
cp .env.example .env           # isi API key
```

## Jalankan scan

```bash
python -m collector.main --chain base --mode accumulation --limit 50   # satu scan
python -m collector.main --chains base eth sol --mode dead_whale        # chain tertentu
python -m collector.main --limit 50                                     # semua chain & mode aktif
python -m collector.main --dry                                          # tanpa tulis
```

## Aturan scoring (tidak bisa ditawar)

- Cluster whale & accumulation phase: bobot 2x.
- Bonus accumulation HANYA jika buyer pattern nyata (gradual/cluster/single-entry) DAN
  price_change_1h < 20%. "Aman tapi datar" = skor tetap rendah.
- Honeypot = risk 100 = reject. Owner tidak renounce = +10. Sell tax >10% = +25.
- Wash-trading: volume GMGN vs Gecko ≥3x = +15; DexScreener avg trade <$50 & ≤10 tx/h = +25;
  same-second raw tx = +20/flag (max 3 flag).
- Liquidity floor: $5k (accumulation), $1k (dead-whale).
- API gagal = ERROR tercatat, bukan silent-fallback ke mock.

## GitHub Actions (cron tiap jam)

Set secrets di repo: `GMGN_API_KEY`, `ETHERSCAN_API_KEY`, `GOPLUS_API_KEY`,
`COINGECKO_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`.
Optional repo variable: `CHAIN_WEIGHT_ORDER`.

## Supabase — tabel yang dibutuhkan

```sql
create table scans (
  id bigint generated always as identity primary key,
  chain text, mode text, token_count int,
  scanned_at timestamptz, payload jsonb
);
create table reject_log (
  id bigint generated always as identity primary key,
  token_address text, chain text, mode text, reason text,
  rejected_at timestamptz
);
create table signals (
  token_address text primary key,
  chain text, verdict text, alpha float8, risk float8,
  signal_at timestamptz, status text, exit_price float8
);
```

## Disclaimer

Screener ini untuk riset, bukan saran finansial. Selalu do own research.

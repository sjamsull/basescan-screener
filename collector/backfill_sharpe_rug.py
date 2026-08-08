"""Backfill Sharpe rug-check untuk semua token universe (base/robinhood).

Isi dead_token_universe.sharpe_json (kolom baru — pastikan di-SQL dulu:
  alter table dead_token_universe add column if not exists sharpe_json text;)
dari Sharpe /v1/rug-check/security. Rate-limit free tier 30 RPM → spacing default
1.5s (~40/menit). Hanya memproses token yang belum punya report.

Jalan: python -m collector.backfill_sharpe_rug [--chain base] [--dry] [--sleep 1.5]
"""

import json
import logging
import sys
import time
from typing import List

from collector.scanners.sharpe import SharpeClient
from collector.storage.supabase import SupabaseStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_sharpe_rug")


def main() -> int:
    if not SupabaseStorage.configured():
        logger.error("Supabase tidak dikonfigurasi.")
        return 1
    args = sys.argv[1:]
    chain = args[args.index("--chain") + 1] if "--chain" in args else None
    dry = "--dry" in args
    sleep_s = float(args[args.index("--sleep") + 1]) if "--sleep" in args else 1.5

    store = SupabaseStorage()
    chains = [chain] if chain else ["base", "robinhood"]
    client = SharpeClient()
    if not client.api_key:
        logger.error("Set env SHARPE_API_KEY terlebih dahulu.")
        return 1

    for ch in chains:
        rows: List[dict] = []
        offset = 0
        while True:
            q = (store.client.table("dead_token_universe")
                 .select("token_address")
                 .eq("chain", ch).range(offset, offset + 999))
            resp = q.execute()
            batch = resp.data or []
            rows += batch
            if len(batch) < 100:
                break
            offset += 100
            if offset > 20000:
                break
        addrs = sorted({r["token_address"] for r in rows if r.get("token_address")})
        logger.info("%s: %d token universe", ch, len(addrs))

        # hanya yang belum punya report
        pending = []
        for a in addrs:
            chk = (store.client.table("dead_token_universe")
                   .select("sharpe_json").eq("token_address", a).execute())
            if not (chk.data and chk.data[0].get("sharpe_json")):
                pending.append(a)
        logger.info("%s: %d perlu di-scan", ch, len(pending))

        filled = 0
        missing = 0
        streak_429 = 0
        for a in pending:
            rep = client.rug_check(a, ch)
            time.sleep(sleep_s)
            if not rep:
                missing += 1
                streak_429 += 1
                # Kalau 429 beruntun (>=3), pause panjang supaya window RPM
                # benar-benar clear sebelum lanjut — jangan buoy terus.
                if streak_429 >= 3:
                    logger.warning("%s: %dx rate-limited berturut, istirahat 120s", ch, streak_429)
                    time.sleep(120)
                    streak_429 = 0
                continue
            streak_429 = 0
            payload = json.dumps(rep, ensure_ascii=False, default=str)
            if dry:
                logger.info("[dry] %s %s flags=%s", ch, a[:10],
                            [k for k, v in rep.items() if v is True][:6])
                continue
            try:
                store.client.table("dead_token_universe").update(
                    {"sharpe_json": payload}).eq("token_address", a).execute()
                filled += 1
            except Exception as exc:
                logger.error("update %s: %s", a[:10], exc)

        logger.info("done %s: filled=%d missing=%d dry=%s", ch, filled, missing, dry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Backfill VERROW risk report untuk token universe robinhood (chain 4663).

Isi dead_token_universe.verrow_json (kolom baru — pastikan di-SQL dulu:
  alter table dead_token_universe add column if not exists verrow_json text;)
dari VERROW /api/scan, untuk semua token robinhood yang belum punya report.
Rate limit per IP: spacing default 2s antar panggilan (sesuai dokumen).

Jalan: python -m collector.backfill_verrow [--chain robinhood] [--dry]
"""

import json
import logging
import sys
import time
from typing import List

from collector.scanners.verrow import VerrowClient
from collector.storage.supabase import SupabaseStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_verrow")


def main() -> int:
    if not SupabaseStorage.configured():
        logger.error("Supabase tidak dikonfigurasi.")
        return 1
    args = sys.argv[1:]
    chain = args[args.index("--chain") + 1] if "--chain" in args else "robinhood"
    dry = "--dry" in args
    sleep_s = float(args[args.index("--sleep") + 1]) if "--sleep" in args else 2.0

    store = SupabaseStorage()
    rows: List[dict] = []
    offset = 0
    while True:
        q = (store.client.table("dead_token_universe")
             .select("token_address")
             .eq("chain", chain).range(offset, offset + 999))
        resp = q.execute()
        batch = resp.data or []
        rows += batch
        if len(batch) < 100:
            break
        offset += 100
        if offset > 20000:
            break

    addrs = sorted({r["token_address"] for r in rows if r.get("token_address")})
    logger.info("%s: %d token universe", chain, len(addrs))

    # hanya yang belum punya report
    pending = []
    for a in addrs:
        chk = (store.client.table("dead_token_universe")
               .select("verrow_json").eq("token_address", a).execute())
        if not (chk.data and chk.data[0].get("verrow_json")):
            pending.append(a)
    logger.info("%s: %d perlu di-scan (sudah punya report = %d)", chain, len(pending), len(addrs) - len(pending))

    client = VerrowClient()
    filled = 0
    missing = 0
    for a in pending:
        rep = client.scan(a)
        time.sleep(sleep_s)
        if not rep:
            missing += 1
            continue
        payload = json.dumps(rep, ensure_ascii=False, default=str)
        if dry:
            logger.info("[dry] %s risk=%s level=%s", a[:10], rep.get("risk_score"), rep.get("risk_level"))
            continue
        try:
            store.client.table("dead_token_universe").update(
                {"verrow_json": payload}).eq("token_address", a).execute()
            filled += 1
        except Exception as exc:
            logger.error("update %s: %s", a[:10], exc)

    logger.info("done: filled=%d missing=%d dry=%s", filled, missing, dry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

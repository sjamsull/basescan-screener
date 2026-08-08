"""Backfill symbol untuk signals lama yang belum punya symbol.

Isi kolom signals.prepared_data.symbol (JSONB, tanpa DDL) dari GMGN /v1/token/info.
Jalan: python -m collector.backfill_symbols [--chain base] [--limit 200]
"""

import json
import logging
import sys
import time
from typing import List

from collector.config import CHAINS
from collector.storage.supabase import SupabaseStorage
from collector.scanners.gmgn import GMGNClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_symbols")


def main() -> int:
    if not SupabaseStorage.configured():
        logger.error("Supabase belum dikonfigurasi (SUPABASE_URL/SERVICE_KEY).")
        return 1

    chains = [c for c in sys.argv[sys.argv.index("--chain") + 1].split(",")] if "--chain" in sys.argv else list(CHAINS)
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 200

    store = SupabaseStorage()
    filled = 0
    for chain in chains:
        client = GMGNClient(chain)
        rows = []
        try:
            resp = (
                store.client.table("signals")
                .select("token_address,prepared_data")
                .eq("chain", chain)
                .order("signal_at", desc=True)
                .limit(limit)
                .execute()
            )
            rows = resp.data or []
        except Exception as exc:
            logger.error("query signals %s: %s", chain, exc)
            continue

        need = [
            r for r in rows
            if not ((r.get("prepared_data") or {}).get("symbol") or "").strip()
        ]
        logger.info("%s: %d sinyal, %d belum punya symbol", chain, len(rows), len(need))
        for r in need:
            addr = r.get("token_address")
            if not addr:
                continue
            try:
                info = client.get_token_info(addr)
                sym = (info.get("symbol") or "").strip()
            except Exception as exc:
                logger.warning("gmgn info %s %s: %s", chain, str(addr)[:10], exc)
                time.sleep(0.5)
                continue
            if sym:
                prepared = r.get("prepared_data") or {}
                prepared["symbol"] = sym[:24]
                try:
                    store.client.table("signals").update(
                        {"prepared_data": prepared}
                    ).eq("token_address", addr).execute()
                    filled += 1
                except Exception as exc:
                    logger.error("update %s: %s", addr[:10], exc)
            time.sleep(0.3)

    logger.info("done: %d symbol diisi", filled)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
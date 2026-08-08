"""Backfill market_cap GMGN ke dead_token_universe untuk semua token sinyal.

Dashboard MC "saat ini" kini bersumber dari dead_token_universe.market_cap (GMGN),
bukan signal_tracks DexScreener (yang beda-beda per pair). Script ini mengisi
GMGN market_cap (derivasi price*circulating_supply) untuk setiap token yang
pernah punya sinyal di base/robinhood — termasuk token yang belum ada di universe.
Jalan: python -m collector.backfill_gmgn_mcap [--chain base|robinhood] [--dry]
"""

import logging
import sys
import time
from typing import List, Optional

from collector.scanners.gmgn import GMGNClient
from collector.storage.supabase import SupabaseStorage
from collector.utils.helpers import to_float

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_gmgn_mcap")


def load_fresh_mcap(store: SupabaseStorage, addr: str, chain: str, client: GMGNClient) -> Optional[float]:
    try:
        info = client.get_token_info(addr)
    except Exception as exc:
        logger.warning("gmgn %s %s: %s", chain, addr[:10], exc)
        return None
    mc = to_float(info.get("market_cap"), 0.0)
    return mc if mc > 0 else None


def main() -> int:
    if not SupabaseStorage.configured():
        logger.error("Supabase tidak dikonfigurasi.")
        return 1
    args = sys.argv[1:]
    chain = args[args.index("--chain") + 1] if "--chain" in args else None
    dry = "--dry" in args
    chains = [chain] if chain else ["base", "robinhood"]

    store = SupabaseStorage()
    clients = {}
    updated = 0
    missing = 0

    for ch in chains:
        rows: List[dict] = []
        offset = 0
        while True:
            q = (store.client.table("signals")
                 .select("token_address,prepared_data")
                 .order("signal_at", desc=True).range(offset, offset + 999))
            if ch:
                q = q.eq("chain", ch)
            resp = q.execute()
            batch = resp.data or []
            rows += batch
            if len(batch) < 100:
                break
            offset += 100
            if offset > 30000:
                break
        # map address -> symbol (dari prepared_data)
        sym_of = {}
        for r in rows:
            a = r.get("token_address")
            if not a:
                continue
            pd = r.get("prepared_data") or {}
            s = (pd.get("symbol") or "").strip()
            if s and a not in sym_of:
                sym_of[a] = s
        addrs = sorted(set(sym_of) | {r["token_address"] for r in rows if r.get("token_address")})
        logger.info("%s: %d sinyal unik", ch, len(addrs))
        if ch not in clients:
            clients[ch] = GMGNClient(ch)
        client = clients[ch]

        for addr in addrs:
            mc = load_fresh_mcap(store, addr, ch, client)
            if not mc:
                missing += 1
                time.sleep(0.3)
                continue
            rec = {"token_address": addr, "chain": ch, "market_cap": round(mc)}
            if addr in sym_of:
                rec["symbol"] = sym_of[addr]
            if dry:
                logger.info("[dry] %s %s mcap=%s sym=%s", ch, addr[:10], round(mc), rec.get("symbol") or "-")
                time.sleep(0.2)
                continue
            try:
                store.client.table("dead_token_universe").upsert(
                    rec, on_conflict="token_address",
                ).execute()
                updated += 1
            except Exception as exc:
                logger.error("upsert %s %s: %s", ch, addr[:10], exc)
            time.sleep(0.2)

    logger.info("done: updated=%d missing(skip)=%d dry=%s", updated, missing, dry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Populate dead_token_universe — pilih token mati (umur >= DW_MIN_TOKEN_AGE_DAYS,
volume 24h rendah) dari signals yang sudah ada di Supabase.

Output: baris di dead_token_universe yang dipakai DeadWhaleScanner.load_universe.
"""

import logging
import time
from datetime import datetime, timezone

from collector import config
from collector.scanners.blockscout import BlockscoutClient
from collector.scanners.dexscreener import DexScreenerClient
from collector.storage.supabase import SupabaseStorage

logger = logging.getLogger(__name__)


class UniversePopulator:
    def __init__(self, chain: str = "base"):
        self.chain = chain
        self.bs = BlockscoutClient()
        self.dex = DexScreenerClient()
        self.store = SupabaseStorage() if SupabaseStorage.configured() else None
        if self.store is None:
            raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY diperlukan")

    def _seed_candidates(self, limit: int):
        """Kandidat dari signals (semua) — umur & 'dead' ditentukan lewat
        pairCreatedAt (DexScreener) + txns 24h, bukan signal_at."""
        rows = self.store.client.table("signals").select(
            "token_address,chain"
        ).eq("chain", self.chain).order("signal_at", desc=True).limit(limit).execute()
        return [{"token_address": r.get("token_address", "")} for r in (rows.data or [])]

    def run(self, limit: int = 50) -> int:
        now = datetime.now(timezone.utc)
        candidates = self._seed_candidates(limit)
        added = 0
        for c in candidates:
            addr = c.get("token_address", "")
            if not addr:
                continue
            try:
                info = self.bs.token_info(self.chain, addr) or {}
                pair = self.dex.get_pair(self.chain, addr) or {}
                pair_created = pair.get("pairCreatedAt") or 0
                ts = (pair_created / 1000.0) if pair_created else 0
                if ts:
                    age_days = (now.timestamp() - ts) / 86400.0
                else:
                    age_days = 9999.0
                txns24 = pair.get("txns") or {}
                txns_24 = int((txns24.get("h24") or {}).get("buys", 0) or 0) + int((txns24.get("h24") or {}).get("sells", 0) or 0)
                mcap = float(info.get("market_cap") or 0.0)
                vol_24h = float(info.get("volume_24h") or 0.0)
                is_dead = (age_days >= config.DW_MIN_TOKEN_AGE_DAYS) and (txns_24 <= config.DW_DEAD_TXNS_THRESHOLD)
                if not is_dead:
                    continue
                record = {
                    "token_address": addr,
                    "chain": self.chain,
                    "symbol": info.get("symbol"),
                    "decimals": int(info.get("decimals", 18) or 18),
                    "total_supply": info.get("total_supply"),
                    "market_cap": mcap,
                    "volume_24h": vol_24h,
                    "holders": int(info.get("holders", 0) or 0),
                    "last_seen": now.isoformat(),
                }
                if ts:
                    record["first_seen"] = datetime.fromtimestamp(ts, timezone.utc).isoformat()
                self.store.client.table("dead_token_universe").upsert(
                    {k: v for k, v in record.items() if k != "id"},
                    on_conflict="token_address",
                ).execute()
                added += 1
            except Exception as exc:
                logger.warning("populate %s: %s", addr, exc)
            time.sleep(0.6)
        logger.info("populate_universe %s: added=%d / candidates=%d", self.chain, added, len(candidates))
        return added


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="collector.populate_universe")
    ap.add_argument("--chain", default="base")
    ap.add_argument("--limit", type=int, default=100)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    pop = UniversePopulator(args.chain)
    n = pop.run(args.limit)
    print(f"[{args.chain}] universe tokens added/updated={n}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

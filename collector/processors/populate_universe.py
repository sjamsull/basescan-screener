"""Populate dead_token_universe — pilih token mati (umur >= DW_MIN_TOKEN_AGE_DAYS,
volume 24h rendah) dari signals yang sudah ada di Supabase.

Output: baris di dead_token_universe yang dipakai DeadWhaleScanner.load_universe.
"""

import logging
import os
from datetime import datetime, timezone

from collector import config
from collector.scanners.blockscout import BlockscoutClient
from collector.storage.supabase import SupabaseStorage

logger = logging.getLogger(__name__)


class UniversePopulator:
    def __init__(self, chain: str = "base"):
        self.chain = chain
        self.bs = BlockscoutClient()
        self.store = SupabaseStorage() if SupabaseStorage.configured() else None
        if self.store is None:
            raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY diperlukan")

    def _seed_candidates(self, limit: int):
        """Cari token mati dari Blockscout v2 /api/v2/tokens (list semua token)
        — filter volume 24h rendah + holders cukup. Lebih baik daripada seed
        hanya dari signals (yang biasanya trending)."""
        tokens = self.bs.list_tokens(self.chain, pages=config.DW_UNIVERSE_PAGES, page_size=50)
        out = []
        for t in tokens:
            addr = t.get("address", "")
            if not addr:
                continue
            vol = float(t.get("volume_24h") or 0.0)
            holders = int(t.get("holders") or 0)
            mcap = float(t.get("market_cap") or 0.0)
            er = float(t.get("exchange_rate") or 0.0)
            is_dead = (vol <= config.DW_DEAD_VOLUME_USD) and holders > 100 and mcap > 0 and er > 0
            if not is_dead:
                continue
            out.append({
                "token_address": addr,
                "symbol": t.get("symbol"),
                "decimals": int(t.get("decimals") or 18),
                "total_supply": str(t.get("total_supply") or 0),
                "market_cap": mcap,
                "volume_24h": vol,
                "holders": holders,
                "exchange_rate": er,
            })
        return out

    def _seed_from_signals(self, limit: int):
        """Fallback: token yang sudah ada di signals (Supabase)."""
        rows = self.store.client.table("signals").select("token_address,chain").eq("chain", self.chain).order("signal_at", desc=True).limit(limit).execute()
        return [{"token_address": r.get("token_address", "")} for r in (rows.data or [])]

    def run(self, limit: int = 50, use_dune: bool = True) -> int:
        now = datetime.now(timezone.utc)
        candidates = []
        if use_dune and os.getenv("DUNE_API_KEY") and os.getenv("DUNE_DEAD_TOKENS_QUERY_ID"):
            try:
                from collector.scanners.dune import run_query
                rows = run_query(int(os.getenv("DUNE_DEAD_TOKENS_QUERY_ID")), os.getenv("DUNE_API_KEY"))
                candidates += rows
            except Exception as exc:
                logger.warning("Dune universe fetch: %s", exc)
        # Blockscout /api/v2/tokens = sumber utama (sudah ada volume_24h, holders, exchange_rate)
        candidates += self._seed_candidates(limit)
        # fallback: signals (hanya bila list_tokens kosong)
        if not candidates:
            candidates += self._seed_from_signals(limit)

        seen = set()
        added = 0
        for c in candidates:
            addr = (c.get("token_address") or "").lower()
            if not addr or addr in seen:
                continue
            seen.add(addr)
            try:
                vol = float(c.get("volume_24h") or 0.0)
                holders = int(c.get("holders") or 0)
                mcap = float(c.get("market_cap") or 0.0)
                er = float(c.get("exchange_rate") or 0.0)
                # is_dead: volume rendah + holder cukup + token valid (exchange_rate>0 & mcap>0)
                if vol > config.DW_DEAD_VOLUME_USD or holders <= 100 or mcap <= 0 or er <= 0:
                    continue
                record = {
                    "token_address": addr,
                    "chain": self.chain,
                    "symbol": c.get("symbol"),
                    "decimals": int(c.get("decimals") or 18),
                    "total_supply": str(c.get("total_supply") or 0),
                    "market_cap": mcap,
                    "volume_24h": vol,
                    "holders": holders,
                    "last_seen": now.isoformat(),
                }
                self.store.client.table("dead_token_universe").upsert(
                    {k: v for k, v in record.items() if k != "id"},
                    on_conflict="token_address",
                ).execute()
                added += 1
            except Exception as exc:
                logger.warning("populate %s: %s", addr, exc)
        logger.info("populate_universe %s: added=%d / seed=%d", self.chain, added, len(candidates))
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

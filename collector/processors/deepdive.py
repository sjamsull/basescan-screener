"""Deep-dive enrichment — hanya dipanggil untuk token yang lolos Layer 1.

Biaya API mahal (Gecko + DexScreener + Explorer). Tidak dipakai untuk semua token.
"""

import logging
from typing import Dict, Optional

from collector.scanners.gecko import GeckoClient
from collector.scanners.dexscreener import DexScreenerClient
from collector.scanners.etherscan import ExplorerClient

logger = logging.getLogger(__name__)


class DeepDive:
    def __init__(self):
        self.gecko = GeckoClient()
        self.dex = DexScreenerClient()
        self.explorer_cache: Dict[str, ExplorerClient] = {}

    def _explorer(self, scan: str) -> ExplorerClient:
        if scan not in self.explorer_cache:
            self.explorer_cache[scan] = ExplorerClient(scan)
        return self.explorer_cache[scan]

    def enrich(self, token: Dict, chain_cfg, security: Dict) -> Dict:
        """Isi gecko_*, dexscreener_*, explorer_* ke dalam token. Tanpa throw."""
        out = dict(token)
        addr = token.get("address", "")

        gecko = self.gecko.price_volume(addr, chain_cfg.gecko_network)
        out["gecko"] = {k: v for k, v in gecko.items() if k != "error"}

        pair = self.dex.get_pair(chain_cfg.dexscreener_chain, addr)
        out["dexscreener"] = self.dex.extract(pair)

        if security and not security.get("is_honeypot"):
            explorer = self._explorer(chain_cfg.explorer_scan)
            buckets = explorer.same_second_buckets(addr, limit=40)
            out["same_second_flags"] = buckets

        # Fee/MC ratio — dari GMGN kalau ada, bukan tambahan API call
        mcap = token.get("market_cap") or (gecko.get("market_cap") or 0)
        fees = token.get("fees") or token.get("fee_24h") or 0
        if mcap:
            out["fee_mc_ratio"] = to_float_safe(fees) / to_float_safe(mcap)
        else:
            out["fee_mc_ratio"] = 0.0

        return out


def to_float_safe(v, default: float = 0.0) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return default
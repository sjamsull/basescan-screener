"""Deep-dive enrichment — hanya dipanggil untuk token yang lolos Layer 1.

Biaya API mahal (Gecko + GMGN + Explorer). Tidak dipakai untuk semua token.
"""

import logging
from typing import Dict, Optional

from collector.scanners.gecko import GeckoClient
from collector.scanners.gmgn import GMGNClient
from collector.scanners.etherscan import ExplorerClient

logger = logging.getLogger(__name__)


class DeepDive:
    def __init__(self):
        self.gecko = GeckoClient()

    def enrich(self, token: Dict, chain_cfg, security: Dict) -> Dict:
        """Isi gecko_*, gmgn_*, explorer_* ke dalam token. Tanpa throw."""
        out = dict(token)
        addr = token.get("address", "")
        gmgn = GMGNClient(chain_cfg.gmgn_id)

        gecko = self.gecko.price_volume(addr, chain_cfg.gecko_network)
        out["gecko"] = {k: v for k, v in gecko.items() if k != "error"}

        # Harga/likuiditas dari GMGN (satu-satunya sumber — bukan DexScreener).
        try:
            out["gmgn"] = gmgn.get_token_info(addr)
        except Exception as exc:
            logger.warning("gmgn deepdive %s: %s", addr[:10], exc)
            out["gmgn"] = {}

        if security and not security.get("is_honeypot") and chain_cfg.explorer_chainid:
            explorer = ExplorerClient(chain_cfg.explorer_chainid)
            buckets = explorer.same_second_buckets(addr, limit=40)
            out["same_second_flags"] = buckets
        else:
            out["same_second_flags"] = []
            out["explorer_skip"] = True if not chain_cfg.explorer_chainid else False

        # Fee/MC ratio — dari GMGN kalau ada, bukan tambahan API call
        mcap = token.get("market_cap") or (out.get("gmgn") or {}).get("market_cap") or (gecko.get("market_cap") or 0)
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
"""Deep-dive enrichment — hanya dipanggil untuk token yang lolos Layer 1.

Biaya API mahal (GMGN + Explorer). Tidak dipakai untuk semua token.
"""

import logging
from typing import Dict

from collector.scanners.gmgn import GMGNClient
from collector.scanners.etherscan import ExplorerClient

logger = logging.getLogger(__name__)


class DeepDive:
    def enrich(self, token: Dict, chain_cfg, security: Dict) -> Dict:
        """Isi gmgn_*, explorer_* ke dalam token (sumber data = GMGN). Tanpa throw."""
        out = dict(token)
        addr = token.get("address", "")
        gmgn = GMGNClient(chain_cfg.gmgn_id)

        # Harga/likuiditas/MC dari GMGN (satu-satunya sumber).
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

        # Fee/MC ratio — dari GMGN market_cap (bukan sumber lain).
        mcap = to_float_safe(token.get("market_cap")) or to_float_safe((out.get("gmgn") or {}).get("market_cap"))
        fees = token.get("fees") or token.get("fee_24h") or 0
        out["fee_mc_ratio"] = to_float_safe(fees) / mcap if mcap else 0.0

        return out


def to_float_safe(v, default: float = 0.0) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return default
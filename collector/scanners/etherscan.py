"""Etherscan-compatible explorer — raw transactions untuk same-second wash-trade detection.

Mendukung etherscan/basescan/arbiscan/bscscan. API key via ETHERSCAN_API_KEY.
"""

import os
import logging
from typing import Dict, List

from collector.utils.api import get_json
from collector.utils.helpers import to_float

logger = logging.getLogger(__name__)


class ExplorerClient:
    def __init__(self, scan: str = "etherscan"):
        self.api_key = os.getenv("ETHERSCAN_API_KEY", "")
        self.scan = scan
        self.base = {
            "etherscan": "https://api.etherscan.io/api",
            "basescan": "https://api.basescan.org/api",
            "arbiscan": "https://api.arbiscan.io/api",
            "bscscan": "https://api.bscscan.com/api",
        }[scan]

    def _params(self, **kw) -> dict:
        return {"apikey": self.api_key, **kw}

    def raw_tx(self, address: str, limit: int = 40) -> List[Dict]:
        """ERC-20 transfers terbaru. Dipakai untuk flag same-second."""
        params = self._params(
            module="account",
            action="tokentx",
            address=address,
            sort="desc",
            offset=limit,
        )
        try:
            data = get_json(self.base, params=params, timeout=20, retries=1)
        except Exception as exc:
            logger.warning("Explorer %s raw_tx: %s", self.scan, exc)
            return []
        if data.get("status") != "1":
            return []
        result = data.get("result", [])
        if not isinstance(result, list):
            logger.warning("Explorer %s non-list result: %s", self.scan, result)
            return []
        return [r for r in result if isinstance(r, dict)]

    def same_second_buckets(self, address: str, limit: int = 40) -> List[Dict]:
        """Kelompokkan tx dengan timestamp identik. Bot/wash signature = banyak tx 1 detik.

        Kembalikan bucket dengan count >= 3 sebagai flag.
        """
        txs = self.raw_tx(address, limit=limit)
        buckets: Dict[str, Dict] = {}
        for tx in txs:
            ts = str(tx.get("timeStamp", ""))
            if not ts:
                continue
            raw_value = int(tx.get("value", "0") or 0)
            decimals = to_float(tx.get("tokenDecimal"), 18)
            decimals = int(decimals) if decimals > 0 else 18
            amount = raw_value / (10 ** decimals)
            entry = buckets.setdefault(ts, {"count": 0, "total_tokens": 0.0})
            entry["count"] += 1
            entry["total_tokens"] += amount

        return [{"count": e["count"], "total_tokens": e["total_tokens"], "timestamp": ts}
                for ts, e in buckets.items() if e["count"] >= 3]
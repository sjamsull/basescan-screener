"""Etherscan V2 terpadu — satu endpoint untuk semua chain, dengan chainid.

Free tier (per 2026): chainid=1 (ETH), 42161 (Arbitrum) terbuka.
chainid=8453 (Base), 56 (BSC) ditolak free tier -> ExplorerClient tidak diinstansiasi
untuk chain itu di pipeline (skip + catat), bukan spam error.
"""

import os
import logging
from typing import Dict, List

from collector.utils.api import get_json
from collector.utils.helpers import to_float

logger = logging.getLogger(__name__)

V2_URL = "https://api.etherscan.io/v2/api"


class ExplorerClient:
    def __init__(self, chainid: int):
        self.api_key = os.getenv("ETHERSCAN_API_KEY", "")
        self.chainid = chainid

    def _params(self, **kw) -> dict:
        return {"chainid": self.chainid, "apikey": self.api_key, **kw}

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
            data = get_json(V2_URL, params=params, timeout=20, retries=1)
        except Exception as exc:
            logger.warning("Explorer v2 chainid=%s raw_tx: %s", self.chainid, exc)
            return []
        if data.get("status") != "1":
            logger.warning("Explorer v2 chainid=%s non-ok: %s", self.chainid, str(data.get("result"))[:100])
            return []
        result = data.get("result", [])
        if not isinstance(result, list):
            return []
        return [r for r in result if isinstance(r, dict)]

    def same_second_buckets(self, address: str, limit: int = 40) -> List[Dict]:
        """Kelompokkan tx dengan timestamp identik. Bot/wash signature = banyak tx 1 detik."""
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
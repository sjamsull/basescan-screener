"""GMGN — feed utama via OpenAPI (openapi.gmgn.ai).

Endpoint aktual (2026): GET /v1/market/rank
  response: {code, data: {data: {rank: [...]}}}
  auth query: timestamp (unix s) + client_id (fresh uuid4)
  header: X-APIKEY

Mode:
  accumulation -> order_by=smart_degen_count desc (smart money sedang datangkan)
  dead_whale   -> order_by=swaps            desc + interval 24h (gas-old whales)

Mapping: menyamakan nama field GMGN -> field kanonik pipeline.
"""

import os
import time
import uuid
import logging
from typing import List, Dict, Optional

from collector.utils.api import get_json, APIError
from collector.utils.validators import valid_address

logger = logging.getLogger(__name__)

CANON = {
    "address": "address",
    "name": "name",
    "symbol": "symbol",
    "price": "price",
    "liquidity": "liquidity",
    "market_cap": "market_cap",
    "volume": "gmgn_volume_interval",
    "holder_count": "holder_count",
    "top_10_holder_rate": "top_10_holder_rate",
    "price_change_percent": "price_change_interval",
    "price_change_percent1m": "price_change_1m",
    "price_change_percent5m": "price_change_5m",
    "price_change_percent1h": "price_change_1h",
    "price_change_percent6h": "price_change_6h",
    "price_change_percent24h": "price_change_24h",
    "smart_degen_count": "smart_degen_count",
    "swaps": "swaps",
    "buys": "buys",
    "sells": "sells",
    "open_timestamp": "open_timestamp",
    "creation_timestamp": "creation_timestamp",
    "buy_tax": "buy_tax",
    "sell_tax": "sell_tax",
    "is_honeypot": "honeypot",
    "is_renounced": "renounced",
    "is_open_source": "is_open_source",
    "is_wash_trading": "is_wash_trading",
    "top10_holder_rate": "top_10_holder_rate",  # alias kolom tua
    "liquid": "liquid",
    "security_audit": "security_audit",
}


class GMGNClient:
    def __init__(self, chain: str = "base"):
        self.api_key = os.getenv("GMGN_API_KEY", "")
        self.chain = chain
        self.base_url = "https://openapi.gmgn.ai"

    def _headers(self) -> dict:
        return {
            "X-APIKEY": self.api_key,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        }

    def _auth(self) -> dict:
        return {
            "timestamp": str(int(time.time())),
            "client_id": str(uuid.uuid4()),
        }

    def get_trending(self, mode: str = "accumulation", limit: int = 50) -> List[Dict]:
        if mode == "accumulation":
            order_by, direction = "smart_degen_count", "desc"
        elif mode == "dead_whale":
            order_by, direction = "swaps", "desc"
        else:
            order_by, direction = "swaps", "desc"

        params = {
            **self._auth(),
            "chain": self.chain,
            "interval": "1h" if mode == "accumulation" else "24h",
            "limit": limit,
            "order_by": order_by,
            "direction": direction,
        }

        try:
            data = get_json(f"{self.base_url}/v1/market/rank", headers=self._headers(), params=params)
        except APIError as exc:
            logger.error("GMGN trending %s/%s: %s", self.chain, mode, exc)
            raise

        rank = ((data.get("data") or {}).get("data") or {}).get("rank") or (data.get("data") or {}).get("list") or []
        return [self._canonical(r) for r in rank]

    def _canonical(self, raw: Dict) -> Dict:
        out = dict(raw)
        out.update({
            "honeypot": int(raw.get("is_honeypot", 0) or 0),
            "renounced": int(raw.get("is_renounced", 0) or 0),
            "open_source": int(raw.get("is_open_source", 0) or 0),
        })
        return out
"""CoinGecko — verifikasi harga/volume on-chain, bandingkan dengan GMGN (deteksi wash)."""

import os
import logging
from typing import Dict, Optional

from collector.utils.api import get_json, APIError

logger = logging.getLogger(__name__)


class GeckoClient:
    def __init__(self):
        self.api_key = os.getenv("COINGECKO_API_KEY", "")
        self.base_url = "https://api.coingecko.com/api/v3"

    def _headers(self) -> dict:
        return {"x_cg_demo_api_key": self.api_key} if self.api_key else {}

    def price_volume(self, address: str, network: str) -> Dict:
        """Likuiditas & volume independen utk cross-check GMGN."""
        url = f"{self.base_url}/coins/{network}/contract/{address}"
        try:
            data = get_json(url, headers=self._headers(), timeout=20, retries=1)
        except APIError as exc:
            logger.warning("Gecko %s: %s", address, exc)
            return {"error": str(exc)}

        market = data.get("market_data", {})
        return {
            "market_cap": (market.get("market_cap") or {}).get("usd"),
            "total_volume": (market.get("total_volume") or {}).get("usd"),
            "circulating_supply": market.get("circulating_supply"),
            "last_price": (market.get("current_price") or {}).get("usd"),
            "fully_diluted_valuation": (market.get("fully_diluted_valuation") or {}).get("usd"),
            "error": None,
        }
"""DexScreener — harga, likuiditas, volume & tx count untuk wash-trade filter."""

import logging
from typing import Dict, Optional

from collector.utils.api import get_json
from collector.utils.helpers import to_float

logger = logging.getLogger(__name__)


class DexScreenerClient:
    BASE_URL = "https://api.dexscreener.com/latest/dex"

    def get_pair(self, chain: str, address: str) -> Optional[Dict]:
        url = f"{self.BASE_URL}/tokens/{address}"
        try:
            data = get_json(url, timeout=20)
        except Exception as exc:
            logger.warning("DexScreener %s: %s", address, exc)
            return None

        pairs = data.get("pairs") or []
        if not pairs:
            return None
        for p in pairs:
            if p.get("chainId") == chain:
                return p
        return pairs[0]

    @staticmethod
    def extract(pair: Optional[Dict]) -> Dict:
        if not pair:
            return {
                "error": "no pair",
                "price_usd": None,
                "liquidity_usd": 0.0,
                "volume_24h": 0.0,
                "txns_24h": 0,
                "avg_trade_usd": 0.0,
            }

        liquidity_raw = pair.get("liquidity")
        liquidity_usd = (
            to_float(liquidity_raw.get("usd"), 0.0)
            if isinstance(liquidity_raw, dict)
            else to_float(liquidity_raw, 0.0)
        )

        txns = (pair.get("txns") or {}).get("h24", {}) or {}
        buys = int(txns.get("buys", 0) or 0)
        sells = int(txns.get("sells", 0) or 0)
        txns_24h = buys + sells

        volume_raw = (pair.get("volume") or {}).get("h24", 0.0)
        volume_24h = to_float(volume_raw, 0.0)
        avg_trade_usd = volume_24h / txns_24h if txns_24h > 0 else 0.0

        return {
            "error": None,
            "price_usd": to_float(pair.get("priceUsd"), 0.0),
            "liquidity_usd": liquidity_usd,
            "volume_24h": volume_24h,
            "txns_24h": txns_24h,
            "avg_trade_usd": avg_trade_usd,
        }
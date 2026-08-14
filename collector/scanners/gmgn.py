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
from collector.utils.helpers import to_float

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
    def __init__(self, chain: str = "base", api_key: Optional[str] = None):
        self.chain = chain
        # Pakai key spesial chain jika diberikan, lalu fallback ke env, lalu ke global config
        self.api_key = api_key or os.getenv(f"GMGN_API_KEY_{chain.upper()}", "") or os.getenv("GMGN_API_KEY", "")
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

    def token_security(self, address: str) -> Dict:
        """GET /v1/token/security — top10 holder, honeypot, rug, alert flags."""
        time.sleep(0.5)  # throttle supaya tidak kena rate-limit GMGN (30 RPM free tier)
        auth = self._auth()
        params = {**auth, "chain": self.chain, "address": address}
        data = get_json(f"{self.base_url}/v1/token/security", headers=self._headers(), params=params)
        d = data.get("data") or {}
        return {
            "address": address,
            "top_10_holder_rate": to_float(d.get("top_10_holder_rate"), 0.0),
            "rug_ratio": to_float(d.get("rug_ratio"), 0.0),
            "is_honeypot": bool(d.get("is_honeypot") or False),
            "is_blacklist": d.get("is_blacklist"),
            "is_show_alert": bool(d.get("is_show_alert") or False),
            "owner_renounced": bool(d.get("is_renounced") or False),
            "open_source": bool(d.get("is_open_source") or False),
            "buy_tax": to_float(d.get("buy_tax"), 0.0),
            "sell_tax": to_float(d.get("sell_tax"), 0.0),
            "burnt": d.get("burn_status"),
            "flags": d.get("flags") or [],
        }

    def get_token_info(self, address: str) -> Dict:
        """GET /v1/token/info — metadata + holder + liquidity + mcap + volume/price.

        GMGN adalah satu-satunya sumber MC/price/volume untuk pipeline (bukan
        DexScreener yang per-pair meleset). market_cap diderivasi dari
        price*circulating_supply bila GMGN tidak mengirimnya eksplisit.
        """
        param = self._auth()
        params = {**param, "chain": self.chain, "address": address}
        data = get_json(f"{self.base_url}/v1/token/info", headers=self._headers(), params=params)
        d = data.get("data") or {}
        price = d.get("price") or {}
        pv = to_float(price.get("price") if isinstance(price, dict) else price, 0.0)
        cs = to_float(d.get("circulating_supply"), 0.0)
        mcap = to_float(d.get("market_cap"), 0.0)
        if mcap <= 0:
            mcap = pv * cs
        buys = int(price.get("buys_24h") if isinstance(price, dict) else 0)
        sells = int(price.get("sells_24h") if isinstance(price, dict) else 0)
        swaps = buys + sells
        # Harga historis untuk hitung momentum (price change proxy)
        p1h = to_float(price.get("price_1h") if isinstance(price, dict) else 0, 0.0)
        p6h = to_float(price.get("price_6h") if isinstance(price, dict) else 0, 0.0)
        p24h = to_float(price.get("price_24h") if isinstance(price, dict) else 0, 0.0)
        return {
            "name": d.get("name"),
            "symbol": d.get("symbol"),
            "decimals": d.get("decimals", 18),
            "holder_count": int(d.get("holder_count") or 0),
            "liquidity": to_float(d.get("liquidity"), 0.0),
            "market_cap": mcap,
            "price_usd": pv,
            "price_1h": p1h,
            "price_6h": p6h,
            "price_24h": p24h,
            "volume_24h": to_float(price.get("volume_24h") if isinstance(price, dict) else 0, 0.0),
            "buys": buys,
            "sells": sells,
            "swaps": swaps,
            "circulating_supply": cs,
            "creation_timestamp": d.get("creation_timestamp"),
        }

    def _canonical(self, raw: Dict) -> Dict:
        out = dict(raw)
        out.update({
            "honeypot": int(raw.get("is_honeypot", 0) or 0),
            "renounced": int(raw.get("is_renounced", 0) or 0),
            "open_source": int(raw.get("is_open_source", 0) or 0),
        })

        # ==== Kernel akumulasi: derivasi dari data GMGN yang ada ====
        smart = int(raw.get("smart_degen_count", 0) or 0)
        renowned = int(raw.get("renowned_count", 0) or 0)
        buys = int(raw.get("buys", 0) or 0)
        sells = int(raw.get("sells", 0) or 0)
        swaps = int(raw.get("swaps", 0) or 0)
        price_1h = to_float(raw.get("price_change_percent1h"), raw.get("price_change_percent", 0.0))

        # whale cluster -> smart_degen + renowned = smart wallets aktif
        out["big_holder_count"] = smart if buys > 0 else 0
        out["whale_count"] = smart

        # buyer pattern: net flow riil dari tx GMGN
        if buys > 0 and sells == 0:
            out["buyer_pattern"] = "single_entry"
        elif buys > sells * 1.3:
            out["buyer_pattern"] = "cluster"
        elif buys > sells:
            out["buyer_pattern"] = "gradual"
        elif swaps > 0:
            out["buyer_pattern"] = "distributing"
        else:
            out["buyer_pattern"] = ""

        # holder trend: akumulasi = smart wallet masuk + net-buy + harga belum meledak
        if smart > 0 and buys >= sells and price_1h < 5.0:
            out["holder_count_trend"] = "accumulation"
        elif smart > 0:
            out["holder_count_trend"] = "accumulating_watch"
        else:
            out["holder_count_trend"] = "neutral"

        out["price_change_1h"] = price_1h

        # ==== Tren harga (PENTING: scorer membaca kline_trend_pct/price_change_24h
        # untuk momentum — sebelumnya TIDAK pernah diisi sehingga momentum macet
        # di 50 & fitur kline_trend mati). GMGN rank mengembalikan % change per
        # interval (1h untuk accumulation, 24h untuk dead_whale) di
        # price_change_percent, plus price_change_percent1h.
        out["kline_trend_pct"] = to_float(
            raw.get("price_change_percent1h"),
            to_float(raw.get("price_change_percent"), 0.0),
        )
        out["price_change_24h"] = to_float(
            raw.get("price_change_percent24h"),
            to_float(raw.get("price_change_percent"), 0.0),
        )
        return out
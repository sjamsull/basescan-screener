"""Layer 1 anti-rug — filter murah SEBELUM enrich (hemat rate-limit)."""

import logging
from typing import Dict, List, Optional

from collector import config
from collector.utils.helpers import to_float, age_hours_from

logger = logging.getLogger(__name__)


class SecurityGate:
    """Filter biaya murah (age, liq, top10, tax) untuk menolak sampah sebelum API mahal."""

    def __init__(self, mode: str = "accumulation"):
        self.mode = mode
        if mode == "dead_whale":
            self.min_age_days = 4.0
            self.min_liq = config.MIN_LIQUIDITY_DEADWHALE
            self.max_top10 = config.DEAD_MAX_TOP10_PCT
        else:
            self.min_age_days = config.MIN_AGE_DAYS
            self.min_liq = config.MIN_LIQUIDITY_ACCUMULATION
            self.max_top10 = config.MAX_TOP10_PCT

    def check(self, token: Dict, pair: Optional[Dict]) -> Dict:
        """Return (passed: bool, reason: str, cheap_payload)."""
        reasons: List[str] = []

        # 1. Alamat valid
        addr = token.get("address", "")
        if not addr:
            return self._reject("no_address")

        # 2. Umur
        created = token.get("creation_time") or token.get("creationTimestamp")
        age_hours = age_hours_from(created)
        if age_hours > 0 and age_hours < self.min_age_days * 24:
            reasons.append(f"age<{self.min_age_days}d")

        # 3. Likuiditas (dari DexScreener kalau ada, fallback GMGN liquidity)
        liq = 0.0
        if pair:
            liq = to_float(pair.get("liquidity_usd"), 0.0)
        liq = liq or to_float(token.get("liquidity"), to_float(token.get("liquidity_usd"), 0.0))
        if liq < self.min_liq:
            reasons.append(f"liq<${self.min_liq}")

        # 4. Top10 holder (GMGN)
        top10 = to_float(token.get("top_10_holder_rate"), to_float(token.get("top10_holder_rate"), 0.0)) * 100
        if 0 < top10 > self.max_top10:
            reasons.append(f"top10>{self.max_top10:.0f}%")

        # 5. Tax (kalau GMGN bawa)
        buy_tax = to_float(token.get("buy_tax"), 0.0)
        sell_tax = to_float(token.get("sell_tax"), 0.0)
        if max(buy_tax, sell_tax) > config.MAX_TAX_PCT:
            reasons.append(f"tax>{config.MAX_TAX_PCT}%")

        # 6. Honeypot flag yang sudah tersedia dari GMGN (kalau ada)
        if str(token.get("honeypot", "0")) == "1":
            reasons.append("honeypot")

        if reasons:
            return self._reject(";".join(reasons))

        return {
            "passed": True,
            "reason": None,
            "liquidity": liq,
            "top10_pct": top10,
            "age_hours": age_hours,
        }

    @staticmethod
    def _reject(reason: str) -> Dict:
        return {"passed": False, "reason": reason, "liquidity": 0.0, "top10_pct": 0.0, "age_hours": 0.0}
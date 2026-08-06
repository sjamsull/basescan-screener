"""Risk assessment — penalti wash-trading, likuiditas mismatch, konsentrasi holder."""

import logging
from typing import Dict

from collector import config
from collector.utils.helpers import to_float

logger = logging.getLogger(__name__)


class RiskEngine:
    """Risk 0-100. 100 = reject (honeypot). Komponen:

    - Honeypot                        -> 100
    - Owner tidak renounce            -> +10
    - Sell tax >10% / >5%             -> +25 / +10
    - Top10 >80% / >65%               -> +20 / +10
    - Liq <$50k / <$100k              -> +20 / +10
    - Volume GMGN vs Gecko mismatch   -> +15 (ratio >= 3x, tanpa Gecko data skip)
    - DexScreener avg trade <$50 & <=10 tx/h -> +25
    - Same-second raw-tx flag         -> +20 per flag (max +60)
    """

    def calculate(self, token: Dict, security: Dict) -> Dict:
        risk = 0.0
        flags: list[str] = []

        if security.get("is_honeypot"):
            return {"score": 100.0, "flags": ["honeypot"]}

        if not security.get("owner_renounced", True):
            risk += 10
            flags.append("owner_not_renounced")

        sell_tax = to_float(security.get("sell_tax"), 0.0)
        if sell_tax > 10:
            risk += 25
            flags.append(f"sell_tax>{sell_tax:.1f}%")
        elif sell_tax > 5:
            risk += 10
            flags.append(f"sell_tax>{sell_tax:.1f}%")

        top10 = to_float(token.get("top_10_holder_rate"), to_float(token.get("top10_holder_rate"), 0.0)) * 100
        if top10 > 80:
            risk += 20
            flags.append("top10>80%")
        elif top10 > 65:
            risk += 10
            flags.append("top10>65%")

        liq = to_float(token.get("liquidity"), to_float(token.get("liquidity_usd"), 0.0))
        if 0 < liq < 50000:
            risk += 20
            flags.append("liq<50k")
        elif liq < 100000:
            risk += 10
            flags.append("liq<100k")

        risk += self._wash_trade_penalty(token, flags)

        return {"score": round(min(100.0, risk), 1), "flags": flags}

    def _wash_trade_penalty(self, token: Dict, flags: list[str]) -> float:
        penalty = 0.0
        gecko = token.get("gecko") or {}
        dex = token.get("dexscreener") or {}

        # 1. Volume GMGN vs on-chain Gecko >= 3x
        gmgn_vol = to_float(token.get("volume_24h"), to_float(token.get("volume"), 0.0))
        gecko_vol = to_float(gecko.get("total_volume"), 0.0)
        if gmgn_vol > 0 and gecko_vol > 0:
            ratio = gmgn_vol / gecko_vol
            if ratio >= config.WASH_VOLUME_RATIO:
                penalty += 15
                flags.append(f"vol_ratio_gmgn_gecko={ratio:.1f}x")

        # 2. DexScreener avg trade < $50 & tx <= 10/jam
        avg_trade = to_float(dex.get("avg_trade_usd"), 0.0)
        tx_hour = to_float(dex.get("txns_24h"), 0.0) / 24.0
        if dex.get("error") is None and 0 < avg_trade < config.DEXS_MIN_AVG_TRADE and tx_hour <= config.DEXS_MAX_TX_PER_HOUR:
            penalty += 25
            flags.append(f"dex_avg_trade=${avg_trade:.0f}")

        # 3. Same-second raw-tx, +20 per flag max 3
        same_second = token.get("same_second_flags") or []
        penalty += min(60, 20 * len(same_second))
        if same_second:
            flags.append(f"same_second_x{len(same_second)}")

        return penalty
"""Scoring — weighted confluence. Whale cluster & accumulation berbobot 2x.

Aturan sakral:
- Bonus ACCUMULATION HANYA jika ada pembeli nyata (gradual/cluster/single-entry)
  DAN price_change_1h < 20%. Harga naik tanpa volume riil = TANPA bonus.
- Jangan kasih skor tinggi karena "aman tapi datar".
"""

import logging
from typing import Dict

from collector.utils.helpers import to_float, clamp

logger = logging.getLogger(__name__)

BUYER_PATTERNS = {"gradual", "cluster", "single_entry", "gradual_cluster", "cluster_single"}


class TokenScorer:
    WEIGHTS = {
        "cluster_whale": 2,
        "accumulation_phase": 2,
        "kline_trend": 1,
        "liquidity_health": 1,
        "social_mention": 1,
    }

    def calculate_alpha(self, token: Dict) -> Dict:
        """Return {alpha, breakdown, momentum, momentum_detail}."""
        momentum, mom_detail = self.calculate_momentum(token)
        score = 30.0
        breakdown: Dict[str, float] = {}
        w = self.WEIGHTS

        # 1. Cluster whale (bobot 2x) — big_holder_count atau whale clusters
        whale_bonus = 0.0
        big_holders = to_float(token.get("big_holder_count"), to_float(token.get("whale_count"), 0.0))
        if big_holders > 0:
            whale_bonus = min(20.0, 4.0 * big_holders) * w["cluster_whale"]
        score += whale_bonus
        breakdown["cluster_whale"] = whale_bonus

        # 2. Accumulation phase (bobot 2x) — HARUS ada buyer real + harga belum meledak
        acc_bonus = 0.0
        holder_trend = str(token.get("holder_count_trend", ""))
        buyer_pattern = str(token.get("buyer_pattern", "")).lower()
        price_1h = to_float(token.get("price_change_1h"), to_float(token.get("price_change_1h_percent"), 0.0))
        acc_eligible = holder_trend == "accumulation" and price_1h < 20.0

        if acc_eligible and buyer_pattern in BUYER_PATTERNS:
            acc_bonus = 20.0 * w["accumulation_phase"]
            breakdown["accumulation_phase"] = acc_bonus
            score += acc_bonus
        elif acc_eligible and buyer_pattern not in BUYER_PATTERNS:
            # Ada tren akumulasi tapi tanpa konfirmasi pembeli nyata — bonus kecil
            acc_bonus = 5.0
            breakdown["accumulation_phase"] = acc_bonus
            score += acc_bonus
        else:
            breakdown["accumulation_phase"] = 0.0

        # 3. Kline trend (bobot 1) — kontribusi relatif momentum
        kline_score = (momentum - 50.0) * 0.4
        score += kline_score
        breakdown["kline_trend"] = round(kline_score, 1)

        # 4. Liquidity health (bobot 1)
        liq = to_float(token.get("liquidity"), to_float(token.get("liquidity_usd"), 0.0))
        liq_score = 0.0
        if liq >= 200000:
            liq_score = 10.0
        elif liq >= 100000:
            liq_score = 7.0
        elif liq >= 50000:
            liq_score = 4.0
        elif liq > 0:
            liq_score = 2.0
        breakdown["liquidity_health"] = liq_score
        score += liq_score

        # 5. Social mention (bobot 1) — kalau ada
        social = to_float(token.get("social_mention_count"), 0.0)
        social_score = min(10.0, social * 2.0)
        breakdown["social_mention"] = social_score
        score += social_score

        return {
            "alpha": round(clamp(score), 1),
            "breakdown": breakdown,
            "momentum": round(momentum, 1),
            "momentum_detail": mom_detail,
        }

    def calculate_momentum(self, token: Dict) -> tuple[float, Dict]:
        score = 50.0
        kline = to_float(token.get("kline_trend_pct"), to_float(token.get("price_change_24h"), 0.0))
        vol_ratio = to_float(token.get("kline_vol_ratio"), 1.0)
        price_1h = to_float(token.get("price_change_1h"), 0.0)

        detail = {"kline_24h": kline, "vol_ratio": vol_ratio, "price_1h": price_1h}

        if 5 <= kline <= 120:
            score += 20
            detail["trend_band"] = "healthy"
        elif kline > 120:
            score -= 15
            detail["trend_band"] = "overheated"
        elif kline < -20:
            score -= 10
            detail["trend_band"] = "bleeding"
        else:
            detail["trend_band"] = "flat"

        if vol_ratio > 1.5:
            score += 8
            detail["volume_confirm"] = True

        # Harga naik terlalu cepat tanpa volume = warning
        if price_1h >= 20:
            score -= 10
            detail["pump_warning"] = True

        return clamp(score), detail

    def get_verdict(self, alpha: float, risk: float) -> str:
        composite = alpha - (risk * 0.5)
        if composite >= 80 and risk < 20:
            return "STRONG BUY"
        if composite >= 60:
            return "BUY"
        if composite >= 40:
            return "NEUTRAL"
        return "CAUTION"
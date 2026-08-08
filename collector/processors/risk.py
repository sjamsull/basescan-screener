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
    - GMGN avg trade <$50 & <=10 swap/jam -> +25
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
        risk += self._launchpad_structure_penalty(token, flags)

        return {"score": round(min(100.0, risk), 1), "flags": flags}

    def _launchpad_structure_penalty(self, token: Dict, flags: list[str]) -> float:
        """Risk struktur on-chain dari GMGN: bundler, trap ratio, dev-hold, sniper, creator.

        Kalibrasi berbasis distribusi nyata feed GMGN (Aug 2026):
        - Base: bundler_rate selalu 0 -> hanya aktif di Solana.
        - entrapment_ratio median 0.84 di Base -> hanya ekor atas yang menandakan jebakan.
        - creator_close 87% dari feed = normal trench -> +5, bukan pembunuh.
        """
        penalty = 0.0

        # 1. Bundler rate (0-1): hanya terisi di Solana. >=0.35 mulai berbahaya.
        bundler = to_float(token.get("bundler_rate"), 0.0)
        if bundler >= 0.65:
            penalty += 40
            flags.append(f"bundler={bundler:.0%}")
        elif bundler >= 0.5:
            penalty += 28
            flags.append(f"bundler={bundler:.0%}")
        elif bundler >= 0.35:
            penalty += 16
            flags.append(f"bundler={bundler:.0%}")

        # 2. Entrapment ratio: ekor atas saja (median feed ~0.84 di EVM).
        entrapment = to_float(token.get("entrapment_ratio"), 0.0)
        if entrapment >= 0.97:
            penalty += 28
            flags.append(f"entrapment={entrapment:.0%}")
        elif entrapment >= 0.92:
            penalty += 18
            flags.append(f"entrapment={entrapment:.0%}")
        elif entrapment >= 0.87:
            penalty += 10
            flags.append(f"entrapment={entrapment:.0%}")

        # 3. Dev team hold rate: dev masih pegang besar = siap dump.
        dev = token.get("dev_team_hold_rate")
        if dev is not None and dev >= 0.25:
            penalty += 20
            flags.append(f"dev_hold={dev:.0%}")

        # 4. Top-70 sniper hold: sniper pegang besar = menunggu dump ke run price.
        sniper = token.get("top70_insider_hold_rate")
        if sniper is None:
            sniper = token.get("top70_sniper_hold_rate")
        if sniper is not None and sniper >= 0.3:
            penalty += 15
            flags.append(f"sniper_hold={sniper:.0%}")

        # 5. Rug ratio: extreme tail.
        rug = to_float(token.get("rug_ratio"), 0.0)
        if rug >= 0.75:
            penalty += 30
            flags.append(f"rug_ratio={rug:.0%}")

        # 6. Creator sudah keluar: normal trench, flag ringan saja.
        if token.get("creator_close"):
            penalty += 5
            flags.append("creator_close")

        # 7. Creator sudah pindahtransfer besar (rug drive kuat tanpa score besar).
        creator_sell = to_float(token.get("creator_sell", 0), 0.0)
        if creator_sell >= 0.5:
            penalty += 15
            flags.append("creator_sold_large")

        return penalty

    def _wash_trade_penalty(self, token: Dict, flags: list[str]) -> float:
        penalty = 0.0
        gecko = token.get("gecko") or {}
        gmgn = token.get("gmgn") or {}

        # 1. Volume GMGN vs on-chain Gecko >= 3x
        gmgn_vol = to_float(token.get("volume_24h"), to_float(token.get("volume"), 0.0)) or to_float(gmgn.get("volume_24h"), 0.0)
        gecko_vol = to_float(gecko.get("total_volume"), 0.0)
        if gmgn_vol > 0 and gecko_vol > 0:
            ratio = gmgn_vol / gecko_vol
            if ratio >= config.WASH_VOLUME_RATIO:
                penalty += 15
                flags.append(f"vol_ratio_gmgn_gecko={ratio:.1f}x")

        # 2. Wash-trade: GMGN avg trade < $50 & <=10 swap/jam (sumber GMGN,
        #    bukan DexScreener per-pair). avg_trade = volume / jumlah swap.
        swaps = int(gmgn.get("swaps") or 0)
        if gmgn.get("error") is None and swaps > 0:
            avg_trade = to_float(gmgn.get("volume_24h"), 0.0) / swaps
            tx_hour = swaps / 24.0
            if 0 < avg_trade < config.DEXS_MIN_AVG_TRADE and tx_hour <= config.DEXS_MAX_TX_PER_HOUR:
                penalty += 25
                flags.append(f"avg_trade=${avg_trade:.0f}")

        # 3. Same-second raw-tx, +20 per flag max 3
        same_second = token.get("same_second_flags") or []
        penalty += min(60, 20 * len(same_second))
        if same_second:
            flags.append(f"same_second_x{len(same_second)}")

        return penalty
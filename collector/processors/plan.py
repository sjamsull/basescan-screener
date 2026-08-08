"""Entry/Exit Plan — MCAP-based, bukan harga token.

Supply konstan -> rasio level identik berapa pun harga. Seluruh level dalam USD MCAP.
Aturan sakral (soul.md): definisi invalidation, definisi size, definisi exit.
Oversizing adalah kebodohan. NEUTRAL/CAUTION = tanpa entry plan.
"""

import logging
from typing import Dict

from collector.utils.helpers import to_float

logger = logging.getLogger(__name__)


def build_plan(token: Dict, verdict: str, risk: float) -> Dict:
    mcap = to_float(token.get("market_cap"), 0.0)
    if mcap <= 0:
        return {"error": "no_mcap", "verdict": verdict}

    current = mcap
    supply = to_float(token.get("total_supply"), to_float(token.get("circulating_supply"), 0.0))

    # Ukuran entry & ladder per verdict. Risk % = porsi risiko portofolio, bukan ukuran posisi.
    if verdict == "STRONG BUY":
        entry = {"lo": round(current * 0.95), "hi": round(current * 1.05)}
        invalidation = round(current * 0.78)
        tps = [round(current * 1.5), round(current * 2.5), round(current * 4.0)]
        risk_pct = 1.0
        allocation_pct = 8.0
        mode = "core"
    elif verdict == "BUY":
        entry = {"lo": round(current * 0.90), "hi": round(current * 1.00)}
        invalidation = round(current * 0.82)
        tps = [round(current * 1.5), round(current * 2.5), round(current * 3.5)]
        risk_pct = 0.5
        allocation_pct = 4.0
        mode = "probe"
    elif verdict == "NEUTRAL":
        entry = {"lo": None, "hi": None}
        invalidation = None
        tps = []
        risk_pct = 0.0
        allocation_pct = 0.0
        mode = "watch"
    else:  # CAUTION
        entry = {"lo": None, "hi": None}
        invalidation = None
        tps = []
        risk_pct = 0.0
        allocation_pct = 0.0
        mode = "avoid"

    # Konsolidasi risiko tinggi -> turunkan mode eksekusi, jangan naik.
    if risk >= 40 and verdict in ("STRONG BUY", "BUY"):
        mode = "probe" if verdict == "STRONG BUY" else "skip-open"
        risk_pct = min(risk_pct, 0.25)
        allocation_pct = min(allocation_pct, 2.0)

    return {
        "verdict": verdict,
        "mode": mode,
        "current_mcap": round(current),
        "supply": supply,
        "current_price": to_float(token.get("price"), 0.0),
        "entry_zone_mcap": entry,
        "invalidation_mcap": invalidation,
        "invalidation_pct": round((invalidation / current - 1) * 100, 1) if invalidation else None,
        "tp_ladder_mcap": tps,
        "tp_ladder_x": [round(t / current, 2) for t in tps],
        "risk_pct_of_portfolio": risk_pct,
        "max_allocation_pct": allocation_pct,
    }


def compact_plan(token: Dict) -> Dict:
    """Plan ringkas untuk disimpan di signals.prepared_data (hemat storage)."""
    plan = token.get("plan") or {}
    return {
        "mode": plan.get("mode"),
        "entry_mcap": plan.get("current_mcap"),
        "entry_zone_mcap": plan.get("entry_zone_mcap"),
        "invalidation_mcap": plan.get("invalidation_mcap"),
        "invalidation_pct": plan.get("invalidation_pct"),
        "tp_ladder_mcap": plan.get("tp_ladder_mcap"),
        "tp_ladder_x": plan.get("tp_ladder_x"),
        "risk_pct_of_portfolio": plan.get("risk_pct_of_portfolio"),
        "max_allocation_pct": plan.get("max_allocation_pct"),
    }
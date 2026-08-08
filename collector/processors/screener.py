"""Screener — orkestrasi per token.

Alur per token:
1. SecurityGate (filter murah: age/liq/top10/tax)        -> buang sampah cepat
2. GoPlus security (survivor-only, hemat rate-limit)    -> anti-rug/counter
3. DeepDive enrich (Gecko/GMGN/Explorer)          -> survivor-only
4. RiskEngine + TokenScorer                              -> skor & verdict
5. Record hasil atau reject permanen
"""

import logging
from typing import Dict, Optional

from collector.processors.security import SecurityGate
from collector.processors.deepdive import DeepDive
from collector.processors.risk import RiskEngine
from collector.processors.scoring import TokenScorer
from collector.processors.plan import build_plan

logger = logging.getLogger(__name__)


class Screener:
    def __init__(self, mode: str = "accumulation"):
        self.mode = mode
        self.gate = SecurityGate(mode)
        self.deepdive = DeepDive()
        self.risk = RiskEngine()
        self.scorer = TokenScorer()

    def process(self, chain_cfg, token: Dict, goplus_security: Optional[Dict] = None) -> Dict:
        """SUDAH lolos gate — dipanggil pipeline setelah security check GoPlus.

        Return record final: scored_alpha, risk, verdict, enriched data.
        """
        addr = token.get("address", "")

        security = goplus_security or {"is_honeypot": None, "owner_renounced": None}

        enriched = dict(token)
        if not security.get("is_honeypot"):
            try:
                enriched = self.deepdive.enrich(token, chain_cfg, security)
            except Exception as exc:
                logger.error("deepdive %s failed: %s", addr, exc)
                enriched = dict(token)

        enriched["security"] = security

        risk_result = self.risk.calculate(enriched, security)
        enriched["risk_score"] = risk_result["score"]
        enriched["risk_flags"] = risk_result["flags"]

        scoring = self.scorer.calculate_alpha(enriched)
        enriched["alpha_score"] = scoring["alpha"]
        enriched["alpha_breakdown"] = scoring["breakdown"]
        enriched["momentum"] = scoring["momentum"]
        enriched["momentum_detail"] = scoring["momentum_detail"]

        verdict = self.scorer.get_verdict(scoring["alpha"], risk_result["score"])
        enriched["verdict"] = verdict

        plan = build_plan(enriched, verdict, risk_result["score"])
        enriched["plan"] = plan

        return {
            "token": enriched,
            "status": "PASSED",
            "reject_reason": None,
            "alpha": scoring["alpha"],
            "risk": risk_result["score"],
            "verdict": verdict,
            "plan": plan,
        }
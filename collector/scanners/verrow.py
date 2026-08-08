"""VERROW — laporan risiko on-chain independen untuk Robinhood Chain (4663).

GET https://verrow.xyz/api/scan/{address} (tanpa auth, public).
Memberikan risk score 0-100 + level + findings terverifikasi (source code,
blacklist, mint authority, selfdestruct, honeypot sell-simulation, liquidity
lock, deployer history). Hanya mendukung chain 4663 (robinhood).
"""

import logging
from typing import Dict, Optional

from collector.utils.api import get_json, APIError

logger = logging.getLogger(__name__)


class VerrowClient:
    BASE_URL = "https://verrow.xyz/api/scan"

    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    def scan(self, address: str) -> Optional[Dict]:
        """Report lengkap dari VERROW (bila tersedia) atau None.

        429 di-handle backoff internal get_json. Menghindari poll cepat: caller
        yang harus meng-spacing panggilan (rate limit per IP).
        """
        url = f"{self.BASE_URL}/{address}"
        try:
            data = get_json(url, timeout=self.timeout, retries=1)
        except APIError as exc:
            logger.warning("verrow %s: %s", address[:10], exc)
            return None
        if not data or not data.get("ok"):
            return None
        report = data.get("report")
        if not report:
            return None
        return {
            "address": address,
            "risk_score": (report.get("risk") or {}).get("score"),
            "risk_level": (report.get("risk") or {}).get("level"),
            "coverage": (report.get("risk") or {}).get("coveragePercent"),
            "critical": (report.get("risk") or {}).get("criticalSummary"),
            "findings": (report.get("risk") or {}).get("findings"),
            "contract": report.get("contract"),
            "ownership": report.get("ownership"),
            "holders": report.get("holders"),
            "liquidity": report.get("liquidity"),
            "liquidityLock": report.get("liquidityLock"),
            "deployer": report.get("deployer"),
            "transferMechanics": report.get("transferMechanics"),
            "evidenceSources": report.get("evidenceSources"),
            "explorerUrl": report.get("explorerUrl"),
            "generatedAt": report.get("generatedAt"),
        }

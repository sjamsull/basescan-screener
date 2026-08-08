"""Sharpe — Rug Check token security (API key required).

GET /v1/rug-check/security?address=0x...&chainId=8453
Auth: Authorization: Bearer sk_live_...  (atau X-API-Key)
Docs: https://www.sharpe.ai/docs/free-api

Chain ID: base=8453, robinhood=4663.
Free tier: 30 RPM, 10.000/bulan — cukup untuk backfill ~230 token sekali jalan
dengan spacing. Hasilnya jauh lebih detail daripada GMGN security_json:
honeypot, mint, blacklist, proxy, hidden owner, buy/sell tax, selfdestruct,
transfer pausable, anti-whale, top holders, LP holders, dex info, CEX list.
"""

import logging
import os
import time
from typing import Dict, List, Optional

from collector.utils.api import get_json, APIError
from collector.utils.helpers import to_float

logger = logging.getLogger(__name__)

BASE_URL = "https://www.sharpe.ai/api/v1"
# Sharpe rug-check saat ini hanya mendukung jaringan EVM yang sudah discan
# (base=8453). Robinhood (4663) belum didukung — jangan masukkan ke sini.
CHAIN_IDS = {"base": 8453}


class SharpeClient:
    def __init__(self):
        self.api_key = os.getenv("SHARPE_API_KEY", "")
        self.base_url = BASE_URL

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "X-API-Key": self.api_key,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Basescan/1.0",
        }

    def rug_check(self, address: str, chain: str = "base") -> Optional[Dict]:
        """Laporan rug-check lengkap (None bila token tidak ditemukan / error)."""
        if not self.api_key:
            logger.error("SHARPE_API_KEY tidak di-set.")
            return None
        chain_id = CHAIN_IDS.get(chain)
        if not chain_id:
            logger.warning("sharpe: chain %s tidak dikenali", chain)
            return None
        url = f"{self.base_url}/rug-check/security"
        for attempt in range(3):
            try:
                data = get_json(url, params={"address": address, "chainId": chain_id},
                                headers=self._headers(), timeout=25, retries=1)
            except APIError as exc:
                retry = getattr(exc, "retry_after", None)
                if attempt < 2 and retry:
                    time.sleep(min(int(retry) + 2, 30))
                    continue
                logger.warning("sharpe %s %s: %s", chain, address[:10], exc)
                return None
            if not data or not data.get("ok"):
                return None
            report = data.get("report") or data.get("data")
            if not report:
                return None
            return report
        return None

    def trending(self, limit: int = 50) -> List[Dict]:
        """Token yang lagi trending untuk di-review rug-check."""
        if not self.api_key:
            return []
        url = f"{self.base_url}/rug-check/trending"
        try:
            data = get_json(url, params={"limit": limit}, headers=self._headers(),
                            timeout=25, retries=1)
        except APIError as exc:
            logger.warning("sharpe trending: %s", exc)
            return []
        if not data or not data.get("ok"):
            return []
        return (data.get("report") or data.get("data") or {}).get("tokens", [])


def summarize_rug(report: Dict) -> Dict:
    """Ringkas field rug-check jadi metrik mudah ditampilkan."""
    flags: list = []
    if report.get("isHoneypot"):
        flags.append("honeypot")
    if report.get("noMint") is False:
        flags.append("mintable")
    if report.get("noBlacklist") is False:
        flags.append("blacklist")
    if report.get("hiddenOwner"):
        flags.append("hidden_owner")
    if report.get("selfDestruct"):
        flags.append("selfdestruct")
    if report.get("transferPausable"):
        flags.append("pausable")
    if report.get("cannotSellAll"):
        flags.append("cannot_sell_all")
    if report.get("cannotBuy"):
        flags.append("cannot_buy")
    if report.get("canTakeBackOwnership"):
        flags.append("takeback_ownership")
    if report.get("isProxy"):
        flags.append("proxy")
    if report.get("slippageModifiable"):
        flags.append("slippage_modifiable")
    if report.get("personalSlippageModifiable"):
        flags.append("personal_slippage")
    if report.get("isAntiWhale"):
        flags.append("anti_whale")
    if report.get("externalCall"):
        flags.append("external_call")
    if report.get("honeypotWithSameCreator"):
        flags.append("honeypot_same_creator")
    top = report.get("topHolders") or []
    lp = report.get("lpHolders") or []
    dex = report.get("dexInfo") or []
    return {
        "is_honeypot": report.get("isHoneypot"),
        "mintable": report.get("noMint") is False,
        "blacklist": report.get("noBlacklist") is False,
        "open_source": report.get("isOpenSource"),
        "is_proxy": report.get("isProxy"),
        "hidden_owner": report.get("hiddenOwner"),
        "buy_tax": to_float(report.get("buyTax"), 0.0),
        "sell_tax": to_float(report.get("sellTax"), 0.0),
        "selfdestruct": report.get("selfDestruct"),
        "pausable": report.get("transferPausable"),
        "cannot_sell_all": report.get("cannotSellAll"),
        "cannot_buy": report.get("cannotBuy"),
        "takeback_ownership": report.get("canTakeBackOwnership"),
        "anti_whale": report.get("isAntiWhale"),
        "external_call": report.get("externalCall"),
        "isInCex": report.get("isInCex"),
        "cex_list": report.get("cexList") or [],
        "creator_percent": to_float(report.get("creatorPercent"), 0.0),
        "owner_percent": to_float(report.get("ownerPercent"), 0.0),
        "holder_count": report.get("holderCount"),
        "top_holders": [
            {"address": h.get("address"), "percent": h.get("percent"),
             "locked": h.get("isLocked"), "tag": h.get("tag")}
            for h in top[:5]
        ],
        "lp_liquidity_total": sum(to_float(d.get("liquidity"), 0.0) for d in dex),
        "dex_count": len(dex),
        "flags": flags,
        "source": report.get("source"),
    }

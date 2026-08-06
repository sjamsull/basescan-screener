"""Pipeline utama — fetch GMGN → gate murah → GoPlus security → deepdive → risk → scoring → store.

Rules:
- API gagal = ERROR tercatat, bukan silent-fallback ke mock.
- Reject disimpan permanen.
- Hanya survivor yang di-enrich (hemat rate-limit).
"""

import logging
import os
from typing import Dict, List

from collector import config
from collector.scanners.gmgn import GMGNClient
from collector.scanners.goplus import GoPlusClient
from collector.processors.screener import Screener
from collector.storage.local import LocalStore
from collector.storage.supabase import SupabaseStorage
from collector.utils.helpers import to_float

logger = logging.getLogger(__name__)


class TokenPipeline:
    def __init__(self, chain: str = "base"):
        self.chain = chain
        self.chain_cfg = config.CHAINS[chain]
        self.gmgn = GMGNClient(self.chain_cfg.gmgn_id)
        self.goplus = GoPlusClient()
        self.store = LocalStore()
        self.supabase = SupabaseStorage() if SupabaseStorage.configured() else None

    def run(self, mode: str = "accumulation", limit: int | None = None, dry: bool = False) -> Dict:
        limit = limit or config.SCAN_LIMIT
        summary = {"chain": self.chain, "mode": mode, "fetched": 0, "rejected": 0, "passed": 0, "errors": []}

        # 1. Fetch dari GMGN
        raw_tokens = self.gmgn.get_trending(mode=mode, limit=limit)
        summary["fetched"] = len(raw_tokens)
        logger.info("%s/%s fetch %d tokens", self.chain, mode, len(raw_tokens))

        # 2. Layer 1 gate (murah)
        survivors: List[Dict] = []
        rejects: List[Dict] = []
        for t in raw_tokens:
            addr = t.get("address", "")
            gate = self._gate(t)
            if gate["passed"]:
                survivors.append(t)
            else:
                rejects.append({"address": addr, "reason": gate["reason"]})

        summary["rejected"] = len(rejects)
        logger.info("%s/%s gate rejected %d, survivor %d", self.chain, mode, len(rejects), len(survivors))
        if self.supabase:
            for r in rejects:
                self.supabase.log_reject(r["address"], self.chain, mode, r["reason"])
        if not dry:
            self.store.write_reject_log(self.chain, mode, rejects)

        # 3. Survivor-only: GoPlus security → enrich → risk → scoring
        results: List[Dict] = []
        for t in survivors:
            addr = t.get("address", "")
            security = self.goplus.check_token(addr, self.chain_cfg.goplus_id)
            screener = Screener(mode)
            rec = screener.process(self.chain_cfg, t, security)

            if rec["status"] == "PASSED" and rec["alpha"] >= config.MIN_ALPHA_TO_SAVE:
                results.append(rec["token"])
                summary["passed"] += 1
                if self.supabase:
                    self.supabase.save_signal({**rec["token"], "chain": self.chain})

        # 4. Simpan
        if not dry:
            self.store.append(results, self.chain, mode)
            if self.supabase:
                try:
                    self.supabase.save_scan(results, self.chain, mode)
                except Exception as exc:
                    summary["errors"].append(f"supabase: {exc}")
                    logger.error("supabase save failed: %s", exc)

        summary["saved"] = len(results)
        return summary

    def _gate(self, token: Dict) -> Dict:
        from collector.processors.security import SecurityGate
        return SecurityGate(self.chain_mode(token)).check(token, pair=None)

    @staticmethod
    def chain_mode(token: Dict) -> str:
        return "dead_whale" if str(token.get("mode", "")).lower() in ("dead_whale", "fake_whale") else "accumulation"
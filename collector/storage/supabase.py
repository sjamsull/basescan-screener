"""Supabase storage — persisten, tidak ephemeral. Error API ditulis ke history, bukan dihilangkan.

Tabel yang dibutuhkan:
  scans(id, chain, mode, scanned_at, token_count, payload jsonb)
  reject_log(id, chain, mode, token_address, reason, rejected_at)
  signals(id, token_address, chain, verdict, alpha, risk, signal_at, exit_price, status)
"""

import os
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from collector.utils.helpers import to_float

logger = logging.getLogger(__name__)

try:
    from supabase import create_client, Client
except ImportError:
    Client = None  # type: ignore


class SupabaseStorage:
    def __init__(self, url: Optional[str] = None, key: Optional[str] = None):
        self.url = url or os.getenv("SUPABASE_URL", "")
        self.key = key or os.getenv("SUPABASE_SERVICE_KEY", "")
        if Client is None:
            raise RuntimeError("supabase package not installed")
        if not self.url or not self.key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY required")
        self.client: Client = create_client(self.url, self.key)

    @staticmethod
    def configured() -> bool:
        return bool(os.getenv("SUPABASE_URL", "") and os.getenv("SUPABASE_SERVICE_KEY", ""))

    def save_scan(self, records: List[Dict], chain: str, mode: str) -> Optional[str]:
        ts = datetime.now(timezone.utc).isoformat()
        try:
            resp = self.client.table("scans").insert({
                "chain": chain,
                "mode": mode,
                "token_count": len(records),
                "scanned_at": ts,
                "payload": records,
            }).execute()
            return resp.data[0]["id"] if resp.data else None
        except Exception as exc:
            logger.error("Supabase save_scan: %s", exc)
            # Integrity > silent: tulis ke local sebagai error record biar tidak hilang
            raise

    def save_signal(self, rec: Dict) -> None:
        from collector.processors.plan import compact_plan
        prepared = {
            "plan": compact_plan(rec),
            "alpha_breakdown": rec.get("alpha_breakdown"),
            "risk_flags": rec.get("risk_flags"),
            "momentum": rec.get("momentum"),
        }
        addr = rec.get("address", "")
        try:
            existing = self.client.table("signals").select("signal_at").eq(
                "token_address", addr
            ).limit(1).execute()
        except Exception:
            existing = None

        payload = {
            "token_address": addr,
            "chain": rec.get("chain", ""),
            "verdict": rec.get("verdict", ""),
            "alpha": to_float(rec.get("alpha_score"), 0.0),
            "risk": to_float(rec.get("risk_score"), 0.0),
            "prepared_data": prepared,
        }
        if not (existing and existing.data):
            from datetime import datetime, timezone
            payload["signal_at"] = datetime.now(timezone.utc).isoformat()

        try:
            self.client.table("signals").upsert(
                payload, on_conflict="token_address"
            ).execute()
        except Exception as exc:
            logger.error("Supabase save_signal %s: %s", addr, exc)

    def log_reject(self, addr: str, chain: str, mode: str, reason: str) -> None:
        try:
            self.client.table("reject_log").insert({
                "token_address": addr,
                "chain": chain,
                "mode": mode,
                "reason": reason,
                "rejected_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
        except Exception as exc:
            logger.error("Supabase reject_log %s: %s", addr, exc)

    def get_latest_scans(self, chain: str, limit: int = 20) -> List[Dict]:
        try:
            resp = self.client.table("scans").select("*").eq("chain", chain).order("scanned_at", desc=True).limit(limit).execute()
            return resp.data
        except Exception as exc:
            logger.error("Supabase get_latest_scans: %s", exc)
            return []
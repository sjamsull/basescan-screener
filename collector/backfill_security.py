"""Backfill security on-chain (GMGN /v1/token/security) untuk universe & signals.

Isi kolom dead_token_universe.security_json + risk_flags (dan signals.risk_flags)
dari GMGN, supaya dashboard kartu pemantauan tidak kosong ("SEC —").
Jalan: python -m collector.backfill_security [--chain base] [--table dead_token_universe] [--dry]
"""

import json
import logging
import sys
import time
from typing import List

from collector import config
from collector.scanners.gmgn import GMGNClient
from collector.storage.supabase import SupabaseStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_security")


def compute_flags(sec: dict) -> str:
    """Sama dengan _gmgn_gate di populate_universe.py."""
    flags: List[str] = []
    if sec.get("is_honeypot") and config.DW_GMGN_SKIP_HONEYPOT:
        flags.append("honeypot")
    if sec.get("is_show_alert") and config.DW_GMGN_SKIP_ALERT:
        flags.append("gmgn_alert")
    if sec.get("is_blacklist") not in (None, False, 0, "", -1):
        flags.append("blacklist")
    top10 = float(sec.get("top_10_holder_rate") or 0.0)
    if top10 > config.DW_GMGN_TOP10_MAX:
        flags.append(f"top10={top10:.0%}")
    rug = float(sec.get("rug_ratio") or 0.0)
    if rug > config.DW_GMGN_RUG_MAX:
        flags.append(f"rug={rug:.0%}")
    tax = max(float(sec.get("buy_tax") or 0.0), float(sec.get("sell_tax") or 0.0))
    if tax > config.DW_GMGN_MAX_TAX:
        flags.append(f"tax={tax:.0%}")
    return ",".join(flags)


def main() -> int:
    if not SupabaseStorage.configured():
        logger.error("Supabase tidak dikonfigurasi.")
        return 1
    args = sys.argv[1:]
    chain = args[args.index("--chain") + 1] if "--chain" in args else None
    table = args[args.index("--table") + 1] if "--table" in args else "dead_token_universe"
    dry = "--dry" in args

    store = SupabaseStorage()
    rows: List[dict] = []
    offset = 0
    while True:
        q = (store.client.table(table)
             .select("token_address,chain,security_json,risk_flags")
             .order("last_seen", desc=True).range(offset, offset + 999)) if table == "dead_token_universe" else \
            (store.client.table(table)
             .select("token_address,chain,prepared_data")
             .order("signal_at", desc=True).range(offset, offset + 999))
        if chain:
            q = q.eq("chain", chain)
        resp = q.execute()
        batch = resp.data or []
        rows += batch
        if len(batch) < 100:
            break
        offset += 100
        if offset > 30000:
            break

    # hanya yang belum punya security_json (untuk universe) — signals pakai prepared
    if table == "dead_token_universe":
        rows = [r for r in rows if not (r.get("security_json"))]
    else:
        rows = [r for r in rows if not (((r.get("prepared_data") or {}).get("security_json")))]

    logger.info("%s rows to backfill: %d", table, len(rows))
    clients = {}
    for r in rows:
        ch = r.get("chain") or chain or "base"
        if ch not in clients:
            clients[ch] = GMGNClient(ch)
        client = clients[ch]
        addr = r["token_address"]
        try:
            sec = client.token_security(addr)
        except Exception as exc:
            logger.warning("gmgn %s %s: %s", ch, addr[:10], exc)
            time.sleep(0.4)
            continue
        if not sec:
            time.sleep(0.3)
            continue
        sec_json = json.dumps(sec, ensure_ascii=False, default=str)
        flags = compute_flags(sec)
        try:
            if table == "dead_token_universe":
                store.client.table(table).update(
                    {"security_json": sec_json, "risk_flags": flags or None}
                ).eq("token_address", addr).execute()
            else:
                pd = dict(r.get("prepared_data") or {})
                pd["security_json"] = sec_json
                if flags:
                    pd["risk_flags"] = flags
                store.client.table(table).update(
                    {"prepared_data": pd}
                ).eq("token_address", addr).execute()
            logger.info("backfill %s %s flags=%s", ch, addr[:10], flags or "-")
        except Exception as exc:
            logger.error("update %s: %s", addr[:10], exc)
        time.sleep(0.3)

    logger.info("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""Backfill security_json + created_at untuk universe token yang masih kosong.

Jalankan otomatis setelah scan/populate supaya token baru di dashboard
langsung punya data security on-chain & umur token.
Jalan: python -m collector.backfill_universe_enrich [--chain base] [--dry]
"""

import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import List

from collector.scanners.gmgn import GMGNClient
from collector.storage.supabase import SupabaseStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_universe_enrich")


def main() -> int:
    if not SupabaseStorage.configured():
        logger.error("Supabase tidak dikonfigurasi.")
        return 1
    args = sys.argv[1:]
    chain = args[args.index("--chain") + 1] if "--chain" in args else None
    dry = "--dry" in args

    store = SupabaseStorage()
    chains = [chain] if chain else ["base", "robinhood"]
    clients = {}

    for ch in chains:
        rows: List[dict] = []
        offset = 0
        while True:
            q = (store.client.table("dead_token_universe")
                 .select("token_address,security_json,created_at")
                 .eq("chain", ch).range(offset, offset + 999))
            resp = q.execute()
            batch = resp.data or []
            rows += batch
            if len(batch) < 100:
                break
            offset += 100
            if offset > 20000:
                break
        # hanya yang belum punya security_json ATAU created_at
        need = [r for r in rows if not r.get("security_json") or not r.get("created_at")]
        logger.info("%s: %d token, %d perlu enrich", ch, len(rows), len(need))
        if ch not in clients:
            clients[ch] = GMGNClient(ch)
        client = clients[ch]

        filled = 0
        for r in need:
            addr = r["token_address"]
            rec = {}
            try:
                sec = client.token_security(addr)
                if sec:
                    rec["security_json"] = json.dumps(sec, ensure_ascii=False, default=str)
            except Exception as exc:
                logger.debug("security %s: %s", addr[:10], exc)
            try:
                info = client.get_token_info(addr)
                if info:
                    ts = int(info.get("creation_timestamp") or 0)
                    if ts > 0:
                        rec["created_at"] = datetime.fromtimestamp(ts, timezone.utc).isoformat()
            except Exception as exc:
                logger.debug("info %s: %s", addr[:10], exc)
            if not rec:
                time.sleep(0.3)
                continue
            if dry:
                logger.info("[dry] %s %s keys=%s", ch, addr[:10], list(rec.keys()))
                continue
            try:
                store.client.table("dead_token_universe").update(rec).eq("token_address", addr).execute()
                filled += 1
            except Exception as exc:
                logger.error("update %s: %s", addr[:10], exc)
            time.sleep(0.3)

        logger.info("done %s: filled=%d dry=%s", ch, filled, dry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

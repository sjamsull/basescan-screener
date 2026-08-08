"""Backfill symbol GMGN untuk sinyal yang prepared_data.symbol-nya kosong.

Filter non-meme (SecurityGate, cleanup_non_meme, dashboard isNM) mencocokkan
SYMBOL — kalau symbol kosong, token mayor seperti WBTC Lolos. Script ini mengisi
symbol dari GMGN /v1/token/info untuk semua sinyal (base/robinhood) yang belum
punya symbol, supaya filter non-meme bisa bekerja.
Jalan: python -m collector.backfill_symbols_gmgn [--chain base] [--dry]
"""

import logging
import sys
import time
from typing import Dict, List

from collector.scanners.gmgn import GMGNClient
from collector.storage.supabase import SupabaseStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_symbols_gmgn")


def symbol_of(client: GMGNClient, addr: str) -> str:
    try:
        info = client.get_token_info(addr)
    except Exception:
        return ""
    return (info.get("symbol") or "").strip()


def main() -> int:
    if not SupabaseStorage.configured():
        logger.error("Supabase tidak dikonfigurasi.")
        return 1
    args = sys.argv[1:]
    chain = args[args.index("--chain") + 1] if "--chain" in args else None
    dry = "--dry" in args
    chains = [chain] if chain else ["base", "robinhood"]

    store = SupabaseStorage()
    clients: Dict[str, GMGNClient] = {}
    filled = 0

    for ch in chains:
        rows: List[dict] = []
        offset = 0
        while True:
            q = (store.client.table("signals")
                 .select("token_address,prepared_data")
                 .order("signal_at", desc=True).range(offset, offset + 999))
            if ch:
                q = q.eq("chain", ch)
            resp = q.execute()
            batch = resp.data or []
            rows += batch
            if len(batch) < 100:
                break
            offset += 100
            if offset > 20000:
                break
        need = [r for r in rows if r.get("token_address") and not ((r.get("prepared_data") or {}).get("symbol") or "").strip()]
        logger.info("%s: %d sinyal, %d symbol kosong", ch, len(rows), len(need))
        if ch not in clients:
            clients[ch] = GMGNClient(ch)
        client = clients[ch]

        for r in need:
            addr = r["token_address"]
            sym = symbol_of(client, addr)
            if not sym:
                time.sleep(0.3)
                continue
            pd = dict(r.get("prepared_data") or {})
            pd["symbol"] = sym
            if dry:
                logger.info("[dry] %s %s -> %s", ch, addr[:10], sym)
                time.sleep(0.2)
                continue
            try:
                store.client.table("signals").update(
                    {"prepared_data": pd}).eq("token_address", addr).execute()
                filled += 1
            except Exception as exc:
                logger.error("update %s %s: %s", ch, addr[:10], exc)
            time.sleep(0.2)

    logger.info("done: filled=%d dry=%s", filled, dry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

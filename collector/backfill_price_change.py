"""Backfill GMGN price momentum (proxy untuk EMA/trend) untuk dead_token_universe.

GMGN /v1/token/info tidak return price change langsung, tapi kasih harga
historis: price_1h, price_6h, price_24h. Kita hitung:
    change% = (price_now - price_hist) / price_hist * 100

Simpan price_change_json = {1h, 6h, 24h} percent.
Jalan: python -m collector.backfill_price_change [--chain base] [--sleep 1]
"""

import json
import logging
import sys
import time
from typing import List

from collector.scanners.gmgn import GMGNClient
from collector.storage.supabase import SupabaseStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_price_change")


def calc_momentum(now: float, hist: float) -> float:
    if now > 0 and hist > 0:
        return round((now - hist) / hist * 100, 3)
    return 0.0


def main() -> int:
    if not SupabaseStorage.configured():
        logger.error("Supabase tidak dikonfigurasi.")
        return 1
    args = sys.argv[1:]
    chain = args[args.index("--chain") + 1] if "--chain" in args else None
    dry = "--dry" in args
    sleep_s = float(args[args.index("--sleep") + 1]) if "--sleep" in args else 1.0

    store = SupabaseStorage()
    chains = [chain] if chain else ["base", "robinhood"]
    clients = {}

    for ch in chains:
        rows: List[dict] = []
        offset = 0
        while True:
            q = (store.client.table("dead_token_universe")
                 .select("token_address")
                 .eq("chain", ch).range(offset, offset + 999))
            resp = q.execute()
            batch = resp.data or []
            rows += batch
            if len(batch) < 100:
                break
            offset += 100
            if offset > 20000:
                break
        addrs = sorted({r["token_address"] for r in rows if r.get("token_address")})
        logger.info("%s: %d token universe", ch, len(addrs))
        if ch not in clients:
            clients[ch] = GMGNClient(ch)

        filled = 0
        for a in addrs:
            try:
                info = clients[ch].get_token_info(a)
            except Exception as exc:
                logger.warning("gmgn %s %s: %s", ch, a[:10], exc)
                time.sleep(sleep_s)
                continue
            pv = float(info.get("price_usd") or 0.0)
            if pv <= 0:
                time.sleep(sleep_s)
                continue
            pc = {
                "1h": calc_momentum(pv, float(info.get("price_1h") or 0.0)),
                "6h": calc_momentum(pv, float(info.get("price_6h") or 0.0)),
                "24h": calc_momentum(pv, float(info.get("price_24h") or 0.0)),
            }
            if dry:
                logger.info("[dry] %s %s pc=%s", ch, a[:10], pc)
                time.sleep(0.2)
                continue
            try:
                store.client.table("dead_token_universe").update(
                    {"price_change_json": json.dumps(pc)}
                ).eq("token_address", a).execute()
                filled += 1
            except Exception as exc:
                logger.error("update %s: %s", a[:10], exc)
            time.sleep(sleep_s)
        logger.info("done %s: filled=%d dry=%s", ch, filled, dry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

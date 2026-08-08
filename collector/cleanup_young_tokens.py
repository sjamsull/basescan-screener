"""Backfill created_at (umur token) lalu hapus token terlalu muda dari universe.

Many robinhood tokens are hours/days old (rug-pull risk). Dashboard & backtest
pakai deadline UNIVERSE_MIN_AGE_DAYS (default 7). Script ini:
  1. isi dead_token_universe.created_at dari GMGN get_token_info.creation_timestamp
  2. hapus token yang umurnya < UNIVERSE_MIN_AGE_DAYS

Jalan: python -m collector.cleanup_young_tokens [--chain robinhood] [--dry] [--sleep 2]
"""

import logging
import sys
import time
from datetime import datetime, timezone
from typing import List

from collector import config
from collector.scanners.gmgn import GMGNClient
from collector.storage.supabase import SupabaseStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("cleanup_young_tokens")


def main() -> int:
    if not SupabaseStorage.configured():
        logger.error("Supabase tidak dikonfigurasi.")
        return 1
    args = sys.argv[1:]
    chain = args[args.index("--chain") + 1] if "--chain" in args else None
    dry = "--dry" in args
    sleep_s = float(args[args.index("--sleep") + 1]) if "--sleep" in args else 1.5
    min_age_days = config.UNIVERSE_MIN_AGE_DAYS

    store = SupabaseStorage()
    chains = [chain] if chain else ["base", "robinhood"]
    clients = {}

    for ch in chains:
        rows: List[dict] = []
        offset = 0
        while True:
            q = (store.client.table("dead_token_universe")
                 .select("token_address,created_at")
                 .eq("chain", ch).range(offset, offset + 999))
            resp = q.execute()
            batch = resp.data or []
            rows += batch
            if len(batch) < 100:
                break
            offset += 100
            if offset > 20000:
                break
        logger.info("%s: %d token, sudah ada created_at=%d",
                    ch, len(rows), sum(1 for r in rows if r.get("created_at")))
        if ch not in clients:
            clients[ch] = GMGNClient(ch)
        client = clients[ch]
        now = datetime.now(timezone.utc)

        for r in rows:
            addr = r["token_address"]
            created = r.get("created_at")
            if not created:
                try:
                    gi = client.get_token_info(addr)
                    ts = int((gi or {}).get("creation_timestamp") or 0)
                except Exception as exc:
                    logger.warning("gmgn %s %s: %s", ch, addr[:10], exc)
                    time.sleep(sleep_s)
                    continue
                if ts > 0:
                    created = datetime.fromtimestamp(ts, timezone.utc).isoformat()
                    if not dry:
                        try:
                            store.client.table("dead_token_universe").update(
                                {"created_at": created}).eq("token_address", addr).execute()
                        except Exception as exc:
                            logger.error("update created_at %s: %s", addr[:10], exc)
                    time.sleep(sleep_s)
                else:
                    logger.warning("%s %s: no creation_timestamp (skip age filter)", ch, addr[:10])
                    continue
            # usia terhadap created_at
            try:
                ca = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                if ca.tzinfo is None:
                    ca = ca.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            age_days = (now - ca).total_seconds() / 86400.0
            if age_days < min_age_days:
                if dry:
                    logger.info("[dry] delete %s %s age=%.1fd", ch, addr[:10], age_days)
                else:
                    try:
                        store.client.table("dead_token_universe").delete().eq(
                            "token_address", addr).execute()
                        logger.info("delete %s %s age=%.1fd", ch, addr[:10], age_days)
                    except Exception as exc:
                        logger.error("delete %s: %s", addr[:10], exc)
            time.sleep(sleep_s)

    logger.info("done (min_age=%dd, dry=%s)", min_age_days, dry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

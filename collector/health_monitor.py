"""Health monitor + track cleanup untuk scanner.

Cek apakah scan & backtest jalan tepat waktu, data fresh, dan tracks
terkini sudah GMGN (bukan DexScreener lama).

Jalan: python -m collector.health_monitor [--fix-tracks]
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta

from collector.storage.supabase import SupabaseStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("health_monitor")


def main() -> int:
    if not SupabaseStorage.configured():
        logger.error("Supabase tidak dikonfigurasi.")
        return 1
    fix = "--fix-tracks" in sys.argv
    store = SupabaseStorage()
    now = datetime.now(timezone.utc)
    ok = True

    # 1. Scan terakhir (scans table)
    try:
        last_scan = store.client.table("scans").select("scanned_at,mode,chain").order("scanned_at", desc=True).limit(1).execute()
        if last_scan.data:
            ts = last_scan.data[0]["scanned_at"]
            age = now - datetime.fromisoformat(ts.replace("Z", "+00:00"))
            status = "OK" if age < timedelta(hours=7) else "STALE"
            if status == "STALE":
                ok = False
            logger.info("last scan: %s (%s) age=%s [%s]", ts[:19], last_scan.data[0].get("mode"), age, status)
        else:
            logger.warning("last scan: BELUM ADA")
            ok = False
    except Exception as exc:
        logger.error("scan check: %s", exc)
        ok = False

    # 2. Backtest terakhir (backtest_reports)
    try:
        last_rep = store.client.table("backtest_reports").select("generated_at").order("generated_at", desc=True).limit(1).execute()
        if last_rep.data:
            ts = last_rep.data[0]["generated_at"]
            age = now - datetime.fromisoformat(ts.replace("Z", "+00:00"))
            status = "OK" if age < timedelta(hours=7) else "STALE"
            if status == "STALE":
                ok = False
            logger.info("last backtest: %s age=%s [%s]", ts[:19], age, status)
        else:
            logger.warning("last backtest: BELUM ADA")
            ok = False
    except Exception as exc:
        logger.error("backtest check: %s", exc)
        ok = False

    # 3. Dead-whale pipeline freshness per chain (via heartbeat di scans).
    #    dead_token_universe.last_seen hanya maju saat ada token mati BARU
    #    ditemukan; di pasar sepi ini normal tidak bergerak, sehingga kita
    #    ukur "apakah pipeline populate_universe/dead_whale masih jalan" bukan
    #    "apakah token baru ditemukan".
    for chain in ["base", "robinhood"]:
        try:
            s = (store.client.table("scans")
                 .select("scanned_at,mode,chain")
                 .eq("mode", "dead_whale").eq("chain", chain)
                 .order("scanned_at", desc=True).limit(1).execute())
            if s.data:
                ts = s.data[0]["scanned_at"]
                age = now - datetime.fromisoformat(ts.replace("Z", "+00:00"))
                status = "OK" if age < timedelta(hours=8) else "STALE"
                if status == "STALE":
                    ok = False
                logger.info("dead_whale %s: last=%s age=%s [%s]", chain, ts[:19], age, status)
            else:
                logger.warning("dead_whale %s: BELUM ADA scan", chain)
                ok = False
        except Exception as exc:
            logger.error("dead_whale %s: %s", chain, exc)
            ok = False

    # 4. Signal_tracks cleanup (opsional)
    if fix:
        try:
            # Hapus track > 30 hari (DexScreener lama), biarkan yang baru ter-track GMGN
            cutoff = (now - timedelta(days=30)).isoformat()
            old = store.client.table("signal_tracks").delete().lt("tracked_at", cutoff).execute()
            logger.info("tracks > 30 hari dihapus: %s", getattr(old, "count", "?"))
        except Exception as exc:
            logger.warning("track cleanup: %s", exc)

    logger.info("overall: %s", "HEALTHY" if ok else "NEEDS ATTENTION")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

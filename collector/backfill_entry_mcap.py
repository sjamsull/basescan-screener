"""Backfill entry_mcap untuk sinyal yang tidak punya MC masuk radar.

Isi prepared_data.plan.entry_mcap dari mcap TRACK PERTAMA signal_tracks
(MC saat pertama kali di-track ≈ MC saat lolos filter / masuk radar).
Jalan: python -m collector.backfill_entry_mcap [--chain base] [--dry]
"""

import logging
import sys
from typing import List

from collector.storage.supabase import SupabaseStorage
from collector.utils.helpers import to_float

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_entry_mcap")


def main() -> int:
    if not SupabaseStorage.configured():
        logger.error("Supabase tidak dikonfigurasi.")
        return 1
    args = sys.argv[1:]
    chain = args[args.index("--chain") + 1] if "--chain" in args else None
    dry = "--dry" in args

    store = SupabaseStorage()
    rows: List[dict] = []
    offset = 0
    while True:
        q = (store.client.table("signals")
             .select("token_address,prepared_data")
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

    filled = 0
    skipped = 0
    logger.info("loaded %d signals (chain=%s)", len(rows), chain or "all")
    for r in rows:
        pd = r.get("prepared_data") or {}
        plan = pd.get("plan") if isinstance(pd.get("plan"), dict) else {}
        if to_float(plan.get("entry_mcap"), 0.0) > 0:
            continue  # sudah punya
        if len(rows) and r is rows[0]:
            logger.info("sample plan keys=%s entry_mcap=%r", list(plan.keys()), plan.get("entry_mcap"))
        addr = r.get("token_address")
        if not addr:
            continue
        # Track pertama (terawal) = MC saat masuk radar
        try:
            t = (store.client.table("signal_tracks")
                 .select("mcap")
                 .eq("token_address", addr)
                 .order("tracked_at")
                 .limit(1)
                 .execute())
        except Exception as exc:
            logger.error("track %s: %s", addr[:10], exc)
            continue
        data = t.data or []
        if not data:
            skipped += 1
            continue
        mc = to_float(data[0].get("mcap"), 0.0)
        if mc <= 0:
            skipped += 1
            continue
        plan["entry_mcap"] = round(mc)
        if dry:
            logger.info("[dry] %s entry_mcap=%s", addr[:10], round(mc))
            continue
        try:
            store.client.table("signals").update(
                {"prepared_data": pd}
            ).eq("token_address", addr).execute()
            filled += 1
        except Exception as exc:
            logger.error("update %s: %s", addr[:10], exc)

    logger.info("backfill done: filled=%d skipped(no-track)=%d (dry=%s)", filled, skipped, dry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
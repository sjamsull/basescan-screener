"""Cleanup historis: hapus sinyal non-meme/mikro-cap dari tabel signals.

Menerapkan kebijakan yang sama dengan SecurityGate sekarang (SIGNAL_EXCLUDE_*,
SIGNAL_MAX_MARKET_CAP_USD) terhadap sinyal lama yang dibuat sebelum filter
tersebut. Jalan: python -m collector.cleanup_non_meme [--chain base] [--dry]
"""

import logging
import sys
from typing import List

from collector import config
from collector.storage.supabase import SupabaseStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("cleanup_non_meme")


def classify(sym: str, name: str) -> str:
    """Return reason non-meme atau '' bila aman.

    Hanya symbol/name yang andal untuk memilah non-meme (major/wrapped/stable).
    MC TIDAK dipakai di sini: mcap historis tidak tersimpan akurat (compact_plan
    menyimpan entry_mcap dari DexScreener per-pair yang meleset, GMGN-current
    menangkap meme yang tumbuh). Filter MC tetap berlaku di SecurityGate untuk
    scan baru (SIGNAL_MAX_MARKET_CAP_USD), bukan untuk menghapus riwayat meme.
    """
    usym = (sym or "").upper()
    if usym in {s.upper() for s in config.SIGNAL_EXCLUDE_SYMBOLS}:
        return f"symbol:{sym}"
    for p in config.SIGNAL_EXCLUDE_SYMBOL_PARTS:
        if p and p.upper() in usym:
            return f"symbol:{sym}"
    uname = (name or "").lower()
    for p in config.SIGNAL_EXCLUDE_NAME_PARTS:
        if p and p in uname:
            return f"name:{name}"
    return None


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
        if offset > 20000:
            break

    # MC otoritatif GMGN dari universe (tidak ada di prepared_data / compact_plan)
    addrs = sorted({r["token_address"] for r in rows if r.get("token_address")})
    mcap_of: dict = {}
    for _, grp in _chunks(addrs, 80):
        try:
            resp = (store.client.table("dead_token_universe")
                    .select("token_address,market_cap")
                    .in_("token_address", list(grp)).execute())
            for u in (resp.data or []):
                mcap_of[u["token_address"]] = float(u.get("market_cap") or 0.0)
        except Exception as exc:
            logger.warning("universe mcap chunk: %s", exc)

    deleted = 0
    for r in rows:
        pd = r.get("prepared_data") or {}
        sym = (r.get("symbol") or pd.get("symbol") or "").strip()
        addr = r.get("token_address")
        mcap = mcap_of.get(addr, 0.0)
        reason = classify(sym, "")
        if not reason:
            continue
        if not addr:
            continue
        if dry:
            logger.info("[dry] delete %s %s (mcap=%s) (%s)", chain or "?", sym or "?", round(mcap), reason)
        else:
            try:
                store.client.table("signals").delete().eq("token_address", addr).execute()
                deleted += 1
            except Exception as exc:
                logger.error("delete %s: %s", addr[:10], exc)
    logger.info("cleanup done: deleted=%d (dry=%s)", deleted, dry)
    return 0


def _chunks(items, n):
    for i in range(0, len(items), n):
        yield i, items[i:i + n]


if __name__ == "__main__":
    raise SystemExit(main())
"""Reset signal_tracks: hapus riwayat lama (sumber DexScreener, per-pair tidak
konsisten) lalu mulai ulang timeline dari GMGN (satu-satunya sumber MC).

Setelah ini dashboard "MC saat ini"/Track Record tidak lagi tercampur sumber.
Jalan: python -m collector.reset_signal_tracks [--chain base] [--dry]
"""

import logging
import sys

from collector.storage.supabase import SupabaseStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("reset_signal_tracks")


def main() -> int:
    if not SupabaseStorage.configured():
        logger.error("Supabase tidak dikonfigurasi.")
        return 1
    args = sys.argv[1:]
    chain = args[args.index("--chain") + 1] if "--chain" in args else None
    dry = "--dry" in args

    store = SupabaseStorage()
    q = store.client.table("signal_tracks").delete().gte("id", 0)
    if chain:
        q = q.eq("chain", chain)
    if dry:
        logger.warning("[dry] MENGHAPUS signal_tracks chain=%s — TIDAK dijalankan", chain or "all")
        return 0

    resp = q.execute()
    logger.info("signal_tracks dihapus (chain=%s). Timeline baru dari GMGN dimulai track berikutnya.", chain or "all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Entry point collector.

Usage:
  python -m collector.main --chain base --mode accumulation --limit 50
  python -m collector.main --all  # jalankan semua chain & mode aktif
"""

import argparse
import logging
import sys
from typing import List

from collector import config
from collector.pipeline import TokenPipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("collector.main")


def run_chain(chain: str, modes: List[str], limit: int | None) -> List[dict]:
    summary = []
    for mode in modes:
        pipe = TokenPipeline(chain)
        res = pipe.run(mode=mode, limit=limit)
        summary.append(res)
        logger.info("DONE %s/%s -> %d saved, %d rejected", chain, mode, res.get("saved"), res.get("rejected"))
    return summary


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="collector")
    ap.add_argument("--chains", nargs="*", default=None,
                    help="chains to scan (default ALL configured)")
    ap.add_argument("--mode", default=None, choices=["accumulation", "dead_whale"],
                    help="single mode override")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry", action="store_true", help="no writes, dry run")
    args = ap.parse_args(argv)

    chains = args.chains or config.chain_order()
    modes = [args.mode] if args.mode else config.active_modes()

    if not chains:
        logger.error("no chains configured via CHAIN_WEIGHT_ORDER")
        return 2
    if not modes:
        logger.error("no active modes (ENABLE_MODE_* = false)")
        return 2

    all_summaries = []
    for chain in chains:
        all_summaries.extend(run_chain(chain, modes, args.limit))

    for s in all_summaries:
        print(f"[{s['chain']}/{s['mode']}] fetched={s['fetched']} "
              f"passed={s['passed']} rejected={s['rejected']}")


if __name__ == "__main__":
    sys.exit(main())
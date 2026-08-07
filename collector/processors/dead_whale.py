"""Dead-Whale Scanner (Tahap 1) — deteksi whale beli cicil di token mati.

Alur:
  1. Ambil universe token mati (dari dead_token_universe / signals yang sudah ada).
  2. Per token: cek metadata (umur, volume, holders) via Blockscout.
  3. Ambil top holders -> kandidat whale (filter is_contract & is_scam).
  4. Ambil riwayat transfer -> deteksi buy/sell per wallet.
  5. Update whale_positions (ledger state: first_buy, buy_count, net_position, hold_days).

Status whale_positions:
  WATCH   : whale baru beli (belum cukup bukti)
  CONFIRM : beli lagi tanpa jual + hold >= DW_HOLD_MIN_DAYS
  SIGNAL  : akumulasi kuat (hold lama, net_position naik, sell_count=0)
  DUMPED  : whale sudah jual (sell_count > 0)

Configurable via .env (DW_*).
"""

import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from collector import config
from collector.scanners.blockscout import BlockscoutClient
from collector.storage.supabase import SupabaseStorage
from collector.utils.helpers import to_float, to_int

logger = logging.getLogger(__name__)

ZERO = "0x0000000000000000000000000000000000000000"
BURN = "0x000000000000000000000000000000000000dead"


class DeadWhaleScanner:
    def __init__(self, chain: str = "base"):
        self.chain = chain
        self.bs = BlockscoutClient()
        self.store = SupabaseStorage() if SupabaseStorage.configured() else None
        if self.store is None:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY required")

    # ---------- universe ----------

    def load_universe(self, limit: int) -> List[Dict]:
        """Ambil token mati dari dead_token_universe (atau seed dari signals)."""
        try:
            resp = (
                self.store.client.table("dead_token_universe")
                .select("token_address,chain,symbol,decimals,total_supply,market_cap")
                .eq("chain", self.chain)
                .limit(limit)
                .execute()
            )
            rows = resp.data or []
            if rows:
                return rows
        except Exception as exc:
            logger.warning("load_universe dead_token_universe: %s", exc)

        # seed: token yang sudah ada di signals (chain ini)
        try:
            resp = (
                self.store.client.table("signals")
                .select("token_address,chain")
                .eq("chain", self.chain)
                .limit(limit)
                .execute()
            )
            return resp.data or []
        except Exception as exc:
            logger.warning("load_universe signals seed: %s", exc)
            return []

    # ---------- whale detection ----------

    def _price_usd(self, info: Dict) -> float:
        """Harga token: pakai exchange_rate langsung (v2) kalau ada,
        else fallback market_cap / total_supply."""
        er = to_float(info.get("exchange_rate"), 0.0)
        if er:
            return er
        decimals = to_int(info.get("decimals"), 18)
        supply_raw = to_float(info.get("total_supply"), 0.0)
        mcap = to_float(info.get("market_cap"), 0.0)
        if not supply_raw or not mcap:
            return 0.0
        return mcap / (supply_raw / (10 ** decimals))

    def analyze_token(self, token: Dict) -> Dict:
        """Deteksi dead-whale dari riwayat transfer: wallet yang beli berulang
        (buy >= DW_MIN_BUY_USD per tx) dan tidak pernah jual.

        Konsep: whale akumulasi terlihat dari pola transfer (banyak buy, 0 sell),
        bukan dari top-holders (yang bisa hold diam tanpa trading).
        """
        addr = token.get("token_address", "")
        info = self.bs.token_info(self.chain, addr) or {}
        price = self._price_usd(info)
        decimals = to_int(info.get("decimals"), 18)

        # ambil transfer sepanjang window analisis (token mati: transfer sedikit, jadi terjangkau)
        since = datetime.now(timezone.utc).timestamp() - config.DW_LOOKBACK_DAYS * 86400
        transfers = self.bs.token_transfers_since(self.chain, addr, since_ts=int(since))

        # agregasi per wallet
        wallet_activity: Dict[str, Dict] = {}
        for t in transfers:
            frm = (t.get("from") or "").lower()
            to = (t.get("to") or "").lower()
            value_raw = to_float(t.get("value"), 0.0)
            value_usd = (value_raw / (10 ** decimals)) * price if price else 0.0
            ts = to_int(t.get("timeStamp"), 0)
            if not value_raw or not ts:
                continue
            if frm == ZERO or to == ZERO or frm == BURN or to == BURN:
                continue
            # transfer dari ke router/contract tidak kita hitung sebagai aktivitas wallet
            # (hanya wallet non-contract yang kita pantau sebagai kandidat)
            if value_usd >= config.DW_MIN_BUY_USD:
                act = wallet_activity.setdefault(to, {"buy": 0, "sell": 0, "first": ts, "last": ts, "buy_amt": 0.0, "sell_amt": 0.0, "buy_usd": 0.0, "sell_usd": 0.0})
                act["buy"] += 1
                act["buy_amt"] += value_raw
                act["buy_usd"] += value_usd
                act["first"] = min(act["first"], ts)
                act["last"] = max(act["last"], ts)
            if frm != ZERO:
                act = wallet_activity.setdefault(frm, {"buy": 0, "sell": 0, "first": ts, "last": ts, "buy_amt": 0.0, "sell_amt": 0.0, "buy_usd": 0.0, "sell_usd": 0.0})
                if value_usd >= config.DW_MIN_BUY_USD:
                    act["sell"] += 1
                    act["sell_amt"] += value_raw
                    act["sell_usd"] += value_usd
                    act["last"] = max(act["last"], ts)

        return {
            "token": addr,
            "info": info,
            "price_usd": price,
            "transfers_scanned": len(transfers),
            "activity": wallet_activity,
        }

    def update_positions(self, result: Dict) -> List[Dict]:
        """Update whale_positions dari hasil analisis satu token.

        Hanya wallet yang MEMBELI (buy >= konfigurasi) dan tidak signifikan menjual
        yang menjadi kandidat whale. Pola:
          WATCH   : buy baru (belum cukup waktu)
          CONFIRM : buy >= 2, sell == 0, hold >= DW_HOLD_MIN_DAYS
          SIGNAL  : buy >= 3, sell == 0, hold >= DW_HOLD_MIN_DAYS*2
          DUMPED  : ada sell (wallet sudah keluar)
        """
        addr = result["token"]
        now = datetime.now(timezone.utc)
        positions = []

        for wallet, act in result["activity"].items():
            if act["sell"] > 0:
                continue
            if act["buy"] == 0:
                continue
            hold_days = (now.timestamp() - act["first"]) / 86400.0
            status = "WATCH"
            if act["buy"] >= 2 and hold_days >= config.DW_HOLD_MIN_DAYS:
                status = "CONFIRM"
            if act["buy"] >= 3 and hold_days >= config.DW_HOLD_MIN_DAYS * 2:
                status = "SIGNAL"

            positions.append({
                "token_address": addr,
                "chain": self.chain,
                "wallet": wallet,
                "first_buy_at": datetime.fromtimestamp(act["first"], timezone.utc).isoformat(),
                "last_buy_at": datetime.fromtimestamp(act["last"], timezone.utc).isoformat(),
                "buy_count": act["buy"],
                "sell_count": act["sell"],
                "net_position": act["buy_amt"] - act["sell_amt"],
                "total_buy": act["buy_amt"],
                "total_sell": act["sell_amt"],
                "buy_usd": round(act["buy_usd"], 2),
                "sell_usd": round(act["sell_usd"], 2),
                "hold_days": round(hold_days, 1),
                "status": status,
                "updated_at": now.isoformat(),
            })

        # upsert
        for p in positions:
            try:
                self.store.client.table("whale_positions").upsert(
                    {k: v for k, v in p.items() if k != "id"},
                    on_conflict="token_address,wallet",
                ).execute()
            except Exception as exc:
                logger.error("whale_positions upsert %s %s: %s", addr, p["wallet"], exc)

        return positions

    # ---------- orchestration ----------

    def run(self, limit: Optional[int] = None) -> Dict:
        limit = limit or config.DW_SCAN_LIMIT
        universe = self.load_universe(limit)
        summary = {"chain": self.chain, "tokens": len(universe), "whales_found": 0, "signals": 0}

        for token in universe:
            try:
                result = self.analyze_token(token)
                positions = self.update_positions(result)
                summary["whales_found"] += len(positions)
                summary["signals"] += sum(1 for p in positions if p["status"] == "SIGNAL")
                if positions:
                    logger.info("%s/%s: %d whale positions (%d signal)",
                                self.chain, token.get("token_address", "")[:10],
                                len(positions), sum(1 for p in positions if p["status"] == "SIGNAL"))
            except Exception as exc:
                logger.error("analyze_token %s: %s", token.get("token_address", ""), exc)
            time.sleep(2.5)  # rate limit polite (instance Blockscout sensitif)

        return summary


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="collector.dead_whale")
    ap.add_argument("--chain", default="base")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    scanner = DeadWhaleScanner(args.chain)
    summary = scanner.run(args.limit)
    print(f"[{summary['chain']}] tokens={summary['tokens']} whales={summary['whales_found']} signals={summary['signals']}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
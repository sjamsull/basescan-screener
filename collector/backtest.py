"""Backtest Tracker — buktikan sinyal, bukan klaim.

Ikuti setiap sinyal ACTIVE sejak keluar, cross-check mcap setiap run (jam).
Metrik (semua replikable):
  - reaction: % mcap pada +1h, +4h, +24h sejak sinyal
  - best_tp  : level TP tertinggi yang tercapai (1/2/3), plus waktu capai
  - invalidation : mcap menembus invalidation_pct sebelum TP1 = bunuh trade
  - time_to_tp1  : jam dari scan sampai threshold pertama tersentuh
  - pnl (ladder 1/3): exit bertingkat TP1(1/3), TP2(1/3), TP3(1/3) + stop terakhir

Basis: perbandingan rasio terhadap observasi pertama DexScreener (entry baseline)
 — konsisten sumber, tidak mencampur mcap GMGN vs DexScreener.
"""

import argparse
import logging
import statistics
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from collector import config
from collector.scanners.dexscreener import DexScreenerClient
from collector.storage.supabase import SupabaseStorage
from collector.utils.helpers import to_float

logger = logging.getLogger(__name__)

TRACK_WINDOW_DAYS = int(__import__("os").getenv("TRACK_WINDOW_DAYS", "30"))
MAX_FUTURE_LOOKAHEAD_HOURS = 72


class BacktestTracker:
    """Melacak sinyal ACTIVE tanpa mengubah verdict lama. Append-only + state end."""

    def __init__(self, chain: Optional[str] = None):
        self.storage = SupabaseStorage() if SupabaseStorage.configured() else None
        self.dexk = DexScreenerClient()
        self.chain = chain
        if self.storage is None:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY required")

    # ---------- akses data ----------

    def active_signals(self) -> List[Dict]:
        resp = (
            self.storage.client.table("signals")
            .select("*")
            .eq("status", "ACTIVE")
            .execute()
        )
        return resp.data or []

    def tracks(self, address: str) -> List[Dict]:
        resp = (
            self.storage.client.table("signal_tracks")
            .select("*")
            .eq("token_address", address)
            .order("tracked_at")
            .execute()
        )
        return resp.data or []

    def upsert_track(self, rec: Dict) -> None:
        self.storage.client.table("signal_tracks").insert(rec).execute()

    def update_signal(self, address: str, fields: Dict) -> None:
        self.storage.client.table("signals").update(fields).eq(
            "token_address", address
        ).execute()

    def save_report(self, rec: Dict) -> None:
        self.storage.client.table("backtest_reports").insert(rec).execute()

    # ---------- engine ----------

    def _plan_levels(self, sig: Dict) -> Optional[Dict]:
        pd = sig.get("prepared_data") or {}
        plan = pd.get("plan") or {}
        tp_x = plan.get("tp_ladder_x") or []
        inv_pct = plan.get("invalidation_pct")
        if not tp_x or inv_pct is None:
            return None
        return {"tp_x": [to_float(x, 0.0) for x in tp_x], "inv_pct": to_float(inv_pct, 0.0)}

    def fetch_quote(self, chain: str, address: str) -> Optional[Dict]:
        try:
            dex_chain = config.CHAINS.get(chain)
            pair = self.dexk.get_pair(dex_chain.dexscreener_chain, address) if dex_chain else None
            if not pair:
                return None
            q = self.dexk.extract(pair)
            if q.get("error") or q.get("market_cap", 0.0) <= 0:
                return None
            return q
        except Exception:
            return None

    def simulate(self, tracks: List[Dict], plan: Dict) -> Dict:
        """Simulasi ladder 3-tranche dari riwayat track.

        Return state akhir + events.Catatan: hanya mengevaluasi bagian yang
        sudah berjalan (ACTIVE != selesai).
        """
        if not tracks:
            return {"state": "NO_DATA"}
        first = tracks[0]
        entry_mcap = to_float(first.get("mcap"), 0.0)
        if entry_mcap <= 0:
            return {"state": "NO_BASELINE", "pnl": 0.0, "time_to_tp1": None}

        tp_x = plan["tp_x"]
        inv = 1.0 + plan["inv_pct"] / 100.0  # e.g. 0.82

        best_tp = 0
        tp_at = {1: None, 2: None, 3: None}
        inv_at = None
        last = dict(tracks[-1])
        last_mcap = to_float(last.get("mcap"), 0.0)
        first_ts = first.get("tracked_at")
        res = {"state": "ACTIVE", "best_tp": 0, "time_to_tp1": None,
               "pnl": None, "entry": entry_mcap}

        # Iterasi berurutan: ketika threshold tersentuh, catat waktu.
        for tr in tracks:
            ts = tr.get("tracked_at")
            mcap = to_float(tr.get("mcap"), 0.0)
            if mcap <= 0:
                continue
            ratio = mcap / entry_mcap
            for tp_i in (3, 2, 1):
                if ratio >= tp_x[tp_i - 1] and tp_at[tp_i] is None:
                    tp_at[tp_i] = ts
                    best_tp = max(best_tp, tp_i)
                    if tp_i == 1 and res["time_to_tp1"] is None and first_ts:
                        hours = self._hours_between(first_ts, ts)
                        res["time_to_tp1"] = hours
            if inv_at is None and ratio <= inv:
                inv_at = ts

        # Realize
        exit_ratio = inv if inv_at is not None else (last_mcap / entry_mcap)
        if best_tp >= 1:
            pnl = (tp_x[0] - 1.0) / 3.0
            if best_tp >= 2:
                pnl += (tp_x[1] - 1.0) / 3.0
                if best_tp >= 3:
                    pnl += (tp_x[2] - 1.0) / 3.0
                else:
                    pnl += (exit_ratio - 1.0) / 3.0
            else:
                # TP1 hanya: sisanya dilepas pada exit (invalidation bila sudah breach)
                pnl += (exit_ratio - 1.0) * (2.0 / 3.0)
            res["pnl"] = round(pnl * 100.0, 2)
            res["state"] = "TP1" if res["pnl"] >= 0 else "TP1_LOSS"
        else:
            # Belum TP1
            if inv_at is not None:
                res["state"] = "INVALIDATED"
                res["pnl"] = round((inv - 1.0) * 100.0, 2)
                res["invalidated_at"] = inv_at
            else:
                res["state"] = "RUNNING"
                res["pnl"] = round((last_mcap / entry_mcap - 1.0) * 100.0, 2)

        res["tp_at"] = tp_at
        res["best_tp"] = best_tp
        return res

    @staticmethod
    def _hours_between(t0, t1) -> float:
        try:
            a = datetime.fromisoformat(str(t0).replace("Z", "+00:00"))
            b = datetime.fromisoformat(str(t1).replace("Z", "+00:00"))
            if a.tzinfo is None:
                a = a.replace(tzinfo=timezone.utc)
            if b.tzinfo is None:
                b = b.replace(tzinfo=timezone.utc)
            return max(0.0, (b - a).total_seconds() / 3600.0)
        except Exception:
            return 0.0

    # ---------- orchestration ----------

    def track(self, chain: str, sig: Dict) -> Dict:
        """Satu sinyal: ambil quote -> simpan track (bila belum ada utk jam ini)."""
        addr = sig.get("token_address", "")
        q = self.fetch_quote(chain, addr)
        if not q:
            return {"address": addr, "status": "NO_QUOTE"}
        self.upsert_track({
            "token_address": addr,
            "chain": chain,
            "tracked_at": datetime.now(timezone.utc).isoformat(),
            "mcap": q["market_cap"],
            "price": q.get("price_usd", 0.0),
            "liquidity": q.get("liquidity_usd", 0.0),
        })
        return {"address": addr, "status": "TRACKED", "mcap": q["market_cap"]}

    def run(self, limit: int = 500, every_run: bool = False) -> Dict:
        """Jalankan tracker sekali. `every_run`=lacak ke semua ACTIVE walaupun sdh punya riwayat."""
        sigs = self.active_signals()
        sigs = [s for s in sigs if s.get("prepared_data")]
        if limit:
            sigs = sigs[:limit]

        tracked = 0
        no_quote = 0
        states: Dict[str, int] = {}

        # 1) Simpan tick baru (hapus duplikat: skip yg sudah ditrack di jam yang sama)
        for sig in sigs:
            chain = sig.get("chain", "base")
            addr = sig["token_address"]
            last = (
                self.storage.client.table("signal_tracks")
                .select("tracked_at")
                .eq("token_address", addr)
                .order("tracked_at", desc=True)
                .limit(1)
                .execute()
            )
            if last.data and self._same_hour(last.data[0]["tracked_at"], datetime.now(timezone.utc)):
                continue  # sudah di-track di jam ini
            r = self.track(chain, sig)
            if r["status"] == "TRACKED":
                tracked += 1
            else:
                no_quote += 1

        # 2) Evaluasi ulang semua signal ACTIVE (state end)
        for sig in sigs:
            addr = sig["token_address"]
            plan = self._plan_levels(sig)
            if not plan:
                continue
            rows = (
                self.storage.client.table("signal_tracks")
                .select("*")
                .eq("token_address", addr)
                .order("tracked_at")
                .execute()
            )
            sim = self.simulate(rows.data or [], plan)
            state = sim.get("state", "NO_DATA")
            states[state] = states.get(state, 0) + 1
            best_tp = sim.get("best_tp", 0)
            # ladder tuntas: TP3 penuh, atau TP2 lalu breach invalidation -> closed
            if best_tp >= 3 or (best_tp >= 2 and state.startswith("TP1") and sim.get("pnl") is not None):
                self.update_signal(addr, {
                    "status": "COMPLETED",
                    "best_tp": best_tp,
                    "pnl_pct": sim.get("pnl"),
                    "tp1_at": (sim.get("tp_at") or {}).get(1),
                    "tp2_at": (sim.get("tp_at") or {}).get(2),
                    "tp3_at": (sim.get("tp_at") or {}).get(3),
                    "note": f"ladder done best_tp={best_tp} pnl={sim.get('pnl')}%",
                })
            # update status di signals hanya bila final (INVALIDATED / selesai / EXPIRED)
            elif state == "INVALIDATED":
                self.update_signal(addr, {
                    "status": "INVALIDATED",
                    "pnl_pct": sim.get("pnl"),
                    "exit_price": sim.get("entry") * (1.0 + sim.get("pnl", 0.0) / 100.0)
                    if sim.get("entry") else None,
                    "note": f"invalidation breach pnl={sim.get('pnl')}%",
                })
            elif state.startswith("TP1"):
                tp_at = sim.get("tp_at") or {}
                self.update_signal(addr, {
                    "best_tp": sim.get("best_tp", 1),
                    "tp1_at": tp_at.get(1),
                    "tp2_at": tp_at.get(2),
                    "tp3_at": tp_at.get(3),
                    "note": f"tp1 hit t={sim.get('time_to_tp1')}h pnl(eval)={sim.get('pnl')}%",
                })
            elif state == "RUNNING" and sim.get("time_to_tp1") is not None:
                self.update_signal(addr, {"tp1_at": (sim.get("tp_at") or {}).get(1)})

        # 3) Expire sinyal tua (>TRACK_WINDOW_DAYS) yang tak pernah tp1
        cutoff = datetime.now(timezone.utc) - timedelta(days=TRACK_WINDOW_DAYS)
        for sig in sigs:
            try:
                sig_ts = datetime.fromisoformat(str(sig.get("signal_at")).replace("Z", "+00:00"))
                if sig_ts.tzinfo is None:
                    sig_ts = sig_ts.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if sig_ts < cutoff and sig.get("status") == "ACTIVE":
                self.update_signal(sig["token_address"], {
                    "status": "EXPIRED",
                    "note": f"no tp1 within {TRACK_WINDOW_DAYS}d window",
                })
                states["EXPIRED"] = states.get("EXPIRED", 0) + 1

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "signals": len(sigs),
            "tracked_ticks": tracked,
            "no_quote": no_quote,
            "states": states,
        }
        self.save_report(report)
        logger.info("Backtest tracker done: signals=%d tracked=%d no_quote=%d states=%s",
                    len(sigs), tracked, no_quote, states)
        return report

    @staticmethod
    def _same_hour(a: str, b) -> bool:
        try:
            x = datetime.fromisoformat(str(a).replace("Z", "+00:00"))
        except Exception:
            return False
        if x.tzinfo is None:
            x = x.replace(tzinfo=timezone.utc)
        return x.year == b.year and x.month == b.month and x.day == b.day and x.hour == b.hour


def report_print(report: Dict) -> None:
    print("\n===== BACKTEST TRACKER =====")
    for k, v in report.items():
        print(f"  {k}: {v}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="collector.backtest")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--all", action="store_true", help="proses semua (default)")
    args = ap.parse_args(argv)

    kw = {}
    if not args.all:
        kw["limit"] = args.limit

    tr = BacktestTracker()
    report = tr.run(**kw)
    report_print(report)
    return 0


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    sys.exit(main())
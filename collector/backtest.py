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
        inv_mcap = to_float(plan.get("invalidation_mcap"), 0.0)
        if not tp_x or inv_pct is None:
            return None
        # Baseline harus dari mcap SAAT SINYAL (bukan track pertama — track pertama
        # bisa sudah jauh dari harga sinyal karena jeda jam antara sinyal & tracking).
        current = to_float(plan.get("current_mcap"), 0.0)
        if current <= 0 and inv_mcap > 0:
            current = inv_mcap / (1.0 + inv_pct / 100.0)
        if current <= 0:
            return None
        return {
            "tp_x": [to_float(x, 0.0) for x in tp_x],
            "inv_pct": to_float(inv_pct, 0.0),
            "entry_mcap": current,
        }

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
        entry_mcap = plan.get("entry_mcap", 0.0)
        if entry_mcap <= 0:
            return {"state": "NO_BASELINE", "pnl": 0.0, "time_to_tp1": None}

        tp_x = plan["tp_x"]
        inv = 1.0 + plan["inv_pct"] / 100.0  # e.g. 0.82

        best_tp = 0
        tp_at = {1: None, 2: None, 3: None}
        inv_at = None
        last = dict(tracks[-1])
        last_mcap = to_float(last.get("mcap"), 0.0)
        first_ts = tracks[0].get("tracked_at")
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
        except Exception:
            return 0.0
        if a.tzinfo is None:
            a = a.replace(tzinfo=timezone.utc)
        if b.tzinfo is None:
            b = b.replace(tzinfo=timezone.utc)
        return (b - a).total_seconds() / 3600.0

    # ---------- orchestration ----------

    def _apply_state(self, addr: str, sim: Dict, states: Dict) -> None:
        """Terapkan state hasil simulate ke tabel signals (dipakai run & reconcile).

        Reconcile perlu me-reset status: sinyal yang sebelumnya INVALIDATED tapi
        sim-based perjalanan masih hidup (RUNNING/TP1) harus kembali ke ACTIVE."""
        state = sim.get("state", "NO_DATA")
        states[state] = states.get(state, 0) + 1
        best_tp = sim.get("best_tp", 0)
        tt1 = sim.get("time_to_tp1")
        if best_tp >= 3 or (best_tp >= 2 and state.startswith("TP1") and sim.get("pnl") is not None):
            self.update_signal(addr, {
                "status": "COMPLETED",
                "best_tp": best_tp,
                "pnl_pct": sim.get("pnl"),
                "tp1_at": (sim.get("tp_at") or {}).get(1),
                "tp2_at": (sim.get("tp_at") or {}).get(2),
                "tp3_at": (sim.get("tp_at") or {}).get(3),
                "time_to_tp1_h": tt1,
                "note": f"ladder done best_tp={best_tp} pnl={sim.get('pnl')}%",
            })
        elif state == "INVALIDATED":
            self.update_signal(addr, {
                "status": "INVALIDATED",
                "pnl_pct": sim.get("pnl"),
                "exit_price": sim.get("entry") * (1.0 + sim.get("pnl", 0.0) / 100.0)
                if sim.get("entry") else None,
                "time_to_tp1_h": tt1,
                "note": f"invalidation breach pnl={sim.get('pnl')}%",
            })
        elif state.startswith("TP1"):
            tp_at = sim.get("tp_at") or {}
            self.update_signal(addr, {
                "status": "ACTIVE",
                "best_tp": sim.get("best_tp", 1),
                "tp1_at": tp_at.get(1),
                "tp2_at": tp_at.get(2),
                "tp3_at": tp_at.get(3),
                "time_to_tp1_h": tt1,
                "note": f"tp1 hit t={tt1}h pnl(eval)={sim.get('pnl')}%",
            })
        elif state == "RUNNING":
            fields = {}
            if sim.get("time_to_tp1") is not None:
                fields["tp1_at"] = (sim.get("tp_at") or {}).get(1)
            if sim.get("best_tp", 0) > 0:
                fields["best_tp"] = sim.get("best_tp")
                fields["note"] = f"tp hit best={sim.get('best_tp')} (reconcile)"
            self.update_signal(addr, {"status": "ACTIVE", "time_to_tp1_h": tt1, **fields})

    def _is_expired(self, sig: Dict) -> bool:
        """True bila sinyal > TRACK_WINDOW_DAYS tanpa tp1 (boleh di-expire)."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=TRACK_WINDOW_DAYS)
        try:
            sig_ts = datetime.fromisoformat(str(sig.get("signal_at")).replace("Z", "+00:00"))
            if sig_ts.tzinfo is None:
                sig_ts = sig_ts.replace(tzinfo=timezone.utc)
        except Exception:
            return False
        return sig_ts < cutoff

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
            self._apply_state(addr, sim, states)

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
            "metrics": self.aggregate_metrics(),
        }
        self.save_report(report)
        logger.info("Backtest tracker done: signals=%d tracked=%d no_quote=%d states=%s",
                    len(sigs), tracked, no_quote, states)
        return report

    def reconcile(self, limit: int = 0) -> Dict:
        """Re-evaluasi SEMUA sinyal (bukan hanya ACTIVE) dengan logika terbaru.

        Koreksi status historis yang ditulis sebelum fix baseline (mis. INVALIDATED
        palsu akibat baseline track-pertama). Tidak menambah track baru; hanya
        mengulang simulasi dari riwayat track yang ada.
        """
        resp = (
            self.storage.client.table("signals")
            .select("*")
            .order("signal_at", desc=True)
            .execute()
        )
        all_rows = resp.data or []
        if limit:
            all_rows = all_rows[:limit]

        states: Dict[str, int] = {}
        corrected = 0
        no_plan = 0
        no_tracks = 0

        for sig in all_rows:
            addr = sig["token_address"]
            plan = self._plan_levels(sig)
            if not plan:
                no_plan += 1
                continue
            rows = (
                self.storage.client.table("signal_tracks")
                .select("*")
                .eq("token_address", addr)
                .order("tracked_at")
                .execute()
            )
            tracks = rows.data or []
            if not tracks:
                no_tracks += 1
                continue
            sim = self.simulate(tracks, plan)
            old_status = sig.get("status")
            self._apply_state(addr, sim, states)
            new_status = self.storage.client.table("signals").select("status").eq(
                "token_address", addr
            ).execute().data or []
            if new_status and new_status[0].get("status") != old_status:
                corrected += 1

        # Expire sinyal tua yang masih ACTIVE dan tak pernah tp1
        for sig in all_rows:
            if sig.get("status") == "ACTIVE" and self._is_expired(sig):
                self.update_signal(sig["token_address"], {
                    "status": "EXPIRED",
                    "note": f"no tp1 within {TRACK_WINDOW_DAYS}d window (reconciled)",
                })
                states["EXPIRED"] = states.get("EXPIRED", 0) + 1
                corrected += 1

        # Backfill time_to_tp1_h untuk sinyal ber-tp1 yang kolomnya kosong (mis.
        # yang plan-nya sudah ditimpa scan baru sehingga tidak lewat jalur simulate).
        backfilled = 0
        for sig in all_rows:
            addr = sig["token_address"]
            if sig.get("time_to_tp1_h") is not None:
                continue
            tp1 = sig.get("tp1_at")
            if not tp1:
                continue
            rows = (
                self.storage.client.table("signal_tracks")
                .select("tracked_at, mcap")
                .eq("token_address", addr)
                .order("tracked_at")
                .execute()
            )
            tracks = rows.data or []
            if not tracks:
                continue
            first_ts = tracks[0].get("tracked_at")
            h = self._hours_between(first_ts, tp1)
            if h is not None and h >= 0:
                self.update_signal(addr, {"time_to_tp1_h": round(h, 2)})
                backfilled += 1

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "signals": len(all_rows),
            "tracked_ticks": 0,
            "no_quote": 0,
            "states": {**states, "reconciled_corrected": corrected,
                       "backfilled_tt1": backfilled,
                       "no_plan": no_plan, "no_tracks": no_tracks},
            "metrics": self.aggregate_metrics(),
        }
        self.save_report(report)
        logger.info("Reconcile done: signals=%d corrected=%d states=%s",
                    len(all_rows), corrected, states)
        return report

    def aggregate_metrics(self) -> Dict:
        """Metrik agregat dari signals yang sudah diverifikasi tracker (honest stats)."""
        rows = self.storage.client.table("signals").select(
            "token_address,chain,verdict,best_tp,tp1_at,tp2_at,tp3_at,signal_at,pnl_pct,status,time_to_tp1_h"
        ).execute().data or []

        # hanya sinyal dengan plan (punya tp_ladder) & data tracker
        valuable = [r for r in rows
                    if r.get("status") in ("COMPLETED", "INVALIDATED", "EXPIRED")
                    or r.get("best_tp", 0) > 0 or r.get("tp1_at")]

        resolved = [r for r in rows if r.get("status") in ("COMPLETED", "INVALIDATED", "EXPIRED")]
        m = {
            "evaluated": len(valuable),
            "tp1_hits": sum(1 for r in rows if r.get("tp1_at")),
            "tp2_hits": sum(1 for r in rows if r.get("tp2_at")),
            "tp3_hits": sum(1 for r in rows if r.get("tp3_at")),
            "invalidated": sum(1 for r in rows if r.get("status") == "INVALIDATED"),
            "completed": sum(1 for r in rows if r.get("status") == "COMPLETED"),
            "expired": sum(1 for r in rows if r.get("status") == "EXPIRED"),
        }
        # time-to-tp1: prioritas kolom time_to_tp1_h (ditulis simulate, konsisten),
        # fallback hitung ulang hanya untuk baris lama yang belum punya kolom.
        tt1 = []
        tt1_anomalies = 0
        for r in rows:
            h = to_float(r.get("time_to_tp1_h"), None)
            if h is not None:
                if h >= 0:
                    tt1.append(h)
                else:
                    tt1_anomalies += 1
                continue
            s, t = r.get("signal_at"), r.get("tp1_at")
            if s and t:
                h = self._hours_between(s, t)
                if h >= 0:
                    tt1.append(h)
                else:
                    tt1_anomalies += 1
        m["avg_time_to_tp1_h"] = round(statistics.mean(tt1), 2) if tt1 else None
        m["n_time_to_tp1"] = len(tt1)
        m["time_to_tp1_anomalies"] = tt1_anomalies

        # hit rate tp1 terhadap yang sudah 'resolved/moving' (sudah ada outcome atau tp1)
        denom = len(valuable)
        m["tp1_hit_rate"] = round(m["tp1_hits"] / denom, 3) if denom else None
        m["invalidation_rate"] = round(m["invalidated"] / denom, 3) if denom else None
        return m

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
    ap.add_argument("--reconcile", action="store_true",
                    help="re-evaluasi SEMUA sinyal historis & koreksi status (tanpa track baru)")
    args = ap.parse_args(argv)

    tr = BacktestTracker()

    if args.reconcile:
        kw = {"limit": 0 if args.all else args.limit}
        report = tr.reconcile(**kw)
        report_print(report)
        return 0

    kw = {}
    if not args.all:
        kw["limit"] = args.limit

    report = tr.run(**kw)
    report_print(report)
    return 0


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    sys.exit(main())
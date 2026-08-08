"""Populate dead_token_universe — pilih token mati (umur >= DW_MIN_TOKEN_AGE_DAYS,
volume 24h rendah) dari signals yang sudah ada di Supabase.

Output: baris di dead_token_universe yang dipakai DeadWhaleScanner.load_universe.
"""

import json
import logging
import os
from datetime import datetime, timezone

from collector import config
from collector.scanners.blockscout import BlockscoutClient
from collector.scanners.gmgn import GMGNClient
from collector.storage.supabase import SupabaseStorage

logger = logging.getLogger(__name__)


class UniversePopulator:
    def __init__(self, chain: str = "base"):
        self.chain = chain
        self.bs = BlockscoutClient()
        self.gmgn = GMGNClient(chain)
        self.store = SupabaseStorage() if SupabaseStorage.configured() else None
        if self.store is None:
            raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY diperlukan")

    def _seed_candidates(self, limit: int):
        """Cari token mati dari Blockscout v2 /api/v2/tokens (list semua token)
        — filter volume 24h rendah + holders cukup. Lebih baik daripada seed
        hanya dari signals (yang biasanya trending)."""
        tokens = self.bs.list_tokens(self.chain, pages=config.DW_UNIVERSE_PAGES, page_size=50)
        out = []
        for t in tokens:
            addr = t.get("address", "")
            if not addr:
                continue
            vol = float(t.get("volume_24h") or 0.0)
            holders = int(t.get("holders") or 0)
            mcap = float(t.get("market_cap") or 0.0)
            er = float(t.get("exchange_rate") or 0.0)
            if self._exclude(t.get("symbol"), t.get("name")):
                continue
            is_dead = (vol <= config.DW_DEAD_VOLUME_USD) and holders > 100 and mcap > 0 and er > 0
            if not is_dead:
                continue
            out.append({
                "token_address": addr,
                "symbol": t.get("symbol"),
                "decimals": int(t.get("decimals") or 18),
                "total_supply": str(t.get("total_supply") or 0),
                "market_cap": mcap,
                "volume_24h": vol,
                "holders": holders,
                "exchange_rate": er,
            })
        return out

    def _seed_from_signals(self, limit: int):
        """Fallback: token yang sudah ada di signals (Supabase)."""
        rows = self.store.client.table("signals").select("token_address,chain").eq("chain", self.chain).order("signal_at", desc=True).limit(limit).execute()
        return [{"token_address": r.get("token_address", "")} for r in (rows.data or [])]

    def _exclude(self, symbol, name) -> bool:
        """Buang stable/wrapped/major & nama scam dari universe."""
        sym = (symbol or "").upper()
        nm = (name or "").upper()
        for p in config.DW_EXCLUDE_SYMBOL_PARTS:
            if p and p.upper() in sym:
                return True
        for p in config.DW_EXCLUDE_NAME_PARTS:
            if p and p.upper() in nm:
                return True
        return False

    def _ensure_schema(self):
        """Cek kolom risk_flags & security_json ada (perlu migration di Supabase SQL Editor)."""
        try:
            self.store.client.table("dead_token_universe").select("risk_flags").limit(1).execute()
        except Exception:
            logger.error("Kolom risk_flags BELUM ada di dead_token_universe — jalankan di Supabase SQL Editor:\n"
                         'ALTER TABLE dead_token_universe ADD COLUMN IF NOT EXISTS risk_flags text;\n'
                         'Untuk membuatnya otomatis, isi .env SUPABASE_SERVICE_KEY yang punya akses DDL.')
        try:
            self.store.client.table("dead_token_universe").select("security_json").limit(1).execute()
        except Exception:
            logger.error("Kolom security_json BELUM ada di dead_token_universe — jalankan di Supabase SQL Editor:\n"
                         'ALTER TABLE dead_token_universe ADD COLUMN IF NOT EXISTS security_json text;\n')

    def _enrich(self, addr: str):
        """Isi field yang Dune tidak sediakan (holders, market_cap, exchange_rate)."""
        info = self.bs.token_info(self.chain, addr)
        if not info:
            return None
        return {
            "symbol": info.get("symbol"),
            "decimals": info.get("decimals", 18),
            "total_supply": str(info.get("total_supply") or 0),
            "market_cap": float(info.get("market_cap") or 0.0),
            "volume_24h": float(info.get("volume_24h") or 0.0),
            "holders": int(info.get("holders") or 0),
            "exchange_rate": float(info.get("exchange_rate") or 0.0),
        }

    def _gmgn_gate(self, addr: str):
        """GMGN security gate — return (ok: bool, flags: list[str], sec: dict)."""
        try:
            sec = self.gmgn.token_security(addr)
        except Exception as exc:
            logger.debug("GMGN gate %s skip-call: %s", addr, exc)
            return True, [], {}
        flags: list[str] = []
        if sec.get("is_honeypot") and config.DW_GMGN_SKIP_HONEYPOT:
            flags.append("honeypot")
        if sec.get("is_show_alert") and config.DW_GMGN_SKIP_ALERT:
            flags.append("gmgn_alert")
        if sec.get("is_blacklist") not in (None, False, 0, "", -1):
            flags.append("blacklist")
        top10 = float(sec.get("top_10_holder_rate") or 0.0)
        if top10 > config.DW_GMGN_TOP10_MAX:
            flags.append(f"top10={top10:.0%}")
        rug = float(sec.get("rug_ratio") or 0.0)
        if rug > config.DW_GMGN_RUG_MAX:
            flags.append(f"rug={rug:.0%}")
        tax = max(float(sec.get("buy_tax") or 0.0), float(sec.get("sell_tax") or 0.0))
        if tax > config.DW_GMGN_MAX_TAX:
            flags.append(f"tax={tax:.0%}")
        return (not flags), flags, sec

    def run(self, limit: int = 50, use_dune: bool = True) -> int:
        now = datetime.now(timezone.utc)
        self._ensure_schema()
        candidates = []
        if use_dune and os.getenv("DUNE_API_KEY") and os.getenv("DUNE_DEAD_TOKENS_QUERY_ID"):
            try:
                from collector.scanners.dune import run_query
                rows = run_query(int(os.getenv("DUNE_DEAD_TOKENS_QUERY_ID")), os.getenv("DUNE_API_KEY"))
                # Dune hanya suplai daftar alamat; simbol/name di-enrich nanti di loop
                candidates += rows
            except Exception as exc:
                logger.warning("Dune universe fetch: %s", exc)
        # Blockscout /api/v2/tokens = sumber utama (sudah ada volume_24h, holders, exchange_rate)
        candidates += self._seed_candidates(limit)
        # fallback: signals (hanya bila list_tokens kosong)
        if not candidates:
            candidates += self._seed_from_signals(limit)

        seen = set()
        flagged = []
        added = 0
        for c in candidates:
            addr = (c.get("token_address") or "").lower()
            if not addr or addr in seen:
                continue
            seen.add(addr)
            try:
                vol = float(c.get("volume_24h") or 0.0)
                holders = int(c.get("holders") or 0)
                mcap = float(c.get("market_cap") or 0.0)
                er = float(c.get("exchange_rate") or 0.0)
                # Dune hanya menyuplai token_address/age/txns — lengkapi via Blockscout
                if mcap <= 0 or er <= 0 or holders <= 0:
                    enr = self._enrich(addr)
                    if enr:
                        c = {**c, **enr}
                        vol = enr["volume_24h"]
                        holders = enr["holders"]
                        mcap = enr["market_cap"]
                        er = enr["exchange_rate"]
                if self._exclude(c.get("symbol"), c.get("name")):
                    continue
                if mcap > config.DW_MAX_MARKET_CAP_USD:
                    continue
                # is_dead: volume rendah + holder cukup + token valid (exchange_rate>0 & mcap>0)
                if vol > config.DW_DEAD_VOLUME_USD or holders <= 100 or mcap <= 0 or er <= 0:
                    continue
                # GMGN risk gate: buang honeypot/rug/alert/top10-konsentrasi
                ok_gate, flags, gsec = self._gmgn_gate(addr)
                # MC otoritatif dari GMGN (bukan Blockscout/DexScreener per-pair).
                # Fallback ke mcap Blockscout bila get_token_info gagal.
                gmgn_mcap = mcap
                created_ts = 0
                try:
                    gi = self.gmgn.get_token_info(addr)
                    if gi:
                        if float(gi.get("market_cap") or 0.0) > 0:
                            gmgn_mcap = float(gi["market_cap"])
                        created_ts = int(gi.get("creation_timestamp") or 0)
                except Exception as exc:
                    logger.debug("gmgn info %s: %s", addr[:10], exc)
                # Filter umur: skip token terlalu muda (banyak rug-pull di
                # robinhood). 0 = umur tidak diketahui -> biarkan lewat (bukan tolak).
                if created_ts > 0:
                    age_days = (now.timestamp() - created_ts) / 86400.0
                    if age_days < config.UNIVERSE_MIN_AGE_DAYS:
                        logger.info("skip muda %s age=%.1fd (< %dd)", addr[:10], age_days, config.UNIVERSE_MIN_AGE_DAYS)
                        continue
                record = {
                    "token_address": addr,
                    "chain": self.chain,
                    "symbol": c.get("symbol"),
                    "decimals": int(c.get("decimals") or 18),
                    "total_supply": str(c.get("total_supply") or 0),
                    "market_cap": gmgn_mcap,
                    "volume_24h": vol,
                    "holders": holders,
                    "created_at": datetime.fromtimestamp(created_ts, timezone.utc).isoformat() if created_ts > 0 else None,
                    "last_seen": now.isoformat(),
                    "security_json": json.dumps(gsec, ensure_ascii=False, default=str) if gsec else None,
                }
                if ok_gate:
                    record["risk_flags"] = None
                    self.store.client.table("dead_token_universe").upsert(
                        {k: v for k, v in record.items() if k != "id"},
                        on_conflict="token_address",
                    ).execute()
                    added += 1
                else:
                    flagged.append({"record": record, "flags": flags})
            except Exception as exc:
                logger.warning("populate %s: %s", addr, exc)
        # Fallback: jika tidak ada token yang lolos GMGN gate, simpan token
        # flagged (risky) supaya dashboard tetap punya data — diberi tanda risk_flags.
        if added == 0 and flagged:
            logger.warning("universe kosong dari token aman — simpan %d token flagged (risky)", len(flagged))
            for item in flagged[: max(5, limit)]:
                rec = dict(item["record"])
                rec["risk_flags"] = ",".join(item["flags"])
                try:
                    self.store.client.table("dead_token_universe").upsert(
                        {k: v for k, v in rec.items() if k != "id"},
                        on_conflict="token_address",
                    ).execute()
                    added += 1
                except Exception as exc:
                    logger.warning("populate flagged %s: %s", rec["token_address"], exc)
        logger.info("populate_universe %s: added=%d / seed=%d", self.chain, added, len(candidates))
        return added


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="collector.populate_universe")
    ap.add_argument("--chain", default="base")
    ap.add_argument("--limit", type=int, default=100)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    pop = UniversePopulator(args.chain)
    n = pop.run(args.limit)
    print(f"[{args.chain}] universe tokens added/updated={n}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

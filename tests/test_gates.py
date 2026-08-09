"""Smoke tests untuk SecurityGate (layer 1 anti-rug) & GMGN risk gate
(_gmgn_gate di UniversePopulator) — tanpa API call."""

from collector.processors.security import SecurityGate
from collector.processors.populate_universe import UniversePopulator


def token(**over):
    t = {
        "address": "0x" + "a" * 40,
        "creationTimestamp": "2024-01-01T00:00:00Z",
        "liquidity": 100_000.0,
        "top_10_holder_rate": 0.40,
        "buy_tax": 0.0,
        "sell_tax": 0.0,
    }
    t.update(over)
    return t


class TestSecurityGate:
    def test_token_bersih_lolos(self):
        r = SecurityGate().check(token(), None)
        assert r["passed"] is True

    def test_no_address_reject(self):
        r = SecurityGate().check(token(address=""), None)
        assert r["passed"] is False
        assert r["reason"] == "no_address"

    def test_umur_kurang_dari_4_hari_reject(self):
        from datetime import datetime, timezone, timedelta
        baru = datetime.now(timezone.utc) - timedelta(hours=48)
        r = SecurityGate().check(token(creationTimestamp=baru.isoformat()), None)
        assert r["passed"] is False
        assert "age" in r["reason"]

    def test_liq_di_bawah_100k_reject(self):
        r = SecurityGate().check(token(liquidity=50_000), None)
        assert r["passed"] is False
        assert "liq" in r["reason"]

    def test_liq_di_atas_850k_reject(self):
        # Token mayor (LINK/Chainlink liquidity ~$500M) harus ditolak
        r = SecurityGate().check(token(liquidity=1_000_000), None)
        assert r["passed"] is False
        assert "liq>" in r["reason"]

    def test_liq_dalam_range_pass(self):
        r = SecurityGate().check(token(liquidity=250_000), None)
        assert r["passed"] is True

    def test_top10_konsentrasi_reject(self):
        r = SecurityGate().check(token(top_10_holder_rate=0.90), None)
        assert r["passed"] is False
        assert "top10" in r["reason"]

    def test_tax_tinggi_reject(self):
        r = SecurityGate().check(token(sell_tax=15.0), None)
        assert r["passed"] is False
        assert "tax" in r["reason"]

    def test_honeypot_flag_reject(self):
        r = SecurityGate().check(token(honeypot="1"), None)
        assert r["passed"] is False
        assert "honeypot" in r["reason"]

    def test_dead_whale_mode_liq_floor_1k(self):
        gate = SecurityGate(mode="dead_whale")
        assert gate.min_liq == 1000
        r = gate.check(token(liquidity=1_500), None)
        assert r["passed"] is True

    def test_liq_dual_source_pair(self):
        r = SecurityGate().check(token(liquidity=0), {"liquidity_usd": 250_000})
        assert r["passed"] is True


class FakeGMGN:
    def __init__(self, sec):
        self._sec = sec

    def token_security(self, addr):
        return self._sec


def make_populator(gmgn_sec):
    """Buat UniversePopulator tanpa __init__ (agar tidak butuh Supabase/API key)."""
    pop = object.__new__(UniversePopulator)
    pop.chain = "base"
    pop.gmgn = FakeGMGN(gmgn_sec)
    return pop


class TestGMGNGate:
    def test_honeypot_ditolak(self):
        pop = make_populator({"is_honeypot": True})
        ok, flags, sec = pop._gmgn_gate("0xabc")
        assert ok is False
        assert "honeypot" in flags

    def test_gmgn_alert_ditolak(self):
        pop = make_populator({"is_show_alert": True})
        ok, flags, _ = pop._gmgn_gate("0xabc")
        assert ok is False
        assert "gmgn_alert" in flags

    def test_blacklist_ditolak(self):
        pop = make_populator({"is_blacklist": 1})
        ok, flags, _ = pop._gmgn_gate("0xabc")
        assert ok is False
        assert "blacklist" in flags

    def test_top10_di_atas_50_persen_ditolak(self):
        pop = make_populator({"top_10_holder_rate": 0.60})
        ok, flags, _ = pop._gmgn_gate("0xabc")
        assert ok is False
        assert any(f.startswith("top10") for f in flags)

    def test_rug_ratio_tinggi_ditolak(self):
        pop = make_populator({"rug_ratio": 0.50})
        ok, flags, _ = pop._gmgn_gate("0xabc")
        assert ok is False
        assert any(f.startswith("rug") for f in flags)

    def test_tax_di_atas_10_persen_ditolak(self):
        pop = make_populator({"buy_tax": 0.15, "sell_tax": 0.0})
        ok, flags, _ = pop._gmgn_gate("0xabc")
        assert ok is False
        assert any(f.startswith("tax") for f in flags)

    def test_token_bersih_lolos(self):
        pop = make_populator(
            {"is_honeypot": False, "is_show_alert": False, "is_blacklist": None,
             "top_10_holder_rate": 0.30, "rug_ratio": 0.10,
             "buy_tax": 0.0, "sell_tax": 0.0}
        )
        ok, flags, sec = pop._gmgn_gate("0xabc")
        assert ok is True
        assert flags == []
        assert sec["rug_ratio"] == 0.10

    def test_api_error_lolos_lalu_tidak_simpan_security(self):
        class Boom(FakeGMGN):
            def __init__(self):
                pass

            def token_security(self, addr):
                raise RuntimeError("rate limited")

        pop = object.__new__(UniversePopulator)
        pop.chain = "base"
        pop.gmgn = Boom()
        ok, flags, sec = pop._gmgn_gate("0xabc")
        assert ok is True
        assert sec == {}
"""Smoke & regression tests for RiskEngine (lihat docstring risk.py)."""

from collector.processors.risk import RiskEngine

engine = RiskEngine()


def token(**over):
    t = {
        "liquidity": 200_000.0,
        "top_10_holder_rate": 0.50,
        "top10_holder_rate": 0.0,
        "volume_24h": 100_000.0,
        "gecko": {"total_volume": 50_000.0},
        "gmgn": {"volume_24h": 100_000.0, "swaps": 100, "price_usd": 1.0},
        "bundler_rate": 0.0,
        "entrapment_ratio": 0.0,
        "rug_ratio": 0.0,
        "same_second_flags": [],
    }
    t.update(over)
    return t


def sec(**over):
    s = {
        "is_honeypot": False,
        "owner_renounced": True,
        "sell_tax": 0.0,
        "buy_tax": 0.0,
    }
    s.update(over)
    return s


class TestHoneypot:
    def test_honeypot_reject_total(self):
        r = engine.calculate(token(), sec(is_honeypot=True))
        assert r["score"] == 100.0
        assert r["flags"] == ["honeypot"]

    def test_honeypot_shortcircuit_ignores_others(self):
        r = engine.calculate(token(volume_24h=0, gecko={"total_volume": 0}), sec(is_honeypot=True))
        assert r["score"] == 100.0
        assert len(r["flags"]) == 1


class TestTaxTop10Owner:
    def test_sell_tax_11pct_kronis(self):
        r = engine.calculate(token(), sec(owner_renounced=True, sell_tax=11.0))
        assert r["score"] >= 25
        assert any("sell_tax" in f for f in r["flags"])

    def test_sell_tax_6pct_ringan(self):
        r = engine.calculate(token(), sec(sell_tax=6.0))
        assert r["score"] >= 10 and r["score"] < 25

    def test_owner_tidak_renounce(self):
        r = engine.calculate(token(), sec(owner_renounced=False))
        assert any(f == "owner_not_renounced" for f in r["flags"])
        assert r["score"] >= 10

    def test_top10_85_persen(self):
        r = engine.calculate(token(top_10_holder_rate=0.85), sec())
        assert any("top10>80%" in f for f in r["flags"])

    def test_liquidity_di_bawah_50k(self):
        r = engine.calculate(token(liquidity=49_999), sec())
        assert any("liq<50k" in f for f in r["flags"])


class TestLaunchpadStructure:
    def test_bundler_high(self):
        r = engine.calculate(token(bundler_rate=0.7), sec())
        assert any("bundler=70%" in f for f in r["flags"])

    def test_entrapment_ekor(self):
        r = engine.calculate(token(entrapment_ratio=0.98), sec())
        assert any("entrapment" in f for f in r["flags"])

    def test_dev_hold(self):
        r = engine.calculate(token(dev_team_hold_rate=0.3), sec())
        assert any("dev_hold" in f for f in r["flags"])

    def test_sniper_hold_alias(self):
        r = engine.calculate(token(top70_sniper_hold_rate=0.35, top70_insider_hold_rate=None), sec())
        assert any("sniper_hold" in f for f in r["flags"])

    def test_rug_tail(self):
        r = engine.calculate(token(rug_ratio=0.8), sec())
        assert any("rug_ratio=80%" in f for f in r["flags"])

    def test_creator_close_flag_ringan(self):
        r = engine.calculate(token(creator_close=True), sec())
        assert "creator_close" in r["flags"]
        assert r["score"] < 10


class TestWashTrading:
    def test_volume_ratio_3x(self):
        r = engine.calculate(token(volume_24h=300_000.0, gecko={"total_volume": 100_000.0}), sec())
        assert any("vol_ratio" in f for f in r["flags"])

    def test_volume_ratio_normal_tidak_kena(self):
        r = engine.calculate(token(volume_24h=100_000.0, gecko={"total_volume": 80_000.0}), sec())
        assert not any("vol_ratio" in f for f in r["flags"])

    def test_gmgn_avg_trade_kecil_dan_sepi(self):
        # avg_trade = volume/swaps = 10000/240 ≈ $41.7 (< $50), 10 swap/jam
        r = engine.calculate(token(gmgn={"volume_24h": 10_000.0, "swaps": 240, "price_usd": 1.0}), sec())
        assert any("avg_trade" in f for f in r["flags"])

    def test_same_second_capped_60(self):
        r = engine.calculate(token(same_second_flags=["a", "b", "c", "d", "e"]), sec())
        assert any("same_second_x5" in f for f in r["flags"])
        assert r["score"] <= 60.0


class TestBounds:
    def test_never_above_100(self):
        r = engine.calculate(
            token(
                liquidity=49_999.0,
                bundler_rate=0.9,
                rug_ratio=0.9,
                dev_team_hold_rate=0.9,
                same_second_flags=["a", "b", "c"],
            ),
            sec(owner_renounced=False, sell_tax=20.0),
        )
        assert 0 <= r["score"] <= 100.0

    def test_clean_token_low_risk(self):
        r = engine.calculate(token(), sec())
        assert r["score"] == 0.0
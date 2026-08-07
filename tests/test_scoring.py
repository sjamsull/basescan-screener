"""Smoke & regression tests for TokenScorer.

Aturan sakral yang harus bertahan (lihat docstring scoring.py):
- Bonus ACCUMULATION HANYA jika ada pembeli nyata (gradual/cluster/single-entry)
  DAN price_change_1h < 20%. "Aman tapi datar" = skor tetap rendah.
- Whale cluster (bobot 2x) hanya dengan konfirmasi flow beli nyata.
- Skor selalu ter-clamp 0..100.
"""

import pytest

from collector.processors.scoring import TokenScorer, BUYER_PATTERNS

scorer = TokenScorer()


def base_token(**over):
    t = {
        "big_holder_count": 0,
        "whale_count": 0,
        "buyer_pattern": "",
        "holder_count_trend": "",
        "price_change_1h": 0.0,
        "price_change_24h": 0.0,
        "kline_trend_pct": 0.0,
        "kline_vol_ratio": 1.0,
        "liquidity": 100_000.0,
        "market_cap": 1_000_000.0,
        "social_mention_count": 0.0,
    }
    t.update(over)
    return t


class TestAccumulationBonus:
    def test_aman_tapi_datar_tidak_bonus_besar(self):
        """Tren akumulasi tanpa pembeli nyata (pattern kosong) -> hanya bonus kecil (5)."""
        t = base_token(holder_count_trend="accumulation", buyer_pattern="", price_change_1h=1.0)
        r = scorer.calculate_alpha(t)
        assert r["breakdown"]["accumulation_phase"] == 5.0
        assert r["alpha"] < 80

    def test_pembeli_nyata_plus_harga_datar_dapat_bonus_penuh(self):
        t = base_token(
            holder_count_trend="accumulation", buyer_pattern="gradual", price_change_1h=1.0
        )
        r = scorer.calculate_alpha(t)
        assert r["breakdown"]["accumulation_phase"] == 20.0 * scorer.WEIGHTS["accumulation_phase"]

    def test_harga_meledak_20pct_keatas_gagal_syarat(self):
        """price_1h >= 20 -> acc_eligible False -> TANPA bonus akumulasi sama sekali."""
        t = base_token(
            holder_count_trend="accumulation", buyer_pattern="gradual", price_change_1h=20.0
        )
        r = scorer.calculate_alpha(t)
        assert r["breakdown"]["accumulation_phase"] == 0.0

    def test_semua_pola_pembeli_valid(self):
        for pat in BUYER_PATTERNS:
            t = base_token(holder_count_trend="accumulation", buyer_pattern=pat, price_change_1h=0.5)
            assert scorer.calculate_alpha(t)["breakdown"]["accumulation_phase"] == 40.0


class TestWhaleCluster:
    def test_whale_besar_tanpa_pola_cluster_tidak_bonus(self):
        t = base_token(big_holder_count=80, buyer_pattern="gradual")
        assert scorer.calculate_alpha(t)["breakdown"]["cluster_whale"] == 0.0

    def test_whale_50_plus_pola_cluster_bonus(self):
        t = base_token(big_holder_count=50, buyer_pattern="cluster")
        assert scorer.calculate_alpha(t)["breakdown"]["cluster_whale"] > 0

    def test_whale_49_belum_bonus(self):
        t = base_token(big_holder_count=49, buyer_pattern="cluster")
        assert scorer.calculate_alpha(t)["breakdown"]["cluster_whale"] == 0.0


class TestMomentum:
    def test_kline_sehat_naik(self):
        m, d = scorer.calculate_momentum(base_token(kline_trend_pct=20.0, kline_vol_ratio=2.0))
        assert m > 70

    def test_kline_overheated_120_minus(self):
        m, d = scorer.calculate_momentum(base_token(kline_trend_pct=150.0))
        assert d["trend_band"] == "overheated"
        assert m < 50

    def test_pump_1h_warning(self):
        _, d = scorer.calculate_momentum(base_token(price_change_1h=25.0))
        assert d.get("pump_warning") is True

    def test_bleeding(self):
        _, d = scorer.calculate_momentum(base_token(kline_trend_pct=-30.0))
        assert d["trend_band"] == "bleeding"


class TestLiquiditySize:
    def test_liquidity_tiers(self):
        assert scorer.calculate_alpha(base_token(liquidity=300_000))["breakdown"]["liquidity_health"] == 10.0
        assert scorer.calculate_alpha(base_token(liquidity=150_000))["breakdown"]["liquidity_health"] == 7.0
        assert scorer.calculate_alpha(base_token(liquidity=60_000))["breakdown"]["liquidity_health"] == 4.0
        assert scorer.calculate_alpha(base_token(liquidity=10_000))["breakdown"]["liquidity_health"] == 2.0

    def test_mcap_sweet_spot(self):
        assert scorer.calculate_alpha(base_token(market_cap=1_000_000))["breakdown"]["size_window"] == 12.0
        assert scorer.calculate_alpha(base_token(market_cap=5_000_000))["breakdown"]["size_window"] == 6.0
        assert scorer.calculate_alpha(base_token(market_cap=100_000_000))["breakdown"]["size_window"] == -12.0


class TestBoundsAndVerdict:
    def test_alpha_selalu_di_0_100(self):
        for mc in (1e6, 1e8, 2e5, 5e7):
            r = scorer.calculate_alpha(base_token(market_cap=mc))
            assert 0.0 <= r["alpha"] <= 100.0

    def test_verdict_tiers(self):
        assert scorer.get_verdict(85, 10) == "STRONG BUY"
        assert scorer.get_verdict(70, 5) == "BUY"
        assert scorer.get_verdict(50, 10) == "NEUTRAL"
        assert scorer.get_verdict(10, 90) == "CAUTION"

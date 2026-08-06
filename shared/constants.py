"""Konstanta bersama untuk collector & API. Tanpa import berat."""

CHAIN_WEIGHTS = {
    "base": 1.15,       # likuiditas EVM solid, ekosistem meme aktif
    "eth": 1.05,
    "sol": 1.0,         # baseline
    "bsc": 0.85,
    "robinhood": 1.1,
}

VERDICT_ORDER = ["STRONG BUY", "BUY", "NEUTRAL", "CAUTION"]

SCAN_MODES = ["accumulation", "deadwhale"]

VERDICT_ALPHA = {
    "STRONG BUY": 80,
    "BUY": 60,
    "NEUTRAL": 40,
    "CAUTION": 0,
}
from dataclasses import dataclass
from typing import Dict, List
import os
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class ChainConfig:
    name: str
    gmgn_id: str
    goplus_id: int
    gecko_network: str
    dexscreener_chain: str
    explorer_chainid: int  # None = explorer free tier tidak tersedia, di-skip


CHAINS: Dict[str, ChainConfig] = {
    "base": ChainConfig("Base", "base", 8453, "base", "base", 8453),
    "robinhood": ChainConfig("Robinhood", "robinhood", 4663, "robinhood", "base", None),
    "arbitrum": ChainConfig("Arbitrum", "arbitrum", 42161, "arbitrum", "arbitrum", 42161),
    "eth": ChainConfig("Ethereum", "eth", 1, "ethereum", "ethereum", 1),
    "bsc": ChainConfig("BSC", "bsc", 56, "bsc", "bsc", 56),
    "sol": ChainConfig("Solana", "sol", 101, "solana", "solana", None),
}

# Explorer free tier (verified 2026): hanya chainid 1 (ETH) & 42161 (Arbitrum).
# chainid 8453 (Base) & 56 (BSC) ditolak free tier — di-skip sampai plan di-upgrade.
# Upgrade path: EXPLORER_CHAINIDS=1,42161,8453,56 untuk mengaktifkan chain lain.
_supported = {1, 42161}
_explorer_env = os.getenv("EXPLORER_CHAINIDS", "").strip()
if _explorer_env:
    _supported = {int(x.strip()) for x in _explorer_env.split(",") if x.strip()}
for _cfg in CHAINS.values():
    if _cfg.explorer_chainid not in _supported:
        object.__setattr__(_cfg, "explorer_chainid", None)

DEFAULT_CHAIN = os.getenv("DEFAULT_CHAIN", "base")


def chain_order() -> List[str]:
    order = os.getenv("CHAIN_WEIGHT_ORDER", "").strip()
    if not order:
        order = "base,eth,sol,bsc,robinhood"
    return [c.strip() for c in order.split(",") if c.strip() in CHAINS]


# ==== THRESHOLD UTAMA ====
MIN_AGE_DAYS = float(os.getenv("MIN_AGE_DAYS", "4"))
MAX_TOP10_PCT = float(os.getenv("MAX_TOP10_PCT", "65.0"))
MAX_TAX_PCT = float(os.getenv("MAX_TAX_PCT", "10.0"))
MIN_FEE_MC_RATIO = float(os.getenv("MIN_FEE_MC_RATIO", "1e-5"))

MIN_LIQUIDITY_ACCUMULATION = int(os.getenv("MIN_LIQUIDITY_ACCUMULATION", "5000"))
MIN_LIQUIDITY_DEADWHALE = int(os.getenv("MIN_LIQUIDITY_DEADWHALE", "1000"))

SCAN_LIMIT = int(os.getenv("SCAN_LIMIT", "50"))
MIN_ALPHA_TO_SAVE = int(os.getenv("MIN_ALPHA_TO_SAVE", "40"))

# Dead-whale mode: cluster lama, top10 boleh lebih pekat
DEAD_MAX_TOP10_PCT = 95.0

# Wash trading
WASH_VOLUME_RATIO = float(os.getenv("WASH_VOLUME_RATIO", "3.0"))
DEXS_MIN_AVG_TRADE = float(os.getenv("DEXS_MIN_AVG_TRADE", "50"))
DEXS_MAX_TX_PER_HOUR = float(os.getenv("DEXS_MAX_TX_PER_HOUR", "10"))

# ==== API KEYS ====
GMGN_API_KEY = os.getenv("GMGN_API_KEY", "")
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")
GOPLUS_API_KEY = os.getenv("GOPLUS_API_KEY", "")
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# ==== MODE AKTIF ====
ENABLE_MODE_ACCUMULATION = os.getenv("ENABLE_MODE_ACCUMULATION", "true").lower() == "true"
ENABLE_MODE_DEADWHALE = os.getenv("ENABLE_MODE_DEADWHALE", "true").lower() == "true"


def active_modes() -> List[str]:
    modes: List[str] = []
    if ENABLE_MODE_ACCUMULATION:
        modes.append("accumulation")
    if ENABLE_MODE_DEADWHALE:
        modes.append("dead_whale")
    return modes


def mode_enabled(mode: str) -> bool:
    return mode in active_modes()

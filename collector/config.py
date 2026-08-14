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
    explorer_chainid: int  # None = explorer free tier tidak tersedia, di-skip


CHAINS: Dict[str, ChainConfig] = {
    "base": ChainConfig("Base", "base", 8453, 8453),
    "robinhood": ChainConfig("Robinhood", "robinhood", 4663, None),
    "arbitrum": ChainConfig("Arbitrum", "arbitrum", 42161, 42161),
    "eth": ChainConfig("Ethereum", "eth", 1, 1),
    "bsc": ChainConfig("BSC", "bsc", 56, 56),
    "sol": ChainConfig("Solana", "sol", 101, None),
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

# Chain yang dipantau & di-evaluasi. Chain lain (sol/bsc/eth/...) diabaikan dari
# tracking & metrik backtest (tetap tersimpan di DB, tidak dihapus).
TRACK_CHAINS = [c.strip() for c in os.getenv("TRACK_CHAINS", "base,robinhood").split(",") if c.strip()]


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

MIN_LIQUIDITY_ACCUMULATION = int(os.getenv("MIN_LIQUIDITY_ACCUMULATION", "100000"))  # $100K min
MAX_LIQUIDITY_ACCUMULATION = int(os.getenv("MAX_LIQUIDITY_ACCUMULATION", "850000"))  # $850K max = filter token mayor
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
# Per-chain GMGN keys (opsional). Jika tidak diset, fallback ke GMGN_API_KEY.
# Berguna untuk mendistribusikan load ke beberapa key supaya tidak rate-limit.
GMGN_API_KEYS = {
    "base": os.getenv("GMGN_API_KEY_BASE", ""),
    "robinhood": os.getenv("GMGN_API_KEY_ROBINHOOD", ""),
    "sol": os.getenv("GMGN_API_KEY_SOL", ""),
    "bsc": os.getenv("GMGN_API_KEY_BSC", ""),
    "eth": os.getenv("GMGN_API_KEY_ETH", ""),
    "arbitrum": os.getenv("GMGN_API_KEY_ARBITRUM", ""),
}
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")
GOPLUS_API_KEY = os.getenv("GOPLUS_API_KEY", "")
BLOCKSCOUT_API_KEY = os.getenv("BLOCKSCOUT_API_KEY", "")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# ==== MODE AKTIF ====
ENABLE_MODE_ACCUMULATION = os.getenv("ENABLE_MODE_ACCUMULATION", "true").lower() == "true"
ENABLE_MODE_DEADWHALE = os.getenv("ENABLE_MODE_DEADWHALE", "true").lower() == "true"


# ==== DEAD-WHALE DETECTION (Tahap 1) ====
# Definisi whale: wallet dengan balance native besar yang beli token mati.
DW_WHALE_BALANCE_USD = float(os.getenv("DW_WHALE_BALANCE_USD", "10000"))   # saldo native wallet >= $10k
DW_MIN_BUY_USD = float(os.getenv("DW_MIN_BUY_USD", "100"))                  # buy per tx >= $100
DW_MIN_TOKEN_AGE_DAYS = int(os.getenv("DW_MIN_TOKEN_AGE_DAYS", "30"))      # token umur >= 30 hari
# Umur minimum token di dashboard universe (robinhood banyak token muda).
# Berdasarkan creation_timestamp deploy token (GMGN), bukan first_seen.
UNIVERSE_MIN_AGE_DAYS = int(os.getenv("UNIVERSE_MIN_AGE_DAYS", "7"))
DW_DEAD_TXNS_THRESHOLD = int(os.getenv("DW_DEAD_TXNS_THRESHOLD", "5"))   # txns 24h <= 5 = dead
DW_DEAD_VOLUME_USD = float(os.getenv("DW_DEAD_VOLUME_USD", "1000"))      # volume 24h <= USD 1000 = dead
DW_UNIVERSE_PAGES = int(os.getenv("DW_UNIVERSE_PAGES", "8"))             # halaman /api/v2/tokens discan (50/token)
DW_MAX_PRICE_RISE_12H = float(os.getenv("DW_MAX_PRICE_RISE_12H", "10"))     # max naik 10% dalam 12h
DW_MAX_PRICE_RISE_36H = float(os.getenv("DW_MAX_PRICE_RISE_36H", "20"))     # max naik 20% dalam 36h
DW_MAX_VOLUME_MULTIPLIER = float(os.getenv("DW_MAX_VOLUME_MULTIPLIER", "3"))  # volume < 3x avg 7d
DW_CONFIRMATION_WINDOW_H = float(os.getenv("DW_CONFIRMATION_WINDOW_H", "18"))  # window 2+ whale
DW_LOOKBACK_DAYS = int(os.getenv("DW_LOOKBACK_DAYS", "30"))                     # lihat beli 30 hari ke belakang
DW_HOLD_MIN_DAYS = int(os.getenv("DW_HOLD_MIN_DAYS", "3"))                  # hold >= 3 hari = confirm
DW_SCAN_LIMIT = int(os.getenv("DW_SCAN_LIMIT", "50"))                       # token per run

# ==== WALLET TIER (hierarchy buy_usd) untuk token meme ====
# Klasifikasi wallet berdasarkan total buy_usd di whale_positions.
WALLET_TIER_SHARK_MIN = float(os.getenv("WALLET_TIER_SHARK_MIN", "10000"))
WALLET_TIER_DOLPHIN_MIN = float(os.getenv("WALLET_TIER_DOLPHIN_MIN", "5000"))
WALLET_TIER_FISH_MIN = float(os.getenv("WALLET_TIER_FISH_MIN", "1000"))
WALLET_TIER_CRAB_MIN = float(os.getenv("WALLET_TIER_CRAB_MIN", "100"))
# ==== FILTER UNIVERSE: buang token non-meme & scam dari screening ====
# Simbol/pattern yang dikecualikan (uppercase). Default: stablecoin, wrapped/liquid
# staking, dan token "tetangga" BTC/ETH/USD besar yang bukan meme.
DW_EXCLUDE_SYMBOL_PARTS = os.getenv("DW_EXCLUDE_SYMBOL_PARTS",
    "USD,USDT,USDC,DAI,WETH,WBTC,STETH,WSTETH,CBETH,RSETH,WRSETH,RBTC,CBTC,RETH,ETHBTC,XSOLVBTC,ACBETH,CLBTC,CGUSD,STAKED,WRAP,STEAK,MTBILL,TBILL").split(",")
# Subsring pada nama/simbol yang menandakan spam/phishing (lowercase).
DW_EXCLUDE_NAME_PARTS = os.getenv("DW_EXCLUDE_NAME_PARTS",
    "phish,scam,airdrop,claim,free,btc20,mint,giveaway").split(",")
# Token di luar batas market-cap ini dianggap "mayor"/non-meme (skip).
DW_MAX_MARKET_CAP_USD = float(os.getenv("DW_MAX_MARKET_CAP_USD", "30000000"))  # $30M max = token kecil/meme

# ==== FILTER MEME UNTUK SIGNAL (SecurityGate — semua mode) ====
# Token biru-chip/ternama yang secara PRESISI (symbol sama-tentu) bukan meme —
# ditolak pada jalur signal agar tidak pernah masuk signals/TP1.
SIGNAL_EXCLUDE_SYMBOLS = os.getenv("SIGNAL_EXCLUDE_SYMBOLS",
    "WBTC,CBTC,RBTC,WETH,STETH,WSTETH,CBETH,RSETH,WRSETH,RETH,ETHBTC,"
    "UNI,LINK,AAVE,MKR,CRV,SNX,COMP,"
    "USDC,USDT,USDE,PYUSD,TUSD,FDUSD,BUSD,GUSD,CGUSD,LUSD,SUSDE,USHAM,USDS,DAI,MTLBILL,TBILL,USDY").split(",")
# Substring pada symbol yang menandakan wrapped/pegged/non-meme (case-insensitive).
# Hati-hati: jangan letakkan kata umum (mis. UNI) di sini — itu memakan meme.
SIGNAL_EXCLUDE_SYMBOL_PARTS = os.getenv("SIGNAL_EXCLUDE_SYMBOL_PARTS",
    "WRAP,STAKED,STEAK,LEVERAGED,STRATEGY,VAULT").split(",")
# Substring pada nama yang menandakan token resmi/non-meme (case-insensitive).
SIGNAL_EXCLUDE_NAME_PARTS = os.getenv("SIGNAL_EXCLUDE_NAME_PARTS",
    "wrapped bitcoin,wrapped ether,uniswap,chainlink,aave protocol,compound finance,curve finance,stablecoin,pegged usd").split(",")
# Market-cap maksimum token baru yang boleh menjadi sinyal (meme = mikro-cap).
SIGNAL_MAX_MARKET_CAP_USD = float(os.getenv("SIGNAL_MAX_MARKET_CAP_USD", "900000"))  # $900K = micro-meme

# ==== GMGN RISK GATE (filter token scam di universe) ====
DW_GMGN_TOP10_MAX = float(os.getenv("DW_GMGN_TOP10_MAX", "0.50"))      # top10 holder <= 50% (GMGN danger >0.50)
DW_GMGN_RUG_MAX = float(os.getenv("DW_GMGN_RUG_MAX", "0.30"))          # rug_ratio <= 0.30
DW_GMGN_SKIP_HONEYPOT = os.getenv("DW_GMGN_SKIP_HONEYPOT", "1") == "1"  # buang honeypot
DW_GMGN_SKIP_ALERT = os.getenv("DW_GMGN_SKIP_ALERT", "1") == "1"        # buang token ber-flag alert GMGN
DW_GMGN_MAX_TAX = float(os.getenv("DW_GMGN_MAX_TAX", "0.10"))           # buy/sell tax <= 10%


def active_modes() -> List[str]:
    modes: List[str] = []
    if ENABLE_MODE_ACCUMULATION:
        modes.append("accumulation")
    if ENABLE_MODE_DEADWHALE:
        modes.append("dead_whale")
    return modes


def mode_enabled(mode: str) -> bool:
    return mode in active_modes()

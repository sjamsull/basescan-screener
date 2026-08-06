"""Tipe bersama. Gunakan Dict kosong sebagai default agar praktis di pipeline."""

EMPTY_SECURITY = {
    "is_honeypot": None,
    "owner_renounced": None,
    "can_mint": None,
    "can_blacklist": None,
    "buy_tax": 0.0,
    "sell_tax": 0.0,
    "is_open_source": None,
    "holder_count": 0,
    "error": None,
}
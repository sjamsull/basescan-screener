"""GoPlus — layer keamanan kontrak. Gagal = status None + error, tidak di-replace mock."""

import os
import logging
from typing import Dict

from collector.utils.api import get_json, APIError
from shared.types import EMPTY_SECURITY

logger = logging.getLogger(__name__)

ZERO_ADDR = "0x0000000000000000000000000000000000000000"


class GoPlusClient:
    def __init__(self):
        self.api_key = os.getenv("GOPLUS_API_KEY", "")
        self.base_url = "https://api.gopluslabs.io/api/v1/token_security"

    def check_token(self, address: str, chain_id: int) -> Dict:
        if self.api_key:
            url = f"{self.base_url}/{chain_id}"
            params = {"contract_addresses": address, "Authorization": self.api_key}
        else:
            url = f"{self.base_url}/{chain_id}"
            params = {"contract_addresses": address}

        try:
            data = get_json(url, params=params, timeout=30)
        except APIError as exc:
            logger.error("GoPlus %s (%d): %s", address, chain_id, exc)
            return {**EMPTY_SECURITY, "error": str(exc)}

        result = data.get("result", {}).get(address.lower(), {})
        if not result:
            return {**EMPTY_SECURITY, "error": "no security data returned"}

        is_honeypot_raw = str(result.get("is_honeypot", "0"))
        owner = str(result.get("owner_address", ""))
        sell_tax = self._tax(result.get("sell_tax"))
        buy_tax = self._tax(result.get("buy_tax"))

        return {
            "is_honeypot": is_honeypot_raw == "1",
            "owner_address": owner,
            "owner_renounced": owner.lower() == ZERO_ADDR,
            "can_mint": str(result.get("is_mintable", "0")) == "1",
            "can_blacklist": str(result.get("is_blacklisted", "0")) == "1",
            "can_pause": str(result.get("is_open_source", "0")) == "1",
            "buy_tax": buy_tax,
            "sell_tax": sell_tax,
            "is_open_source": str(result.get("is_open_source", "0")) == "1",
            "is_proxy": str(result.get("is_proxy", "0")) == "1",
            "holder_count": int(result.get("holder_count", "0") or 0),
            "raw": result,
            "error": None,
        }

    @staticmethod
    def _tax(value) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0
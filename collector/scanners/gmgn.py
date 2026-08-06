"""GMGN — feed utama: trending accumulation & dead-whale, plus token_info."""

import os
import logging
from typing import List, Dict, Optional

from collector.utils.api import get_json, APIError
from collector.utils.validators import valid_address

logger = logging.getLogger(__name__)


class GMGNClient:
    def __init__(self, chain: str = "base"):
        self.api_key = os.getenv("GMGN_API_KEY", "")
        self.chain = chain
        self.base_url = "https://gmgn.ai/defi/router/v1"

    def _headers(self) -> dict:
        return {"X-APIKEY": self.api_key, "User-Agent": "Basescan/1.0"} if self.api_key else {"User-Agent": "Basescan/1.0"}

    def get_trending(self, mode: str = "accumulation", limit: int = 50) -> List[Dict]:
        """mode: accumulation → smart_degen order desc; dead_whale → swaps asc."""
        if mode == "accumulation":
            orderby, direction = "smart_degen_count", "desc"
        elif mode == "dead_whale":
            orderby, direction = "swaps", "desc"
        else:
            orderby, direction = "swaps", "desc"

        url = f"{self.base_url}/trending/{self.chain}"
        params = {"orderby": orderby, "direction": direction, "limit": limit}
        try:
            data = get_json(url, headers=self._headers(), params=params)
            return data.get("data", {}).get("list", [])
        except APIError as exc:
            logger.error("GMGN trending %s/%s: %s", self.chain, mode, exc)
            raise

    def get_token_info(self, address: str) -> Optional[Dict]:
        if not valid_address(address):
            return None
        url = f"{self.base_url}/token_info"
        params = {"chain": self.chain, "address": address, "isCache": "false"}
        try:
            data = get_json(url, params=params, headers=self._headers())
            return data.get("data")
        except APIError as exc:
            logger.error("token_info %s: %s", address, exc)
            return None
"""Blockscout — sumber whale detection on-chain (gratis).

Strategi hybrid:
  - PRO API (api.blockscout.com/v2/api) untuk chain yang didukung (ETH=1, Arbitrum=42161)
  - Instance-level (base.blockscout.com, robinhoodchain.blockscout.com) untuk Base & robinhood
    yang TIDAK didukung PRO API.

Endpoint yang dipakai:
  - tokentx   : riwayat ERC-20 transfer (from, to, value, timeStamp) -> deteksi buy/sell
  - holders   : daftar holder sorted by balance -> deteksi whale
  - balance   : saldo native wallet
  - tokenbalance : saldo token wallet

Catatan: instance-level kadang 500 (rate limit) -> perlu retry + backoff.
"""

import logging
import os
import time
from typing import Dict, List, Optional

from collector.utils.api import get_json, APIError
from collector.utils.helpers import to_float, to_int

logger = logging.getLogger(__name__)

def _iso_to_ts(iso: str) -> int:
    """Konversi ISO8601 (dengan 'Z') ke unix timestamp. 0 kalau tidak valid."""
    if not iso:
        return 0
    s = iso.rstrip("Z")
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(s)
        import calendar
        return calendar.timegm(dt.timetuple())
    except Exception:
        return 0

ZERO_ADDR = "0x0000000000000000000000000000000000000000"

PRO_BASE = "https://api.blockscout.com/v2/api"

# chain -> (mode, base_url)
# mode "pro" = PRO API (butuh key), mode "instance" = instance-level (gratis tanpa key)
CHAIN_ENDPOINTS = {
    "eth": ("pro", "https://api.blockscout.com/v2/api"),
    "arbitrum": ("pro", "https://api.blockscout.com/v2/api"),
    "base": ("instance", "https://base.blockscout.com"),
    "robinhood": ("instance", "https://robinhoodchain.blockscout.com"),
}

# chain -> chain_id (untuk PRO API)
CHAIN_IDS = {
    "eth": 1,
    "arbitrum": 42161,
    "base": 8453,
    "robinhood": 4663,
}


class BlockscoutClient:
    def __init__(self):
        self.pro_key = os.getenv("BLOCKSCOUT_API_KEY", "")

    # ---------- low-level ----------

    def _get(self, chain: str, module: str, action: str, params: Dict) -> Dict:
        mode, base = CHAIN_ENDPOINTS.get(chain, ("instance", "https://base.blockscout.com"))
        if mode == "pro":
            if not self.pro_key:
                raise APIError(f"BLOCKSCOUT_API_KEY required for {chain} (PRO API)")
            url = f"{PRO_BASE}"
            q = {
                "chainid": CHAIN_IDS.get(chain, 1),
                "module": module,
                "action": action,
                "apikey": self.pro_key,
                **params,
            }
        else:
            url = f"{base}/api"
            q = {"module": module, "action": action, **params}

        # instance-level kadang 500 / rate-limit -> retry dengan backoff agresif
        last_exc: Optional[Exception] = None
        for attempt in range(4):
            try:
                data = get_json(url, params=q, timeout=30, retries=1)
                # PRO API error di body (status 0 / error)
                if data.get("status") == "0" and data.get("message") not in ("No token transfers found",):
                    return data
                if data.get("error"):
                    raise APIError(f"Blockscout {chain} {action}: {data['error']}")
                return data
            except APIError as exc:
                last_exc = exc
                wait = 3.0 * (2 ** attempt)
                logger.warning("Blockscout %s/%s retry %d after %.1fs: %s", chain, action, attempt, wait, exc)
                time.sleep(wait)
        raise APIError(f"Blockscout {chain}/{action} failed after retries: {last_exc}")

    # ---------- token transfers (buy/sell history) ----------

    def token_transfers(self, chain: str, address: str, page: int = 1, offset: int = 20) -> List[Dict]:
        """Riwayat ERC-20 transfer token. Return list dict {from,to,value,timeStamp,...}.

        - PRO API (eth/arbitrum): module=account&action=tokentx (v1)
        - instance (base/robinhood): v2 /tokens/{addr}/transfers (v1 sering rate-limited)
        """
        mode, base = CHAIN_ENDPOINTS.get(chain, ("instance", "https://base.blockscout.com"))
        if mode == "pro":
            data = self._get(chain, "account", "tokentx", {
                "contractaddress": address,
                "page": page,
                "offset": offset,
            })
            res = data.get("result")
            return res if isinstance(res, list) else []
        return self._transfers_v2(base, address, page=page)

    def _transfers_v2(self, base: str, address: str, page: int = 1) -> List[Dict]:
        """Satu halaman transfer via API v2. Page >= 2 tidak didukung (perlu next_page_params)."""
        url = f"{base}/api/v2/tokens/{address}/transfers"
        try:
            data = get_json(url, timeout=30, retries=1)
        except Exception as exc:
            logger.warning("Blockscout transfers v2 %s: %s", address, exc)
            return []
        out = []
        for t in (data.get("items") or []):
            if t.get("token_type") != "ERC-20" or t.get("type") != "token_transfer":
                continue
            frm = (t.get("from") or {}).get("hash", "")
            to = (t.get("to") or {}).get("hash", "")
            total = t.get("total") or {}
            out.append({
                "from": frm,
                "to": to,
                "value": total.get("value", "0"),
                "tokenDecimal": total.get("decimals", "18"),
                "timeStamp": _iso_to_ts(t.get("timestamp", "")),
                "contractAddress": address,
                "hash": t.get("transaction_hash", ""),
                "method": t.get("method"),
            })
        return out

    def token_transfers_since(self, chain: str, address: str, since_ts: int, limit: int = 500) -> List[Dict]:
        """Transfer token sejak timestamp tertentu (untuk deteksi buy terbaru).

        Instance Blockscout mengembalikan halaman terbaru-dulu; halaman berhenti
        begitu semua entry < since_ts.
        """
        out: List[Dict] = []
        mode, base = CHAIN_ENDPOINTS.get(chain, ("instance", "https://base.blockscout.com"))
        if mode == "pro":
            page = 1
            while len(out) < limit:
                batch = self.token_transfers(chain, address, page=page, offset=100)
                if not batch:
                    break
                for t in batch:
                    ts = to_int(t.get("timeStamp"), 0)
                    if ts >= since_ts:
                        out.append(t)
                    else:
                        return out
                page += 1
                if len(batch) < 100:
                    break
            return out
        return self._transfers_v2_since(base, address, since_ts, limit)

    def _transfers_v2_since(self, base: str, address: str, since_ts: int, limit: int = 500) -> List[Dict]:
        """Pagination v2 via next_page_params sampai semua entry < since_ts."""
        out: List[Dict] = []
        next_params: Optional[Dict] = None
        for _ in range(20):  # maks 20 halaman (20 x 50 = 1000 transfer)
            url = f"{base}/api/v2/tokens/{address}/transfers"
            if next_params:
                sep = "?"
                for k, v in next_params.items():
                    url += f"{sep}{k}={v}"
                    sep = "&"
            try:
                data = get_json(url, timeout=30, retries=1)
            except Exception as exc:
                logger.warning("Blockscout transfers v2 page %s: %s", address, exc)
                break
            items = data.get("items") or []
            for t in items:
                if t.get("token_type") != "ERC-20" or t.get("type") != "token_transfer":
                    continue
                ts = _iso_to_ts(t.get("timestamp", ""))
                if ts >= since_ts:
                    total = t.get("total") or {}
                    frm = (t.get("from") or {}).get("hash", "")
                    to = (t.get("to") or {}).get("hash", "")
                    out.append({
                        "from": frm,
                        "to": to,
                        "value": total.get("value", "0"),
                        "tokenDecimal": total.get("decimals", "18"),
                        "timeStamp": ts,
                        "contractAddress": address,
                        "hash": t.get("transaction_hash", ""),
                        "method": t.get("method"),
                    })
                    if len(out) >= limit:
                        return out
                else:
                    return out
            next_params = data.get("next_page_params")
            if not next_params:
                break
            time.sleep(0.4)
        return out

    # ---------- holders (whale detection) ----------

    def top_holders(self, chain: str, address: str, limit: int = 20) -> List[Dict]:
        """Daftar holder terbesar (sorted by balance) untuk token.

        Return list dict: {address, name, is_contract, is_scam, balance_raw, decimals}
        """
        mode, base = CHAIN_ENDPOINTS.get(chain, ("instance", "https://base.blockscout.com"))
        if mode == "pro":
            # PRO API pakai module=token&action=getTokenHolders
            data = self._get(chain, "token", "getTokenHolders", {
                "contractaddress": address, "page": 1, "offset": limit,
            })
            res = data.get("result") or []
            out = []
            for h in res:
                out.append({
                    "address": h.get("address", ""),
                    "name": h.get("name"),
                    "is_contract": h.get("is_contract", False),
                    "is_scam": h.get("is_scam", False),
                    "balance_raw": h.get("balance", "0"),
                    "decimals": to_int(h.get("decimals"), 18),
                })
            return out

        # instance-level: v2 /api/v2/tokens/{addr}/holders
        url = f"{base}/api/v2/tokens/{address}/holders?items_count={limit}"
        try:
            data = get_json(url, timeout=30, retries=1)
        except Exception as exc:
            logger.warning("Blockscout holders %s: %s", address, exc)
            return []
        out = []
        for h in (data.get("items") or []):
            addr_info = h.get("address") or {}
            token_info = h.get("token") or {}
            out.append({
                "address": addr_info.get("hash", ""),
                "name": addr_info.get("name"),
                "is_contract": addr_info.get("is_contract", False),
                "is_scam": addr_info.get("is_scam", False),
                "balance_raw": h.get("value", "0"),
                "decimals": to_int(token_info.get("decimals"), 18),
                "exchange_rate": to_float(token_info.get("exchange_rate"), 0.0),
            })
        return out

    # ---------- balance ----------

    def native_balance(self, chain: str, address: str) -> float:
        """Saldo native (wei -> ETH). Return 0 kalau gagal."""
        try:
            data = self._get(chain, "account", "balance", {"address": address})
            return to_float(data.get("result"), 0.0)
        except Exception:
            return 0.0

    def token_balance(self, chain: str, token: str, address: str) -> float:
        """Saldo token wallet (raw units). Return 0 kalau gagal."""
        try:
            data = self._get(chain, "account", "tokenbalance", {
                "contractaddress": token, "address": address,
            })
            return to_float(data.get("result"), 0.0)
        except Exception:
            return 0.0

    # ---------- token metadata ----------

    def token_info(self, chain: str, address: str) -> Optional[Dict]:
        """Metadata token (symbol, decimals, supply, holders)."""
        mode, base = CHAIN_ENDPOINTS.get(chain, ("instance", "https://base.blockscout.com"))
        try:
            if mode == "pro":
                data = self._get(chain, "token", "getToken", {"contractaddress": address})
                res = data.get("result") or {}
                return {
                    "symbol": res.get("symbol"),
                    "name": res.get("name"),
                    "decimals": to_int(res.get("decimals"), 18),
                    "total_supply": res.get("totalSupply"),
                    "holders": to_int(res.get("holderCount"), 0),
                }
            url = f"{base}/api/v2/tokens/{address}"
            data = get_json(url, timeout=30, retries=1)
            return {
                "symbol": data.get("symbol"),
                "name": data.get("name"),
                "decimals": to_int(data.get("decimals"), 18),
                "total_supply": data.get("total_supply"),
                "holders": to_int(data.get("holders_count"), 0),
                "volume_24h": to_float(data.get("volume_24h"), 0.0),
                "market_cap": to_float(data.get("circulating_market_cap"), 0.0),
                "exchange_rate": to_float(data.get("exchange_rate"), 0.0),
            }
        except Exception as exc:
            logger.warning("Blockscout token_info %s: %s", address, exc)
            return None

"""Dune — sumber *universe* token mati (optional).

API key Dune hanya bisa eksekusi SQL di warehouse default (postgres), **bukan**
blockchain-specific (`base.core.*`) kecuali query dibuat di Dune UI dengan
network = Base. Jadi kita pakai flow *saved query*:

1. (Satu kali) buat query SQL ini di Dune UI, pilih network **Base**:
   https://dune.com/queries/new
   -----------------------------------------------------------------
   WITH tok AS (
     SELECT contract_address, symbol, decimals
     FROM tokens.erc20
     WHERE blockchain = 'base'
   ),
   created AS (
     SELECT address AS contract_address, MIN(block_time) AS created_at
     FROM base.creation_traces
     GROUP BY 1
   ),
   vol AS (
     SELECT
       tr.contract_address,
       COUNT(*) AS txns_24h,
       SUM((CAST(tr.value AS double) / POWER(10, t.decimals)) * COALESCE(p.price, 0)) AS volume_24h
     FROM erc20_base.evt_Transfer tr
     JOIN tok t ON t.contract_address = tr.contract_address
     LEFT JOIN prices.usd p
       ON p.blockchain = 'base'
      AND p.contract_address = tr.contract_address
      AND p.minute = DATE_TRUNC('hour', tr.evt_block_time)
     WHERE tr.evt_block_time >= NOW() - INTERVAL '24' HOUR
     GROUP BY 1
   )
   SELECT
     t.contract_address AS token_address,
     t.symbol,
     t.decimals,
     DATE_DIFF('day', c.created_at, NOW()) AS age_days,
     COALESCE(v.volume_24h, 0) AS volume_24h,
     COALESCE(v.txns_24h, 0)   AS txns_24h
   FROM tok t
   JOIN created c ON c.contract_address = t.contract_address
   LEFT JOIN vol v ON v.contract_address = t.contract_address
   WHERE DATE_DIFF('day', c.created_at, NOW()) >= 30
     AND COALESCE(v.txns_24h, 0) <= 5
   ORDER BY age_days DESC
   LIMIT 200
   -----------------------------------------------------------------
2. Setelah **Save**, salin `query_id` dari URL (https://dune.com/queries/<ID>)
   ke env `DUNE_DEAD_TOKENS_QUERY_ID`.
3. populate_universe memakai query_id ini via SDK; kalau tidak ada, fallback ke
   Blockscout/DexScreener (signals seed) — sistem tetap berjalan.

API key via env DUNE_API_KEY.
"""

import logging
import os, time
from typing import List, Optional
import requests

logger = logging.getLogger(__name__)

BASE = "https://api.dune.com/api/v1"


def _hdr(key: str):
    return {"X-Dune-API-Key": key, "Content-Type": "application/json"}


def run_query(query_id: int, api_key: Optional[str] = None, poll_interval: int = 5, max_wait: int = 180) -> List[dict]:
    """Execute a *saved query* (network=Base dibikin di Dune UI) via REST API v1."""
    key = api_key or os.getenv("DUNE_API_KEY", "")
    if not key:
        raise RuntimeError("DUNE_API_KEY belum di-set")
    url_exec = f"{BASE}/query/{query_id}/execute"
    url_res = f"{BASE}/execution"
    r = requests.post(url_exec, headers=_hdr(key), json={}, timeout=10)
    if r.status_code != 200:
        r.raise_for_status()
    body = r.json()
    eid = body.get("execution_id") or body.get("job_id")
    if not eid:
        raise RuntimeError(f"Dune execute gagal: {body}")
    deadline = time.time() + max_wait
    while time.time() < deadline:
        rr = requests.get(f"{url_res}/{eid}/results", headers=_hdr(key), timeout=30)
        rr.raise_for_status()
        d = rr.json()
        state = (d.get("execution", {}) or {}).get("state") or d.get("state") or ""
        if state.endswith("_COMPLETED"):
            break
        if state.endswith("_FAILED") or "error" in (rr.text.lower()):
            raise RuntimeError(f"Dune query FAILED: {d}")
        time.sleep(poll_interval)
    res = d.get("result", {}).get("rows") or d.get("result_data", {}).get("rows") or []
    if isinstance(res, dict):
        res = res.get("rows", [])
    return res

"""Dune — sumber *universe* token mati (optional).

API key Dune hanya bisa eksekusi SQL di warehouse default (postgres), **bukan**
blockchain-specific (`base.core.*`) kecuali query dibuat di Dune UI dengan
network = Base. Jadi kita pakai flow *saved query*:

1. (Satu kali) buat query SQL ini di Dune UI, pilih network **Base**:
   https://dune.com/queries/new
   -----------------------------------------------------------------
   SELECT
     LOWER(contract_address)                  AS token_address,
     symbol,
     decimals,
     total_supply,
     holders,
     age_days,
     volume_24h,
     txns_24h
   FROM (
     SELECT
       t.contract_address,
       t.symbol,
       t.decimals,
       t.total_supply,
       t.holders,
       DATE_DIFF('day', t.created_at, NOW()) AS age_days,
       COALESCE(v.volume_24h, 0) AS volume_24h,
       COALESCE(v.txns_24h,    0) AS txns_24h
     FROM base.core.dim_tokens t
     LEFT JOIN LATERAL (
       SELECT
         SUM(amount_usd) AS volume_24h,
         COUNT(*)         AS txns_24h
       FROM base.core.fact_token_transfers
       WHERE block_time >= NOW() - INTERVAL '24 hours'
         AND token_address = t.contract_address
     ) v ON TRUE
     WHERE t.created_at IS NOT NULL
   )
   WHERE age_days >= 30 AND txns_24h <= 5
   ORDER BY holders DESC
   LIMIT 200
   -----------------------------------------------------------------
2. Setelah **Save**, salin `query_id` dari URL (https://dune.com/queries/<ID>)
   ke env `DUNE_DEAD_TOKENS_QUERY_ID`.
3. populate_universe memakai query_id ini via SDK; kalau tidak ada, fallback ke
   Blockscout/DexScreener (signals seed) — sistem tetap berjalan.

API key via env DUNE_API_KEY.
"""

import logging
import os
from typing import List, Optional

logger = logging.getLogger(__name__)

try:
    from dune_client.client import DuneClient

    _HAS_SDK = True
except Exception:  # dune-client belum terpasang di environment tertentu
    DuneClient = None  # type: ignore
    _HAS_SDK = False


def get_client(api_key: Optional[str] = None) -> Optional["DuneClient"]:
    if not _HAS_SDK:
        return None
    key = api_key or os.getenv("DUNE_API_KEY", "")
    if not key:
        return None
    return DuneClient(api_key=key)


def run_query(query_id: int, api_key: Optional[str] = None) -> List[dict]:
    """Jalankan *saved query* (network Base di Dune UI) & kembalikan rows."""
    if not _HAS_SDK:
        raise RuntimeError("dune-client not installed; run: pip install dune-client")
    client = get_client(api_key) or DuneClient(api_key=os.getenv("DUNE_API_KEY", ""))
    df = client.run_query_dataframe(query_id)
    if df is None:
        return []
    return df.to_dict(orient="records")

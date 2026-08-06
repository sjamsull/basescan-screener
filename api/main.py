"""FastAPI — endpoint ringan untuk dashboard. Tidak expose API key apa pun."""

import logging
import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException

app = FastAPI(title="Basescan API", version="1.0.0")

DASH_KEY = os.getenv("DASH_API_KEY", "")


def require_key(key_header: Optional[str]) -> None:
    if DASH_KEY and key_header != DASH_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/")
def root():
    return {"name": "Basescan API", "status": "ok"}


@app.get("/health")
def health():
    return {"ok": True}


from shared.constants import VERDICT_ALPHA


@app.get("/verdicts")
def verdicts():
    return VERDICT_ALPHA


@app.get("/chain-config")
def chain_config(k: Optional[str] = None):
    require_key(k)
    from collector import config as cfg
    return {name: {"gmgn": c.gmgn_id, "goplus_id": c.goplus_id} for name, c in cfg.CHAINS.items()}
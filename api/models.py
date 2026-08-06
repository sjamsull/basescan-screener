"""Pydantic models untuk API."""

from pydantic import BaseModel
from typing import Dict, Any, Optional


class ScanResult(BaseModel):
    chain: str
    mode: str
    token_count: int
    scanned_at: str
    payload: list[Dict[str, Any]]


class Signal(BaseModel):
    token_address: str
    chain: str
    verdict: str
    alpha: float
    risk: float
    signal_at: str
    status: str
    exit_price: Optional[float] = None
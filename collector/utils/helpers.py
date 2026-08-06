"""Helper numeric & format. Semua anti-crash: default aman, tanpa throw."""

from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def pct(value: float) -> float:
    """0-1 ke 0-100."""
    return value * 100.0


def clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def age_hours_from(first_ts: Any, now: Any = None) -> float:
    """Age dalam jam dari timestamp (int/float/string ISO)."""
    if first_ts is None:
        return 0.0
    try:
        if isinstance(first_ts, str):
            dt = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ts = dt.timestamp()
        else:
            ts = float(first_ts)
        if ts > 10**15:  # ms -> s
            ts /= 1000.0
        ref = datetime.now(timezone.utc).timestamp()
        return max(0.0, (ref - ts) / 3600.0)
    except (TypeError, ValueError):
        return 0.0
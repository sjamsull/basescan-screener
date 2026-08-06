"""Validator alamat & payload. Alamat palsu = reject sebelum buang API call."""

import re

_ADDR = re.compile(r"^(0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})$")


def valid_address(addr: str | None) -> bool:
    if not addr or not isinstance(addr, str):
        return False
    if len(addr) > 50:
        return False
    return _ADDR.match(addr) is not None


def short_addr(addr: str, head: int = 6, tail: int = 4) -> str:
    if len(addr) <= head + tail:
        return addr
    return f"{addr[:head]}...{addr[-tail:]}"
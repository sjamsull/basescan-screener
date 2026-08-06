"""HTTP client dengan retry eksponensial. Tidak ada silent catch-all."""

import time
import logging
import requests

logger = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Basescan/1.0"


def get_json(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = 30,
    retries: int = 3,
    backoff: float = 1.5,
) -> dict:
    """GET dan decode JSON. Raise APIError kalau API non-2xx / bukan JSON.

    Tidak mengembalikan None diam-diam — pipeline harus tahu API gagal.
    """
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(
                url,
                params=params,
                headers=headers or {"User-Agent": UA},
                timeout=timeout,
            )
            if resp.status_code == 429:
                wait = backoff * (2 ** attempt)
                logger.warning("Rate-limited (%s), retry in %.1fs", url, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            last_exc = exc
            wait = backoff * (2 ** attempt)
            logger.warning("GET %s failed (attempt %d): %s", url, attempt + 1, exc)
            time.sleep(wait)
    raise APIError(f"GET {url} failed after {retries} attempts: {last_exc}")


class APIError(RuntimeError):
    pass
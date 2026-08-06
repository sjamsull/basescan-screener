"""Local JSON storage — dev fallback yang TERTULIS, bukan mock. History permanen di disk."""

import json
import os
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)

DATA_DIR = Path(os.getenv("LOCAL_DATA_DIR", "data"))


class LocalStore:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root else DATA_DIR
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, chain: str, mode: str) -> Path:
        return self.root / f"{chain}_{mode}.jsonl"

    def append(self, records: List[Dict], chain: str, mode: str):
        path = self._path(chain, mode)
        ts = datetime.now(timezone.utc).isoformat()
        with open(path, "a", encoding="utf-8") as f:
            for rec in records:
                rec["_saved_at"] = ts
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def read(self, chain: str, mode: str, limit: int = 200) -> List[Dict]:
        path = self._path(chain, mode)
        if not path.exists():
            return []
        rows = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    logger.warning("skip corrupt line in %s: %s", path, exc)
        return rows[-limit:]

    def write_reject_log(self, chain: str, mode: str, records: List[Dict]):
        """Simpan alasan reject permanen — transparansi penuh."""
        path = self.root / f"reject_log_{chain}_{mode}.jsonl"
        ts = datetime.now(timezone.utc).isoformat()
        with open(path, "a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps({"_saved_at": ts, **rec}, ensure_ascii=False) + "\n")
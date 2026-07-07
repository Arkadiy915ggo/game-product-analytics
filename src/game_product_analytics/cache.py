from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


class DiskCache:
    def __init__(self, root: Path, namespace: str) -> None:
        self.root = root / namespace
        self.root.mkdir(parents=True, exist_ok=True)

    def get(self, key: str, *, max_age_seconds: int) -> Any | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        created_at = float(payload.get("created_at", 0))
        if max_age_seconds >= 0 and time.time() - created_at > max_age_seconds:
            return None
        return payload.get("value")

    def set(self, key: str, value: Any) -> None:
        path = self._path(key)
        payload = {"created_at": time.time(), "value": value}
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(path)

    def _path(self, key: str) -> Path:
        return self.root / f"{_hash_key(key)}.json"


def cache_key(*parts: object) -> str:
    data = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return data


def _hash_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

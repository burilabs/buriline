"""Append-only, hash-chained audit trail.

Every analysis, question and answer is recorded together with the SHA-256 of the
dataset it was based on and the model that produced the text. Each record carries the
hash of the previous record, so any later modification of the file is detectable with
``verify()``. Records are never updated or deleted by the application.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

GENESIS = "0" * 64


def _canonical(record: dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _record_hash(record: dict[str, Any]) -> str:
    body = {k: v for k, v in record.items() if k != "hash"}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


class AuditTrail:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _last_hash(self) -> str:
        last = GENESIS
        for rec in self.iter_records():
            last = rec.get("hash", last)
        return last

    def iter_records(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def append(self, action: str, *, user: str = "system", dataset_sha256: str | None = None,
               model: str | None = None, prompt: str | None = None, answer: str | None = None,
               details: dict[str, Any] | None = None) -> dict[str, Any]:
        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "user": user,
            "action": action,
            "dataset_sha256": dataset_sha256,
            "model": model,
            "prompt": prompt,
            "answer": answer,
            "details": details or {},
            "prev_hash": self._last_hash(),
        }
        record["hash"] = _record_hash(record)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(_canonical(record) + "\n")
        return record

    def verify(self) -> tuple[bool, int, str]:
        """Return (ok, number_of_records, message)."""
        prev = GENESIS
        count = 0
        for rec in self.iter_records():
            count += 1
            if rec.get("prev_hash") != prev:
                return False, count, f"Kette unterbrochen bei Eintrag {count}"
            if _record_hash(rec) != rec.get("hash"):
                return False, count, f"Eintrag {count} wurde verändert"
            prev = rec["hash"]
        return True, count, "Audit-Trail unverändert"

    def records(self, limit: int = 200) -> list[dict[str, Any]]:
        recs = list(self.iter_records())
        return recs[-limit:][::-1]


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

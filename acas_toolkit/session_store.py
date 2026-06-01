"""
Persistent session store mapping ``session_id`` → sandbox snapshot.

The store remembers, for each named conversation/session, the ACA Sandboxes
*snapshot* ID we should hydrate from on the next ``open``. Snapshots survive
sandbox deletion (and process restarts, and reboots), so a "session" here is
genuinely durable across runs.

Storage format
--------------
JSON file. Default location: ``~/.acas-toolkit/sessions.json``. Override
via ``ACAS_SESSION_STORE_PATH`` env var.

The on-disk shape::

    {
      "version": 1,
      "sessions": {
        "<session_id>": {
          "subscription_id": "...",
          "resource_group": "...",
          "sandbox_group": "...",
          "disk": "python-3.13",
          "snapshot_id": "<id-or-null>",
          "created_at": "2026-05-19T10:00:00Z",
          "last_seen_at": "2026-05-19T10:05:00Z"
        }
      }
    }

A session entry can have ``snapshot_id == null`` between ``open`` and the
first ``checkpoint`` — it's a session that exists but has no persisted
state yet.

This is intentionally a stupid-simple file-backed store. Swap it for SQLite
/ Redis / etc. when concurrent-process access is needed.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class SessionEntry:
    subscription_id: str
    resource_group: str
    sandbox_group: str
    disk: str
    snapshot_id: str | None = None
    created_at: str = field(default_factory=_now)
    last_seen_at: str = field(default_factory=_now)


class SessionStore:
    def __init__(self, path: Path | str | None = None):
        if path is None:
            path = os.environ.get("ACAS_SESSION_STORE_PATH") or (
                Path.home() / ".acas-toolkit" / "sessions.json"
            )
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ----- I/O ------------------------------------------------------------

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": SCHEMA_VERSION, "sessions": {}}
        with self.path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        # Ignore version on read for now; bump and migrate when needed.
        data.setdefault("sessions", {})
        return data

    def _save(self, data: dict) -> None:
        # Atomic write via temp file + rename.
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(self.path.parent),
            prefix=".sessions-",
            suffix=".json.tmp",
            delete=False,
        ) as tmp:
            json.dump(data, tmp, indent=2)
            tmp_path = Path(tmp.name)
        tmp_path.replace(self.path)

    # ----- API ------------------------------------------------------------

    def get(self, session_id: str) -> SessionEntry | None:
        data = self._load()
        raw = data["sessions"].get(session_id)
        if not raw:
            return None
        return SessionEntry(**raw)

    def put(self, session_id: str, entry: SessionEntry) -> None:
        data = self._load()
        entry.last_seen_at = _now()
        data["sessions"][session_id] = asdict(entry)
        self._save(data)

    def delete(self, session_id: str) -> None:
        data = self._load()
        data["sessions"].pop(session_id, None)
        self._save(data)

    def items(self) -> Iterator[tuple[str, SessionEntry]]:
        data = self._load()
        for sid, raw in data["sessions"].items():
            yield sid, SessionEntry(**raw)


__all__ = ["SessionEntry", "SessionStore"]

"""
Probe: snapshot rehydrate with workspace volumes (b2 compatibility).

Why this exists
---------------
`azure-containerapps-sandbox` 0.1.0b2 rejects
`begin_create_sandbox(snapshot_id=...)` when combined with `volumes`.
`acas_toolkit` now defers volume re-apply until after the rehydrate LRO
returns. This probe validates that behavior against a live sandbox group.

Run
---
    uv run python probes/sandbox_snapshot_with_volume.py
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from dotenv import load_dotenv

from acas_toolkit import SandboxPool, SessionManager
from acas_toolkit.workspace import WorkspaceVolume, ensure_workspace_volume


def _must_contain(haystack: str, needle: str, msg: str) -> None:
    if needle not in haystack:
        raise RuntimeError(f"{msg}: expected {needle!r} in {haystack!r}")


def main() -> int:
    load_dotenv(Path(".") / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    session_id = f"b2-volume-rehydrate-{int(time.time())}"
    marker = f"marker-{int(time.time())}"

    vol = WorkspaceVolume(
        name="ws-b2-rehydrate",
        mountpoint="/workspace",
        auto_create=True,
    )
    volumes = [vol.as_mount_dict()]

    print(f"[probe] session={session_id}")
    print(f"[probe] volume={vol.name} mountpoint={vol.mountpoint}")

    with SandboxPool.from_env() as pool, SessionManager(pool) as mgr:
        print(f"[probe] regional endpoint: {pool.clients.regional_endpoint}")

        # SessionManager forwards `volumes` straight to create_sandbox; it
        # does not auto-create workspace volumes. Ensure the volume exists.
        ensure_workspace_volume(
            pool.clients.client,
            pool.config.sandbox_group,
            vol,
            resource_group=pool.config.resource_group,
        )

        print("[probe] turn 1 (fresh): write marker into mounted volume")
        with mgr.session(session_id, volumes=volumes) as sbx_id:
            print(f"  sandbox: {sbx_id}")
            r = pool.exec(
                sbx_id,
                f"printf %s {marker!r} > /workspace/b2-marker.txt && "
                "cat /workspace/b2-marker.txt",
            )
            if r.exit_code != 0:
                raise RuntimeError(
                    f"turn1 write failed rc={r.exit_code} stderr={r.stderr!r}"
                )
            _must_contain(r.stdout, marker, "turn1 marker readback mismatch")
            print(f"  wrote marker: {marker}")

        print("[probe] turn 2 (rehydrate): read marker from mounted volume")
        with mgr.session(session_id, volumes=volumes) as sbx_id:
            print(f"  sandbox: {sbx_id}")
            r = pool.exec(sbx_id, "cat /workspace/b2-marker.txt")
            if r.exit_code != 0:
                raise RuntimeError(
                    f"turn2 read failed rc={r.exit_code} stderr={r.stderr!r}"
                )
            _must_contain(r.stdout, marker, "turn2 marker mismatch after rehydrate")
            print("  marker present after rehydrate")

        print("[probe] cleanup: discard session + snapshot")
        mgr.discard(session_id)

    print("[probe] PASS: snapshot rehydrate with volumes succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

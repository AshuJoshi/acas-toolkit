"""04 — Workspace volume demo (persistence across sandbox lifetimes).

Demonstrates the volume mount workflow:

* Create (or reuse) a named AzureBlob volume in the sandbox group.
* Lease a fresh sandbox with the volume mounted at ``/workspace``.
* Write a file inside ``/workspace`` and release the sandbox.
* Lease a **second** fresh sandbox and read the same file back.

The two sandboxes are independent — same group, same volume, different
``sandbox_id``. The volume is what makes the data survive.

Uses ``SandboxPool`` + ``ensure_workspace_volume`` directly. No agent
layer.

Run
---

::

    # First run creates the volume + writes the note.
    uv run python examples/04_workspace_volume.py --turn write

    # Second run leases a fresh sandbox and reads it back.
    uv run python examples/04_workspace_volume.py --turn read

    # Or run both in sequence (default).
    uv run python examples/04_workspace_volume.py
"""

from __future__ import annotations

import argparse
import sys
import time

from acas_toolkit import (
    SandboxPool,
    SandboxPoolConfig,
    WorkspaceVolume,
    ensure_workspace_volume,
)


WORKSPACE_DIR = "/workspace"
NOTE_PATH = f"{WORKSPACE_DIR}/note.txt"
DEFAULT_VOLUME_NAME = "ws-demo"


# ---------- per-turn workloads ---------------------------------------------

def _write_turn(pool: SandboxPool, sbx_id: str) -> None:
    payload = f"hello from write-turn at {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}"
    cmd = (
        f"mkdir -p {WORKSPACE_DIR} && "
        f"echo '{payload}' > {NOTE_PATH} && "
        f"ls -la {WORKSPACE_DIR} && "
        f"echo WROTE_OK"
    )
    result = pool.exec(sbx_id, cmd)
    print(f"    exit_code={result.exit_code}")
    print(f"    stdout:\n{(result.stdout or '').rstrip()}")
    if result.exit_code != 0:
        print(f"    stderr:\n{(result.stderr or '').rstrip()}", file=sys.stderr)
        raise SystemExit(2)


def _read_turn(pool: SandboxPool, sbx_id: str) -> None:
    cmd = f"ls -la {WORKSPACE_DIR} ; echo --- ; cat {NOTE_PATH}"
    result = pool.exec(sbx_id, cmd)
    print(f"    exit_code={result.exit_code}")
    print(f"    stdout:\n{(result.stdout or '').rstrip()}")
    if result.exit_code != 0:
        print(f"    stderr:\n{(result.stderr or '').rstrip()}", file=sys.stderr)
        raise SystemExit(2)


# ---------- turn driver ----------------------------------------------------

def _run_turn(turn: str, volume_name: str) -> None:
    cfg = SandboxPoolConfig.from_env()
    # Cold-only so each turn really does prove a fresh sandbox can
    # read what a different sandbox wrote.
    cfg = SandboxPoolConfig(
        subscription_id=cfg.subscription_id,
        resource_group=cfg.resource_group,
        sandbox_group=cfg.sandbox_group,
        location=cfg.location,
        warm_size=0,
        warm_disk=cfg.warm_disk,
    )
    volume = WorkspaceVolume(
        name=volume_name,
        mountpoint=WORKSPACE_DIR,
        auto_create=(turn == "write"),  # create on first run, expect-exists thereafter
    )

    print(f"\n=== turn: {turn} ===")
    print(f"    sandbox_group={cfg.sandbox_group}  region={cfg.location}")
    print(f"    volume={volume.name}  mountpoint={volume.mountpoint}")

    with SandboxPool(cfg) as pool:
        ensure_workspace_volume(
            pool.clients.client,
            cfg.sandbox_group,
            volume,
            resource_group=cfg.resource_group,
        )
        t0 = time.monotonic()
        with pool.lease(disk="python-3.13", volumes=[volume.as_mount_dict()]) as sbx_id:
            print(f"    sandbox: {sbx_id}  ({time.monotonic() - t0:.1f}s)")
            if turn == "write":
                _write_turn(pool, sbx_id)
            else:
                _read_turn(pool, sbx_id)


# ---------- main -----------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--turn",
        choices=["write", "read", "both"],
        default="both",
        help="Which half of the demo to run (default: both)",
    )
    parser.add_argument(
        "--volume",
        default=DEFAULT_VOLUME_NAME,
        help=f"Volume name (default: {DEFAULT_VOLUME_NAME!r})",
    )
    args = parser.parse_args()

    if args.turn in ("write", "both"):
        _run_turn("write", args.volume)
    if args.turn in ("read", "both"):
        _run_turn("read", args.volume)

    print("\n[demo] done.")


if __name__ == "__main__":
    main()

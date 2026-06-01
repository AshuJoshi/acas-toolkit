"""
Demo: warm sandbox pool.

Measures cold vs warm acquire latency. Opens a pool with ``warm_size=2``,
waits for both warmers to be ready, then acquires 3 sandboxes back-to-back.
The first two should hit the warm pool (~instant); the third falls back to
a cold ``create_sandbox`` because the warmer hasn't had time to refill.

    uv run python scripts/warm_pool_demo.py
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from acas_toolkit import SandboxPool, SandboxPoolConfig  # noqa: E402


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    cfg = SandboxPoolConfig.from_env()
    cfg.warm_size = 2
    cfg.warm_disk = "python-3.13"

    with SandboxPool(cfg) as pool:
        print(f"[demo] waiting for {cfg.warm_size} warm sandboxes ...")
        t0 = time.monotonic()
        pool.wait_warm(timeout=60.0)
        print(f"[demo] warm pool ready in {time.monotonic() - t0:.2f}s")

        acquired: list[str] = []
        for i in range(3):
            t = time.monotonic()
            sbx_id = pool.acquire(disk=cfg.warm_disk)
            elapsed = time.monotonic() - t
            acquired.append(sbx_id)
            print(f"[demo] acquire #{i + 1}: {sbx_id} in {elapsed * 1000:.0f} ms")

        # Quick exec smoke on each to prove they're real.
        for sbx_id in acquired:
            r = pool.exec(sbx_id, "echo warm && python3 --version")
            print(f"[demo] exec {sbx_id}: rc={r.exit_code} {r.stdout.strip()}")

        # Tear down explicitly (pool.close() would also handle it).
        for sbx_id in acquired:
            pool.release(sbx_id)

    return 0


if __name__ == "__main__":
    sys.exit(main())

"""03 — Egress policy demo.

Shows the three egress modes supported by ACAS sandboxes by leasing
three sandboxes back-to-back with different policies, exercising the
network from each, and reading the per-sandbox decisions log.

The sandboxes are created **directly** via ``SandboxPool.acquire`` —
no Agent Framework / Microsoft Foundry layer is involved here. That
keeps the demo small enough to read in one sitting.

Variants
--------
1. ``baseline`` — no policy. Defaults to "Allow" (open egress). The
   ``pip install`` and the ``curl example.com`` both succeed.
2. ``deny-by-default`` — every host is blocked. Both calls fail.
3. ``deny+pypi-allowlist`` — ``deny`` baseline plus an allowlist for
   the PyPI hosts. The ``pip install`` succeeds; the
   ``curl example.com`` still fails.

Step 4 reads ``client.get_egress_decisions(sbx_id)`` and prints the
``allowed`` / ``denied`` counts for each variant. The ``denied`` side
is reliable; the ``allowed`` side is sparse on the ``0.1.0b1`` PyPI
build (see the ``Known SDK quirk`` note on
``acas_toolkit.data_client.GroupClientAdapter.get_egress_decisions``)
— do not gate logic on the allowed count.

Run
---

::

    uv run python examples/03_egress_policy.py

Requires the standard ACAS env vars (``.env`` works). The third
variant downloads ``sympy`` from PyPI; expect ~15 s of network time.
"""

from __future__ import annotations

import time

from acas_toolkit import (
    EgressPolicyBuilder,
    SandboxPool,
    SandboxPoolConfig,
)


# ---------- workload --------------------------------------------------------

# Two probes: PyPI (sympy install) and a non-PyPI host (example.com).
# Each probe runs whether or not the policy allows it; we read the exit
# code to decide pass/fail. ``-q`` keeps pip output short.
_PIP_CMD = "pip install --quiet --no-input sympy >/tmp/pip.out 2>&1; echo PIP_EXIT=$?"
_CURL_CMD = (
    "curl -sS -o /dev/null -w 'CURL_HTTP=%{http_code}\\n' "
    "--max-time 5 https://example.com/ ; echo CURL_EXIT=$?"
)


def _run_workload(pool: SandboxPool, sbx_id: str) -> dict[str, str]:
    """Run both probes and return their trailing lines."""
    pip = pool.exec(sbx_id, _PIP_CMD)
    curl = pool.exec(sbx_id, _CURL_CMD)
    return {
        "pip": (pip.stdout or "").strip().splitlines()[-1] if pip.stdout else "(no output)",
        "curl": (curl.stdout or "").strip().splitlines()[-1] if curl.stdout else "(no output)",
    }


# ---------- audit ----------------------------------------------------------

def _print_audit(pool: SandboxPool, sbx_id: str) -> None:
    """Print the per-sandbox egress decisions log.

    The proxy flushes asynchronously; we sleep a beat before reading.
    """
    time.sleep(2.0)
    client = pool.clients.client  # GroupClientAdapter
    try:
        decisions = client.get_egress_decisions(sbx_id)
    except Exception as exc:  # noqa: BLE001 — informational only
        print(f"    audit unavailable: {type(exc).__name__}: {exc}")
        return
    ne = getattr(decisions, "network_egress", None)
    allowed = list(getattr(ne, "allowed", []) or []) if ne else []
    denied = list(getattr(ne, "denied", []) or []) if ne else []
    print(f"    audit: allowed={len(allowed)}  denied={len(denied)}")
    for entry in allowed[:3]:
        host = getattr(entry, "host", "?")
        path = getattr(entry, "path", "?")
        print(f"      allow {host}{path}")
    for entry in denied[:3]:
        host = getattr(entry, "host", "?")
        path = getattr(entry, "path", "?")
        print(f"      deny  {host}{path}")


# ---------- variant driver -------------------------------------------------

def _run_variant(pool: SandboxPool, name: str, policy: dict | None) -> None:
    print(f"\n=== variant: {name} ===")
    if policy is None:
        print("    policy: (none — default Allow)")
        kwargs: dict = {"disk": "python-3.13"}
    else:
        print(f"    policy: {policy}")
        kwargs = {"disk": "python-3.13", "egress_policy": policy}
    t0 = time.monotonic()
    with pool.lease(**kwargs) as sbx_id:
        print(f"    sandbox: {sbx_id}  ({time.monotonic() - t0:.1f}s)")
        out = _run_workload(pool, sbx_id)
        print(f"    pip:  {out['pip']}")
        print(f"    curl: {out['curl']}")
        _print_audit(pool, sbx_id)


# ---------- main -----------------------------------------------------------

def main() -> None:
    # Cold-only — every variant gets a fresh sandbox so the egress
    # policy applies from the first packet. (Warm sandboxes are
    # pre-created with the warm policy.)
    cfg = SandboxPoolConfig.from_env()
    cfg = SandboxPoolConfig(
        subscription_id=cfg.subscription_id,
        resource_group=cfg.resource_group,
        sandbox_group=cfg.sandbox_group,
        location=cfg.location,
        warm_size=0,
        warm_disk=cfg.warm_disk,
    )
    print(f"[demo] subscription={cfg.subscription_id}")
    print(f"[demo] sandbox_group={cfg.sandbox_group}  region={cfg.location}")

    pypi_allowlist = (
        EgressPolicyBuilder()
        .deny_by_default()
        .allow_hosts(["pypi.org", "files.pythonhosted.org"])
        .build()
    )
    deny_all = EgressPolicyBuilder().deny_by_default().build()

    with SandboxPool(cfg) as pool:
        _run_variant(pool, "baseline (no policy)", None)
        _run_variant(pool, "deny-by-default", deny_all)
        _run_variant(pool, "deny + pypi allowlist", pypi_allowlist)

    print("\n[demo] done.")


if __name__ == "__main__":
    main()

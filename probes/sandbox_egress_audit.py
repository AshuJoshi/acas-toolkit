"""
Probe B5: empirical search for an ACAS egress-audit-log enable knob.

Background
----------
On 2026-05-18 the egress spike observed that ``SandboxClient.get_egress_decisions``
returned a sparse list (1 allowed entry, 0 denied) after a workload that
issued dozens of HTTPS requests under a deny-by-default policy. The portal
has a "Network Audit" tab that appears to show the full log, so the
data clearly exists server-side — the SDK endpoint seems to be either
sampling, deduplicating, or gated behind an un-documented telemetry flag.

The SDK's ``create_sandbox`` accepts a ``telemetry_config: dict | None``
which ships as ``body["telemetryConfig"]``, but the shape is undocumented.

This probe tries a small matrix of ``telemetry_config`` payloads against a
deterministic workload (5 allowed + 5 denied HTTPS requests with distinct
paths to defeat host-level deduplication) and reports, for each variant,
how many decisions the SDK returns.

Outcomes
--------
- If a variant unblocks the log → document the shape, wire an opt-in into
  the toolkit, close B5 as a config (not a bug).
- If no variant unblocks the log → file a bug with the ACAS preview team
  attaching this probe output as the repro.

Run
---
    uv run python probes/sandbox_egress_audit.py | tee temp/b5-2026-05-22/probe.txt

This script intentionally bypasses the warm pool (every variant needs a
fresh sandbox) and deletes every sandbox it creates.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from acas_toolkit import (  # noqa: E402
    EgressPolicyBuilder,
    SandboxPool,
    SandboxPoolConfig,
)


# Deterministic workload. Distinct paths so host-level dedup can't hide entries.
ALLOWED_REQUESTS: list[tuple[str, str]] = [
    ("pypi-root",     "https://pypi.org/"),
    ("pypi-simple",   "https://pypi.org/simple/"),
    ("pypi-robots",   "https://pypi.org/robots.txt"),
    ("pypi-sitemap",  "https://pypi.org/sitemap.xml"),
    ("pypi-help",     "https://pypi.org/help/"),
]
DENIED_REQUESTS: list[tuple[str, str]] = [
    ("ex-root",       "https://example.com/"),
    ("ex-a",          "https://example.com/a"),
    ("ex-b",          "https://example.com/b"),
    ("ex-c",          "https://example.com/c"),
    ("ex-d",          "https://example.com/d"),
]

CURL = (
    "curl --silent --show-error --output /dev/null "
    "--max-time 10 --write-out '%{{http_code}}' {url}"
)

# Variants of telemetry_config to probe. The first entry is the baseline
# (no telemetry_config) — must match yesterday's sparse result.
VARIANTS: list[tuple[str, dict | None]] = [
    ("baseline (no telemetry_config)", None),
    ("enabled-true",                   {"enabled": True}),
    ("egressDecisions-enabled",        {"egressDecisions": {"enabled": True}}),
    ("audit-enabled",                  {"audit": {"enabled": True}}),
    ("networkAudit-enabled",           {"networkAudit": {"enabled": True}}),
    ("verbose-true",                   {"verbose": True}),
    ("sampling-rate-1",                {"sampling": {"rate": 1.0}}),
    ("diagnosticSettings-verbose",     {"diagnosticSettings": {"egressAudit": "Verbose"}}),
    # Second pass — server told us telemetryConfig requires `endpoints`.
    # Peel the schema one layer deeper.
    ("endpoints-empty",                {"endpoints": []}),
    ("endpoints-url-only",             {"endpoints": [{"url": "https://example.com"}]}),
    ("endpoints-otlp",                 {"endpoints": [{"type": "otlp", "url": "https://example.com"}]}),
    ("endpoints-audit-egress",         {"endpoints": [{"type": "audit", "scope": "egress"}]}),
]


def _run_workload(pool: SandboxPool, sbx_id: str) -> dict:
    """Issue 5 allowed + 5 denied HTTPS requests; return per-request HTTP codes."""
    out = {"allowed": {}, "denied": {}}
    for label, url in ALLOWED_REQUESTS:
        r = pool.exec(sbx_id, CURL.format(url=url))
        out["allowed"][label] = {
            "exit_code": r.exit_code,
            "http_code": (r.stdout or "").strip(),
            "stderr_snippet": (r.stderr or "").strip()[:120],
        }
    for label, url in DENIED_REQUESTS:
        r = pool.exec(sbx_id, CURL.format(url=url))
        out["denied"][label] = {
            "exit_code": r.exit_code,
            "http_code": (r.stdout or "").strip(),
            "stderr_snippet": (r.stderr or "").strip()[:120],
        }
    return out


def _probe_variant(pool: SandboxPool, name: str, telemetry: dict | None) -> dict:
    """Spin a fresh locked-down sandbox, run workload, pull audit, return summary."""
    cfg = pool.config
    client = pool.clients.client
    policy = EgressPolicyBuilder.pip_allowlist().build()

    kwargs = {"disk": "python-3.13", "egress_policy": policy}
    if telemetry is not None:
        kwargs["telemetry_config"] = telemetry

    print(f"\n=== variant: {name} ===")
    print(f"  telemetry_config={telemetry!r}")

    create_error: str | None = None
    sbx_id: str | None = None
    workload: dict | None = None
    decisions_raw: dict | None = None
    allowed_count = denied_count = -1

    t0 = time.monotonic()
    try:
        sbx_id = pool.acquire(**kwargs)
        print(f"  sandbox: {sbx_id}  ({time.monotonic() - t0:.1f}s)")

        workload = _run_workload(pool, sbx_id)
        allowed_codes = [v["http_code"] for v in workload["allowed"].values()]
        denied_codes = [v["http_code"] for v in workload["denied"].values()]
        print(f"  workload allowed HTTP codes: {allowed_codes}")
        print(f"  workload denied  HTTP codes: {denied_codes}")

        # Give the proxy a moment to flush.
        time.sleep(2.0)

        decisions = client.get_egress_decisions(sbx_id, cfg.sandbox_group)
        ne = decisions.network_egress
        allowed_count = len(ne.allowed) if ne else 0
        denied_count = len(ne.denied) if ne else 0
        # Best-effort: also stash the raw shape for the report.
        decisions_raw = {
            "allowed": [
                {"host": e.host, "path": e.path, "method": e.method}
                for e in (ne.allowed if ne else [])
            ],
            "denied": [
                {"host": e.host, "path": e.path, "method": e.method}
                for e in (ne.denied if ne else [])
            ],
        }
        print(f"  audit log: allowed={allowed_count}  denied={denied_count}")
    except Exception as e:  # noqa: BLE001  — capture & continue across variants
        create_error = f"{type(e).__name__}: {e}"
        print(f"  ERROR: {create_error}")
    finally:
        if sbx_id is not None:
            try:
                pool.release(sbx_id)
            except Exception as e:  # noqa: BLE001
                print(f"  (release error: {e!r})")

    return {
        "name": name,
        "telemetry_config": telemetry,
        "error": create_error,
        "sandbox_id": sbx_id,
        "allowed_count": allowed_count,
        "denied_count": denied_count,
        "workload": workload,
        "decisions": decisions_raw,
    }


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("azure").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    cfg = SandboxPoolConfig.from_env()
    cfg = SandboxPoolConfig(
        subscription_id=cfg.subscription_id,
        resource_group=cfg.resource_group,
        sandbox_group=cfg.sandbox_group,
        location=cfg.location,
        warm_size=0,           # cold path only — every variant is a fresh sandbox
        warm_disk=cfg.warm_disk,
    )
    print(f"[probe] subscription={cfg.subscription_id}")
    print(f"[probe] rg={cfg.resource_group} group={cfg.sandbox_group} region={cfg.location}")
    print(f"[probe] variants: {len(VARIANTS)}")

    results: list[dict] = []
    with SandboxPool(cfg) as pool:
        for name, telemetry in VARIANTS:
            results.append(_probe_variant(pool, name, telemetry))

    # Final matrix
    print("\n" + "=" * 70)
    print("RESULTS MATRIX (workload was 5 allowed + 5 denied requests)")
    print("=" * 70)
    print(f"{'variant':<38} {'allowed':>10} {'denied':>10}  error")
    print("-" * 70)
    for r in results:
        a = r["allowed_count"] if r["error"] is None else "—"
        d = r["denied_count"] if r["error"] is None else "—"
        err = "" if r["error"] is None else r["error"][:60]
        print(f"{r['name']:<38} {str(a):>10} {str(d):>10}  {err}")

    print("\nInterpretation:")
    print("  - allowed=5 denied=5 → variant unblocked full audit log.")
    print("  - allowed<5 or denied<5 (and no error) → still sparse; variant ineffective or partially effective.")
    print("  - error → server rejected the telemetry_config shape (informative — field is known).")


if __name__ == "__main__":
    main()

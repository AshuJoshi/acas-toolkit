"""
C7.2 V3 — standalone /acas zero-cost smoke (offline, no AF, no OTel SDK).

The layering invariant (per diary 2026-05-27 #16 D-C7.1): a user who
``from acas_toolkit import SandboxPool, SessionManager`` and
does NOT configure any OTel SDK or call
``configure_observability(...)`` should:

1. Be able to import + use the toolkit normally (no AF / no Azure SDK
   surface poked).
2. Pay **zero** runtime cost on the OTel surface — every span the
   toolkit creates internally is a ``NonRecordingSpan`` (the OTel
   API's no-op default).
3. See no exceptions, no warnings about missing providers.

This script asserts (1)–(3) without ever calling
``configure_observability``. It uses a minimal fake clients shim so it
runs entirely offline.

Run::

    uv run python scripts/standalone_acas_zerocost_smoke.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ------- 1. Import the toolkit. No `configure_observability` call. --
# Bare imports — no `agentic_platforms.af.obs`, no
# `azure.monitor.opentelemetry`, no `agent_framework.observability`.
from opentelemetry import trace  # noqa: E402
from opentelemetry.trace import NonRecordingSpan, ProxyTracer  # noqa: E402

from acas_toolkit import (  # noqa: E402
    SandboxPool,
    SandboxPoolConfig,
)
from acas_toolkit._telemetry import get_tracer  # noqa: E402

failures = 0


def _assert(cond: bool, label: str) -> int:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}")
    return 0 if cond else 1


# ------- 2. Imports surface check ----------------------------------
# If the toolkit reached into AF or azure-monitor as a side effect,
# `sys.modules` would carry them. Standalone users must NOT pay that.
print("\n[zerocost] 1. Import-surface check (no AF / no azure-monitor)")
failures += _assert(
    "agent_framework" not in sys.modules,
    "`agent_framework` not in sys.modules after importing /acas",
)
failures += _assert(
    "agent_framework.observability" not in sys.modules,
    "`agent_framework.observability` not loaded",
)
failures += _assert(
    "azure.monitor.opentelemetry" not in sys.modules,
    "`azure.monitor.opentelemetry` not loaded",
)
failures += _assert(
    "agentic_platforms.af" not in sys.modules,
    "`agentic_platforms.af` not loaded (we only imported /acas)",
)


# ------- 3. Tracer is the OTel ProxyTracer (no SDK configured) -----
print("\n[zerocost] 2. Global OTel tracer is the no-op ProxyTracer")
tracer = get_tracer()
# When no SDK is registered the global provider is a ProxyTracerProvider,
# which hands out ProxyTracers. Once an SDK is installed they delegate
# to the real provider; until then every span is a NonRecordingSpan.
failures += _assert(
    isinstance(tracer, ProxyTracer),
    f"_telemetry.get_tracer() returned ProxyTracer "
    f"(got {type(tracer).__name__})",
)


# ------- 4. Spans created by /acas are NonRecordingSpan ------------
print("\n[zerocost] 3. Spans emitted by /acas are NonRecordingSpan (no-op)")

from types import SimpleNamespace
from acas_toolkit.sandbox_factory import SandboxClients


class _StubControl:
    """Minimal stub matching the methods the pool's span paths touch."""

    def create_sandbox(self, group, **kwargs):
        return SimpleNamespace(id=sbx_id_fixed)

    def delete_sandbox(self, sbx_id, group):
        return None

    def exec(self, sbx_id, group, command):
        # Duck-typed result — pool.exec only reads .exit_code /
        # .stdout / .stderr. Avoids constructing the pydantic
        # ExecResult model and its required fields.
        return SimpleNamespace(exit_code=0, stdout="hi\n", stderr="")

    @property
    def _dp(self):
        return SimpleNamespace(close=lambda: None)


sbx_id_fixed = "00000000-0000-0000-0000-000000000001"

# Capture every span the pool opens via a tap on the tracer.
captured_spans: list = []

real_start = tracer.start_as_current_span

import contextlib


@contextlib.contextmanager
def _tap_start_as_current_span(*args, **kwargs):
    with real_start(*args, **kwargs) as s:
        captured_spans.append(s)
        yield s


tracer.start_as_current_span = _tap_start_as_current_span  # type: ignore[method-assign]

cfg = SandboxPoolConfig(
    subscription_id="sub-stub",
    resource_group="rg-stub",
    location="westus2",
    sandbox_group="sg-stub",
    warm_size=0,
)
pool = SandboxPool(cfg)
stub = _StubControl()
pool._clients = SandboxClients(  # type: ignore[assignment]
    client=stub,  # type: ignore[arg-type]
    mgmt=SimpleNamespace(),  # type: ignore[arg-type]
    regional_endpoint="https://stub.invalid",
    sandbox_group="sg-stub",
)
try:
    sbx_id = pool.acquire(disk="ubuntu")
    pool.exec(sbx_id, "echo hi")
    pool.release(sbx_id)
finally:
    tracer.start_as_current_span = real_start  # type: ignore[method-assign]
    pool.close()

failures += _assert(
    len(captured_spans) >= 3,
    f"toolkit emitted ≥3 spans during acquire/exec/release "
    f"(got {len(captured_spans)})",
)
failures += _assert(
    all(isinstance(s, NonRecordingSpan) for s in captured_spans),
    "every emitted span is a NonRecordingSpan (no SDK = no-op)",
)


# ------- 5. Spans are silently dropped, no errors / warnings -------
print("\n[zerocost] 4. No exporter side-effects (no SDK = no flush, no errors)")
# `force_flush` is on SDK providers only; ProxyTracerProvider doesn't
# have one. If we accidentally installed an SDK it would be present.
provider = trace.get_tracer_provider()
failures += _assert(
    not hasattr(provider, "force_flush"),
    f"global TracerProvider is NOT an SDK provider "
    f"(type={type(provider).__name__}; no force_flush attr)",
)


# ------- final ------------------------------------------------------
print()
if failures:
    print(f"[zerocost] {failures} assertion(s) FAILED")
    sys.exit(1)
print("[zerocost] all assertions PASS — /acas standalone pays zero OTel cost")
sys.exit(0)

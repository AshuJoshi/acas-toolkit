"""Toolkit-level OpenTelemetry helpers — private, framework-agnostic.

Why this module exists
----------------------

The toolkit core (``acas_toolkit/``) is framework-agnostic. The
OTel **API** (``opentelemetry-api``) is a vendor-neutral
instrumentation surface — not Azure-specific, not tied to any
agent framework — and qualifies under the same rule as ``pydantic``:
a curated dep that all serious Python SDKs share.

This module gives the rest of the toolkit two things:

* :func:`get_tracer` — a tracer scoped to ``"acas_toolkit"`` so all
  our spans are attributable to one library. Cached.
* :func:`sensitive_data_enabled` — reads ``ENABLE_SENSITIVE_DATA``
  from the env on every call (runtime flips work). Integrations
  that bridge into a higher-level framework set this env var when
  the caller passes ``enable_sensitive_data=True`` so explicit
  kwargs win over env at startup, then everyone reads the env.

Zero-cost when nothing is configured
------------------------------------

If no OTel SDK is installed/configured, ``get_tracer(...).start_as_current_span(...)``
returns a ``NonRecordingSpan`` — a no-op. Toolkit users who don't
care about tracing pay zero overhead. Users who configure a global
OTel provider (via ``azure-monitor-opentelemetry``, an exporter, or
their own wiring) get the spans automatically.

Underscore prefix
-----------------

Module is private (``_telemetry``) because the public surface for
configuring observability is the OTel SDK itself plus your exporter
of choice — end-users should not have to reach into the toolkit's
internals to enable tracing. Internal toolkit modules import from here.
"""

from __future__ import annotations

import os
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Tracer

#: Truncation limit for sensitive string attributes (code, stdout,
#: stderr, shell commands). 4KB stays well inside Azure Monitor's
#: ~8KB per-attribute cap and prevents trace storage blow-up on
#: noisy `pip install` output.
_SENSITIVE_ATTR_MAX_BYTES = 4096

#: Env var name we honor for the sensitive-data gate. Shared with
#: ``agent_framework.observability`` so the user has one knob across
#: both the toolkit and the agent framework integration.
SENSITIVE_DATA_ENV = "ENABLE_SENSITIVE_DATA"

#: Tracer name used for all ``acas.*`` spans. Showing up in App
#: Insights / Jaeger / Tempo under this library name makes it easy to
#: filter to "our" spans separately from a calling framework's spans
#: (e.g. ``agent_framework.*``).
_TRACER_NAME = "acas_toolkit"

_tracer: Tracer | None = None


def get_tracer() -> Tracer:
    """Return the cached tracer for ``acas.*`` spans.

    Lazy because ``trace.get_tracer(...)`` looks up the global provider;
    importing this module before the provider is set would bind to the
    default no-op proxy permanently. Calling lazily means we bind once
    the user's OTel setup (e.g. ``configure_azure_monitor(...)`` from
    ``azure-monitor-opentelemetry``, or any other exporter wiring) has run.
    """
    global _tracer
    if _tracer is None:
        _tracer = trace.get_tracer(_TRACER_NAME)
    return _tracer


def sensitive_data_enabled() -> bool:
    """Return True if sensitive span attributes should be emitted.

    Reads :data:`SENSITIVE_DATA_ENV` from the env on every call —
    runtime flips work. Recognized truthy values: ``"1"``,
    ``"true"``, ``"yes"``, ``"on"`` (case-insensitive). Everything
    else, including unset, returns ``False``.
    """
    raw = os.environ.get(SENSITIVE_DATA_ENV, "")
    return raw.strip().lower() in ("1", "true", "yes", "on")


def set_sensitive_attr(span: Any, key: str, value: str | None) -> None:
    """Set ``key=value`` on ``span`` only if sensitive data is enabled.

    Truncates to :data:`_SENSITIVE_ATTR_MAX_BYTES` bytes (UTF-8) and
    appends ``"…[truncated N bytes]"`` so consumers know the cap fired.

    No-op on ``None`` or empty values; no-op when sensitive data is
    off. Safe to call unconditionally from the emit sites.
    """
    if value is None or value == "":
        return
    if not sensitive_data_enabled():
        return
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) > _SENSITIVE_ATTR_MAX_BYTES:
        # Slice on a byte boundary, then re-decode tolerantly so we
        # don't split a multibyte codepoint and produce invalid UTF-8.
        truncated = encoded[:_SENSITIVE_ATTR_MAX_BYTES].decode(
            "utf-8", errors="ignore"
        )
        dropped = len(encoded) - _SENSITIVE_ATTR_MAX_BYTES
        value = f"{truncated}…[truncated {dropped} bytes]"
    span.set_attribute(key, value)


__all__ = [
    "SENSITIVE_DATA_ENV",
    "get_tracer",
    "sensitive_data_enabled",
    "set_sensitive_attr",
]

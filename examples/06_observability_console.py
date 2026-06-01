"""
Example 06 — Observability with the OTel console exporter.

Wires OpenTelemetry to print spans to stdout. Every ACAS SDK call (lease,
exec, snapshot, etc.) becomes a span you can read in your terminal.

Run::

    uv run python examples/06_observability_console.py

For Azure Application Insights export, see example 07.
"""

from __future__ import annotations

import logging

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
)

from acas_toolkit import SandboxPool, SandboxPoolConfig
from acas_toolkit.executor import ExecRequest, execute


def configure_console_otel() -> None:
    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    configure_console_otel()

    cfg = SandboxPoolConfig.from_env()
    with SandboxPool(cfg) as pool:
        with pool.lease() as sbx_id:
            for snippet in (
                "print('hello from span 1')",
                "import sys; print(sys.version)",
                "print(2 ** 10)",
            ):
                result = execute(
                    ExecRequest(language="python", code=snippet),
                    pool=pool,
                    sbx_id=sbx_id,
                )
                print(f"[demo] -> {result.stdout.strip()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

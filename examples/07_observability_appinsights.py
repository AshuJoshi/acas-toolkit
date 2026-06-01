"""07 — Observability with Azure Application Insights.

Wires OpenTelemetry to Azure Monitor / Application Insights via the
official ``azure-monitor-opentelemetry`` distro, then runs a short
``SandboxPool`` workload so the ``acas.sandbox.*`` spans show up in
the App Insights ``dependencies`` table.

No agent layer. Uses ``SandboxPool`` + ``pool.exec`` directly so the
demo is provably attributable to the toolkit (rather than to an SDK
on top of it).

Prereqs
-------

* ``APPLICATIONINSIGHTS_CONNECTION_STRING`` set (the deploy outputs
  put this in ``.env`` — ``SandboxPool.from_env`` will source it via
  ``python-dotenv``).
* ``pip install azure-monitor-opentelemetry`` (already a dep of this
  repo).

Run
---

::

    uv run python examples/07_observability_appinsights.py

The script prints the OTel ``trace_id`` and a KQL query you can
paste into the App Insights ``Logs`` blade to find the run.
Telemetry takes 1–3 minutes to surface in App Insights.
"""

from __future__ import annotations

import logging
import os
import sys
import time

from opentelemetry import trace

from acas_toolkit import SandboxPool, SandboxPoolConfig


TRACER_NAME = "acas_toolkit.examples.observability_appinsights"


def _load_dotenv_soft() -> None:
    """Best-effort ``.env`` load so we can read APPLICATIONINSIGHTS_CONNECTION_STRING
    *before* ``SandboxPoolConfig.from_env()`` would have loaded it for us.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


def _configure_appinsights() -> None:
    conn = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not conn:
        print(
            "ERROR: APPLICATIONINSIGHTS_CONNECTION_STRING is not set.\n"
            "       Source your .env (see infra/README.md) or set it manually.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    # azure-monitor-opentelemetry installs the AzureMonitorTraceExporter
    # behind a default TracerProvider/BatchSpanProcessor in one call.
    from azure.monitor.opentelemetry import configure_azure_monitor

    configure_azure_monitor(connection_string=conn)


def _workload(pool: SandboxPool) -> None:
    with pool.lease(disk="python-3.13") as sbx_id:
        # A handful of short execs — each emits an ``acas.sandbox.exec``
        # child span under our root span. The exec spans carry
        # ``acas.exec.exit_code``, ``acas.exec.duration_ms`` and the
        # sandbox id, which is what you'll query for in App Insights.
        for snippet in (
            "echo hello-from-appinsights-demo",
            "python -c 'print(2 + 2)'",
            "python -c 'import platform; print(platform.python_version())'",
        ):
            result = pool.exec(sbx_id, snippet)
            print(f"    [{snippet}] -> exit={result.exit_code}  {(result.stdout or '').strip()}")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    _load_dotenv_soft()
    _configure_appinsights()

    cfg = SandboxPoolConfig.from_env()
    cfg = SandboxPoolConfig(
        subscription_id=cfg.subscription_id,
        resource_group=cfg.resource_group,
        sandbox_group=cfg.sandbox_group,
        location=cfg.location,
        warm_size=0,  # cold lease so the spans cover the full lifecycle
        warm_disk=cfg.warm_disk,
    )
    print(f"[demo] sandbox_group={cfg.sandbox_group}  region={cfg.location}")

    tracer = trace.get_tracer(TRACER_NAME)
    with tracer.start_as_current_span("examples.07_observability_appinsights") as root:
        ctx = root.get_span_context()
        trace_id_hex = format(ctx.trace_id, "032x")
        print(f"[demo] trace_id={trace_id_hex}")
        t0 = time.monotonic()
        with SandboxPool(cfg) as pool:
            _workload(pool)
        print(f"[demo] workload took {time.monotonic() - t0:.1f}s")

    # Force-flush so we don't lose spans on a quick exit.
    provider = trace.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush(timeout_millis=10_000)

    print(
        "\n[demo] done. Open Application Insights → Logs and run:\n"
        f"  union dependencies, traces\n"
        f"  | where timestamp > ago(30m)\n"
        f"  | where operation_Id == '{trace_id_hex}' or customDimensions['acas.sandbox.id'] != ''\n"
        f"  | order by timestamp asc\n"
        "  (Telemetry typically appears within 1–3 minutes.)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

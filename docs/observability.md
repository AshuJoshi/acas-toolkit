# Observability

ACAS Toolkit is instrumented with OpenTelemetry out of the box. Every
public API call (lease, release, execute, snapshot, etc.) emits a span.

## Console exporter (local dev)

See `examples/06_observability_console.py`. Wire a `ConsoleSpanExporter`
and every span lands on stdout.

## Azure Application Insights (production)

See `examples/07_observability_appinsights.py`. Set
`APPLICATIONINSIGHTS_CONNECTION_STRING` and call
`configure_azure_monitor()` once at startup — every subsequent ACAS call
becomes a trace you can query in the App Insights portal.

## Span anatomy

The tracer name is `acas_toolkit` — every span this library emits is
attributable to that one library so you can filter to it separately
from whatever framework is calling it.

Each public call produces one parent span with attributes:

* `acas.sandbox.id` — the sandbox the call targets
* `acas.sandbox.disk` — the disk image (e.g. `python-3.13`)
* `acas.exec.exit_code` — for `exec` / `execute_code` calls
* `acas.exec.duration_ms` — server-reported execution time
* `acas.exec.status` — `"ok"` (exit_code == 0) or `"error"` otherwise

Sensitive attributes are gated on `ENABLE_SENSITIVE_DATA=1` and
truncated to 4 KB:

* `acas.exec.cmd` — the shell command sent to the sandbox
* `acas.exec.code` — the Python source (set by `execute_code`)
* `acas.exec.stdout` / `acas.exec.stderr` — captured output

The HTTP call to the ACAS data plane is a child span, so you can see
network latency separately from execution time.

## Querying

In the App Insights *Logs* blade, every toolkit span lands in the
`dependencies` table. A minimal slice:

```kusto
dependencies
| where customDimensions.tracerName == "acas_toolkit"
| project timestamp, name, duration,
          sbx=tostring(customDimensions["acas.sandbox.id"]),
          exit=toint(customDimensions["acas.exec.exit_code"]),
          status=tostring(customDimensions["acas.exec.status"])
| order by timestamp desc
```

Filter on `name == "acas.exec"` for execution spans, or
`customDimensions["acas.exec.status"] == "error"` for failures. The
attribute namespaces (`acas.sandbox.*`, `acas.exec.*`,
`acas.session.*`) are intentionally stable across releases so saved
queries keep working.

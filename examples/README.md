# Examples

Each script is self-contained and demonstrates one capability of the
toolkit. Run them in order — the concepts compound.

## Setup checklist

Before running any example:

1. Deploy the Bicep (see [`infra/README.md`](../infra/README.md)).
2. Copy [`.env.example`](../.env.example) to `.env` at the repo root
   and fill in the values from the deployment outputs.
3. Install the toolkit: `uv sync --extra dev`.

The examples auto-load `.env` via `python-dotenv`, so as long as
`.env` lives at the repo root you do not need to `source` it manually.

## Example index

### 01 — Minimal call

[`01_minimal.py`](01_minimal.py)

Lease one sandbox, run `print("hello")`, release it. The smallest
possible round-trip end-to-end. Use this to verify your `.env` is
correct.

Requires: core ACAS env vars only.

### 02 — Warm pool

[`02_warm_pool.py`](02_warm_pool.py)

Pre-create `N` sandboxes at startup so the first `lease()` is
sub-second instead of paying ~10 s of cold-start. Drives the same
`pool.lease()` API as example 01 — the pool just hands you a
pre-warmed one when available.

Requires: core ACAS env vars only. Set `ACAS_WARM_SIZE` to the
desired pool depth (the example also accepts `--warm N`).

### 03 — Egress policy

[`03_egress_policy.py`](03_egress_policy.py)

Three sandboxes leased back-to-back, each with a different egress
policy: baseline (open), deny-by-default, and deny + PyPI allowlist.
Each variant runs the same `pip install` and `curl example.com` and
prints the allowed / denied counts from `get_egress_decisions`.

Requires: core ACAS env vars. The third variant downloads `sympy`
from PyPI (~15 s of network time).

### 04 — Workspace volume

[`04_workspace_volume.py`](04_workspace_volume.py)

Mounts an Azure Files share at `/work` inside every leased sandbox.
Run with `--turn write` to drop a file, then `--turn read` (against a
brand-new sandbox) to read it back — proves the volume is durable
across sandbox lifetimes.

Requires: core ACAS env vars. Also calls `ensure_workspace_volume`,
which provisions the file share on first run if it does not exist.

### 05 — Session snapshots

[`05_session_snapshots.py`](05_session_snapshots.py)

Pause and resume a sandbox session. The example installs `sympy` in
session `s5-demo`, snapshots it, exits, and on the next run rehydrates
the snapshot — `sympy` is still importable without re-installing.

Requires: core ACAS env vars. Override the session id with
`--session <id>` or `ACAS_SESSION_ID`. Use `--discard` to clean up.

### 06 — Observability (console)

[`06_observability_console.py`](06_observability_console.py)

Wires an OpenTelemetry `ConsoleSpanExporter`. Every public toolkit
call lands as a JSON span on stdout — lease, execute, release, etc.
Easiest way to inspect the attribute names and span hierarchy.

Requires: core ACAS env vars only.

### 07 — Observability (Application Insights)

[`07_observability_appinsights.py`](07_observability_appinsights.py)

Same demo as 06 but ships spans to Azure Application Insights via
`azure-monitor-opentelemetry`. Prints the `trace_id` and a copy-paste
KQL query so you can verify the span landed in the portal.

Requires: core ACAS env vars **plus**
`APPLICATIONINSIGHTS_CONNECTION_STRING` (provided by the Bicep when
`enableObservability=true`).

### 08 — CodeAct agent

[`08_codeact_agent.py`](08_codeact_agent.py)

End-to-end: a Microsoft Agent Framework `ChatAgent` backed by a
Foundry-hosted `gpt-5-mini` deployment, with `execute_code` and
`run_shell` tools that run inside an ACAS sandbox. Ask the agent to
compute something or inspect a file — it writes Python, the executor
runs it in a clean subprocess, the agent reads the result, and the
loop continues.

Requires: core ACAS env vars **plus** `AZURE_AI_PROJECT_ENDPOINT` and
`AZURE_AI_MODEL_DEPLOYMENT_NAME` (provided by the Bicep when
`enableFoundry=true`).

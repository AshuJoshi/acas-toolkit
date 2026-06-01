# ACAS Toolkit

[![CI](https://github.com/AshuJoshi/acas-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/AshuJoshi/acas-toolkit/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

Build agents that run code in **Azure Container Apps Sandboxes** (ACAS), you can read about them: [ACA Sandboxes](./docs/ACASandboxes.md)

ACAS Toolkit is a thin, framework-agnostic Python layer over the ACAS
preview SDK. It gives you a pooled sandbox client, a CodeAct-style
executor that hands each call a clean interpreter, durable workspace
volumes, session snapshots, declarative egress policy, and
out-of-the-box OpenTelemetry traces — so your agent can treat ACAS as
a black-box "run this code somewhere safe" backend.

---

## Table of contents

1. [Why CodeAct + ACAS?](#why-codeact--acas)
2. [What this toolkit gives you](#what-this-toolkit-gives-you)
3. [What is CodeAct? (under the hood)](#what-is-codeact-under-the-hood)
4. [Prerequisites](#prerequisites)
5. [Setup, step by step](#setup-step-by-step)
6. [Examples](#examples)
7. [Repository tour](#repository-tour)
8. [Architecture](#architecture)
9. [Project status](#project-status)
10. [Roadmap](#roadmap)
11. [Contributing & license](#contributing--license)

---

## Why CodeAct + ACAS?

There are two ways to give an agent capability:

1. **A catalog of 5–10 tools** — `read_file`, `write_file`, `http_get`,
   `set_egress`, `run_sql`, … The LLM picks one per turn and emits a
   JSON tool call.
2. **One tool: `execute_code(code: str)`** — the LLM emits a Python
   snippet; the runtime runs it in a sandbox. Anything the agent wants
   to do, it does *inside* that Python.

The second shape is the **CodeAct pattern**
([Wang et al., 2024](https://arxiv.org/abs/2402.01030)). It wins on
five things that compound:

* **The attack surface shrinks ~10×.** One tool means one entry point
  to threat-model — and one route on whatever HTTP surface eventually
  fronts the agent (`POST /sessions/{id}/exec_python` instead of one
  route per primitive).
* **Python is the LLM's strongest channel.** Frontier models were
  trained on more Python than they were on any particular tool-call
  schema. Composition (`for x in xs: …`), conditionals, error
  handling, intermediate variables, library calls — all free,
  expressed in the language the model knows best.
* **The combinatorial blow-up disappears.** "Read these five files,
  grep for X, write a summary" is one snippet, one round-trip, one
  model call — not five `read_file` + five `regex_match` + one
  `write_file`. Latency and token cost grow with task complexity,
  not linearly with primitives.
* **Errors arrive as tracebacks the model already understands.** When
  a `dict` lookup raises `KeyError`, the model sees the traceback and
  knows immediately what went wrong, because Python tracebacks are
  dense in its training data — unlike a runtime's structured tool
  error.
* **"Tools" become Python libraries.** Want SQL? Make `psycopg2`
  importable and let the agent write `cursor.execute(…)`. Want
  HTTP? `requests`. Want data? `pandas`. The tool ecosystem becomes
  *what is installed in the sandbox image* — a problem your
  packaging system already solves.

The standard objection — *"giving the model `exec` is too
dangerous"* — assumes there's nothing strong underneath the
interpreter. With ACAS there is: each session runs in its own VM
with its own kernel, filesystem, network tap, and egress proxy. The
sandbox boundary holds, which lets the safety story live there
instead of in ten hand-curated tool gates. That is why this toolkit
picks the CodeAct shape as its default — ACAS is what makes the
shape credible.

## What this toolkit gives you

The ACAS data and control-plane SDK (`azure-containerapps-sandbox`) is
powerful but low-level: it exposes sandbox groups, per-sandbox clients,
shell-command execution, file APIs, snapshots, ingress, egress audit,
volumes, and so on. Wiring all of that together for an agent that
"just wants to run some code" is repetitive and easy to get wrong.

This toolkit packages the patterns we kept re-implementing:

* A **pool** that owns the sandbox-group client, handles warm-pool
  pre-creation, leases sandboxes to callers, and releases them safely.
* A **CodeAct executor** that runs each Python (or shell) call as a
  fresh subprocess inside a long-lived sandbox — fast like a REPL,
  isolated like a serverless function.
* A **session manager** that pairs a logical session id with a real
  sandbox id, durable across process restarts via a JSON store.
* A **workspace volume** helper that mounts an Azure Files share into
  every sandbox at `/work` so files survive across calls and sessions.
* An **egress policy builder** that produces the JSON shape the ACAS
  control plane expects (deny-by-default, host allowlists, etc.).
* **OpenTelemetry instrumentation** with a stable `acas_toolkit`
  tracer name and `acas.sandbox.*` / `acas.exec.*` / `acas.session.*`
  attribute namespaces, exportable to console or Azure Application
  Insights.
* **Agent Framework tools** (`execute_code`, `run_python`,
  `run_pytest`, `run_shell`) so a Microsoft Agent Framework agent can
  call into the toolkit with no glue code.

Everything is plain Python with no FastAPI, no Cosmos, no broker — if
you want a hosted multi-tenant service, that is on the roadmap as a
separate sample.

## What is CodeAct? (under the hood)

The value-prop case is in [§Why CodeAct + ACAS?](#why-codeact--acas).
This section covers one implementation detail you will hit as soon as
you wire `execute_code` into a real loop.

A CodeAct session needs to be **stateful within a session but
stateless between calls**: `pip install` and file writes from one turn
should be visible in the next turn (so the sandbox keeps living), but
module-level globals, `sys.modules` patches, and signal handlers must
*not* leak between tool calls (so the interpreter must be fresh each
time).

[`acas_toolkit.executor.execute`](acas_toolkit/executor.py) handles
that by writing each call to `/tmp/<uuid>.py` and running it as a
brand-new subprocess inside the long-lived sandbox. The sandbox's
filesystem and installed packages persist; the interpreter does not.
See [`acas_toolkit/executor.py`](acas_toolkit/executor.py) for the
failure mapping (timeout, OOM, executor crash).

[`examples/08_codeact_agent.py`](examples/08_codeact_agent.py) shows
the end-to-end loop: a Microsoft Agent Framework agent backed by a
Foundry-hosted `gpt-5-mini` deployment, with `execute_code` and
`run_shell` wired through ACAS.

## Prerequisites

| You need | Why | How to get it |
|---|---|---|
| An Azure subscription with **ACAS preview enabled** | The sandbox group is a preview resource type (`Microsoft.App/sandboxGroups`) | See the [ACA sandboxes overview](./docs/ACASandboxes.md). |
| Permission to deploy at **subscription scope** | The Bicep creates a resource group, so it must run as a subscription-scope deployment | `Contributor` on the subscription, or a custom role that includes `Microsoft.Resources/subscriptions/resourceGroups/write` |
| Azure CLI 2.60+ | Used by the deployment command below | <https://learn.microsoft.com/cli/azure/install-azure-cli> |
| [`uv`](https://docs.astral.sh/uv/) | Resolves and installs the Python deps (locked to the preview SDK build) | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Python **3.11+** | Toolkit requirement (`requires-python = ">=3.11"`) | Already on most modern distros / macOS / WSL |

Optional, only if you plan to run specific examples:

| Optional | Needed by | Why |
|---|---|---|
| Application Insights workspace (provisioned by the Bicep when `enableObservability=true`, on by default) | Example 07 | OTel exporter target |
| Foundry account + `gpt-5-mini` deployment (provisioned by the Bicep when `enableFoundry=true`, on by default) | Example 08 | The LLM the CodeAct agent talks to |

## Setup, step by step

### 1. Deploy the Azure resources

The examples will not run without them. The Bicep creates a single
resource group with the sandbox group, storage account (for workspace
volumes and session snapshots), a user-assigned managed identity,
optionally Log Analytics + App Insights, and optionally a Foundry
account + project + `gpt-5-mini` deployment.

```bash
az login
az account set --subscription <your-subscription-id>

az deployment sub create \
    --name acas-toolkit-bootstrap \
    --location westus2 \
    --template-file infra/main.bicep \
    --parameters infra/main.parameters.json \
    --parameters environmentName=dev
```

Takes ~5–10 minutes. The Foundry model deployment is the slowest
step. See [`infra/README.md`](infra/README.md) for every parameter and
what each module creates.

If you do not want Foundry / App Insights:

```bash
--parameters enableFoundry=false enableObservability=false
```

### 2. Capture deployment outputs into `.env`

```bash
cp .env.example .env
az deployment sub show \
    --name acas-toolkit-bootstrap \
    --query properties.outputs -o json
```

Copy values from the JSON into `.env`. The mapping is documented in
[`infra/README.md`](infra/README.md#outputs--env). The required keys
are:

* `ACAS_SUBSCRIPTION_ID`, `ACAS_RESOURCE_GROUP`, `ACAS_LOCATION`,
  `ACAS_SANDBOX_GROUP` — every example
* `APPLICATIONINSIGHTS_CONNECTION_STRING` — example 07 only
* `AZURE_AI_PROJECT_ENDPOINT`, `AZURE_AI_MODEL_DEPLOYMENT_NAME` —
  example 08 only

### 3. Install the toolkit

```bash
uv sync --extra dev
```

This resolves `azure-containerapps-sandbox==0.1.0b1` from PyPI (no
vendored wheel) plus the OpenTelemetry, Agent Framework, and dev
dependencies.

### 4. Run the first example

```bash
uv run python examples/01_minimal.py
```

If you see a printed result and no traceback, you are good. Walk
through the remaining examples in order (see below).

### 5. (Optional) Run the test suite

```bash
uv run python -m pytest -q
```

The tests are import-only smoke tests — they do not hit Azure.

## Examples

Each file is self-contained. Run them in order; each one introduces one
concept.

| # | File | What it shows |
|---|---|---|
| 01 | [`examples/01_minimal.py`](examples/01_minimal.py) | First ACAS call — lease a sandbox, run code, release. |
| 02 | [`examples/02_warm_pool.py`](examples/02_warm_pool.py) | Eliminate cold-start latency with a warm pool. |
| 03 | [`examples/03_egress_policy.py`](examples/03_egress_policy.py) | Pin network egress with a declarative allow-list. |
| 04 | [`examples/04_workspace_volume.py`](examples/04_workspace_volume.py) | Persistent `/work` across calls (agent writes a file, reads it next turn). |
| 05 | [`examples/05_session_snapshots.py`](examples/05_session_snapshots.py) | Pause and resume a sandbox session via snapshots. |
| 06 | [`examples/06_observability_console.py`](examples/06_observability_console.py) | OTel console exporter — see spans on stdout. |
| 07 | [`examples/07_observability_appinsights.py`](examples/07_observability_appinsights.py) | OTel → Azure Application Insights. |
| 08 | [`examples/08_codeact_agent.py`](examples/08_codeact_agent.py) | End-to-end: Foundry-backed Agent Framework agent calling `execute_code` and `run_shell` tools that run inside ACAS. |

See [`examples/README.md`](examples/README.md) for per-example
prerequisites and command-line flags.

## Repository tour

```
acas-toolkit/
├── acas_toolkit/                 # the Python package (this is the library)
├── examples/                     # 8 self-contained runnable demos
├── probes/                       # diagnostic scripts for a live sandbox group
├── infra/                        # Bicep for one-command provisioning
├── docs/                         # reference documentation
├── tests/                        # import-only smoke tests
├── .env.example                  # template for the env vars the examples read
├── pyproject.toml                # project metadata and pinned deps
├── CONTRIBUTING.md               # how to file issues / open PRs
└── README.md                     # this file
```

### `acas_toolkit/` — the library

| Module | Purpose |
|---|---|
| [`__init__.py`](acas_toolkit/__init__.py) | Public re-exports. Import from `acas_toolkit`, not the submodules. |
| [`sandbox_pool.py`](acas_toolkit/sandbox_pool.py) | `SandboxPool` + `SandboxPoolConfig`. Owns the group client, warm pool, leases. |
| [`sandbox_factory.py`](acas_toolkit/sandbox_factory.py) | `SandboxClients` bundle + `make_sandbox_client` helper. |
| [`control_client.py`](acas_toolkit/control_client.py) | `make_control_client` and regional endpoint resolution. |
| [`data_client.py`](acas_toolkit/data_client.py) | `make_data_client` and the `GroupClientAdapter` that bridges the new SDK shape back to the toolkit's stable call surface. |
| [`executor.py`](acas_toolkit/executor.py) | `ExecRequest` → `ExecResult` CodeAct executor (fresh subprocess per call). |
| [`session_manager.py`](acas_toolkit/session_manager.py) | Pairs a logical session id with a real sandbox id; survives process restarts. |
| [`session_store.py`](acas_toolkit/session_store.py) | Default JSON-on-disk store backing `SessionManager`. Path: `~/.acas-toolkit/sessions.json` (override with `ACAS_SESSION_STORE_PATH`). |
| [`workspace.py`](acas_toolkit/workspace.py) | `WorkspaceVolume` + `ensure_workspace_volume` for the Azure Files share mounted at `/work`. |
| [`egress.py`](acas_toolkit/egress.py) | `EgressPolicyBuilder` — emits the JSON the control plane expects. |
| [`types.py`](acas_toolkit/types.py) | Shared dataclasses / enums (`ExecStatus`, etc.). |
| [`_telemetry.py`](acas_toolkit/_telemetry.py) | OTel tracer setup; attribute helpers; sensitive-data gating. |
| [`integrations/agent_framework/`](acas_toolkit/integrations/agent_framework/) | Agent Framework tool wrappers (`execute_code`, `run_python`, `run_pytest`, `run_shell`). |

### `infra/` — one-command provisioning

| File | Purpose |
|---|---|
| [`main.bicep`](infra/main.bicep) | Subscription-scope entry point. Creates the RG and invokes `resources.bicep`. |
| [`resources.bicep`](infra/resources.bicep) | Resource-group-scope orchestration. Wires the modules together. |
| [`main.parameters.json`](infra/main.parameters.json) | Default parameter values. Override with `--parameters key=value` on the CLI. |
| [`modules/sandboxgroup.bicep`](infra/modules/sandboxgroup.bicep) | The `Microsoft.App/sandboxGroups` resource itself. |
| [`modules/storage.bicep`](infra/modules/storage.bicep) | Storage account + Azure Files share for workspace volumes and snapshots. |
| [`modules/identity.bicep`](infra/modules/identity.bicep) | User-assigned managed identity (for future RBAC scenarios). |
| [`modules/monitoring.bicep`](infra/modules/monitoring.bicep) | Log Analytics workspace + Application Insights. Gated by `enableObservability`. |
| [`modules/foundry.bicep`](infra/modules/foundry.bicep) | Foundry account + project + `gpt-5-mini` deployment. Gated by `enableFoundry`. |
| [`README.md`](infra/README.md) | Per-resource explainer + Bicep-outputs → `.env` mapping. |

### `examples/` — eight runnable demos

See the [Examples](#examples) table above. Each script ends with a
shebang-style usage block in its module docstring.

### `probes/` — diagnostic scripts

| Script | Purpose |
|---|---|
| [`sandbox_introspect.py`](probes/sandbox_introspect.py) | Dump kernel, network interfaces, mounted volumes, disk image, available toolchains. |
| [`sandbox_egress_audit.py`](probes/sandbox_egress_audit.py) | Verify what the configured egress policy actually allows and blocks. |

These are not part of the library — they exist to answer "is the
sandbox actually behaving the way I think it is?" questions.

### `docs/` — reference documentation

| File | Purpose |
|---|---|
| [`README.md`](docs/README.md) | Index. |
| [`ACASandboxes.md`](docs/ACASandboxes.md) | Overview of Azure Container Apps Sandboxes — what they are and the platform features the toolkit builds on. |
| [`observability.md`](docs/observability.md) | The OTel spans the toolkit emits, console + App Insights wiring, sample KQL. |

### `tests/`

[`test_imports.py`](tests/test_imports.py) — import-only smoke tests
plus one regression guard for `GroupClientAdapter.get_egress_decisions`.
They do not hit Azure.

## Architecture

```
your laptop                         azure
┌─────────────────────────┐         ┌──────────────────────────────┐
│  your agent (any        │  https  │  ACA Sandbox Group           │
│  framework, or your     │ ──────► │   ├─ sandbox A (python-3.13) │
│  own loop)              │         │   ├─ sandbox B (python-3.13) │
│   │                     │         │   └─ ...                     │
│   └─ acas_toolkit:      │         │                              │
│       SandboxPool       │         │  Storage Account             │
│       execute()         │         │   ├─ workspace volume (/work)│
│       SessionManager    │         │   └─ session snapshots       │
│       EgressPolicyBuilder         └──────────────────────────────┘
│       (OTel built in)   │
└─────────────────────────┘
```

The toolkit **core** (`acas_toolkit/`) is framework-agnostic. The
`acas_toolkit.integrations.agent_framework` sub-package adds thin tool
wrappers for Microsoft's Agent Framework — used by examples 04, 05, 08.

## Project status

Public preview. APIs may change before 1.0. The pinned ACAS SDK
(`azure-containerapps-sandbox==0.1.0b1`) is itself a preview build;
when it graduates to GA the toolkit will be re-verified and the
`prerelease = "allow"` flag in `pyproject.toml` dropped.

CI runs `ruff check` + an import smoke suite on every push and PR to
`main` ([workflow](./.github/workflows/ci.yml)); the badge at the top
of this README reflects its current state.

## Roadmap

The toolkit shipped today is the first layer; two more are planned,
both rooted in the same `SandboxPool` / `SessionManager` primitives.

| Milestone | What it adds | Status |
|---|---|---|
| **`AcasProvider`** | A Microsoft Agent Framework `ContextProvider` that owns a `SandboxPool`, drives per-session sandbox snapshot + rehydrate off the AF session id, and subsumes the tool wrappers in `acas_toolkit.integrations.agent_framework`. | Planned — pending an upstream AF proposal for session-lifecycle hooks. |
| **Agent harness reference app** | A small FastAPI service that runs **inside a dedicated "harness" ACAS sandbox** and exposes `/sessions`, `/runs`, `/runs/{id}/events` (SSE), and `/runs/{id}/approvals/{aid}`. Consumes `AcasProvider` internally. | Planned — depends on `AcasProvider` + ACAS SDK GA. |

Beyond those: more integrations (`integrations/openai_agents/`,
`integrations/langchain/`, …) as the communities surface adapters.

## Contributing & license

* Bug reports and PRs: [GitHub Issues](https://github.com/AshuJoshi/acas-toolkit/issues).
* Contributing guide: [`CONTRIBUTING.md`](CONTRIBUTING.md).
* Code of conduct: [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).
* Security policy: [`SECURITY.md`](SECURITY.md).
* License: MIT — see [`LICENSE`](LICENSE).

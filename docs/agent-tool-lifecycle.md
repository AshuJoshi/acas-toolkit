# Agent ↔ Tool ↔ Sandbox lifecycle

When an LLM-driven agent uses the toolkit's tool wrappers (for example
`run_shell`, `execute_code`, `run_python`, `run_pytest`), three
different things are happening at three different layers. This page
unpacks them so you can answer:

* **What does the model actually see?** Which strings reach the LLM?
* **What happens when the model picks a tool?** Does the toolkit spin
  up a sandbox per tool call?
* **When is a sandbox actually created — and is it cold or warm?**

The reference example throughout is
[`examples/08_codeact_agent.py`](../examples/08_codeact_agent.py),
which leases one sandbox, gives a Foundry-hosted Agent Framework agent
two tools bound to it, and asks the agent to `pip install sympy` and
factor `2**64 - 1`.

---

## Layer 1 — What the model sees

On every turn, the agent runtime sends three strings (plus prior
messages) to the model:

1. **Developer system instructions** — written by you.
   In example 08 these live in the `Agent(instructions=...)` argument:

   > *"You are a code-execution agent with two tools: `run_shell` for
   > shell commands (installs, file inspection, package management) and
   > `execute_code` for Python 3 code (computation, library use).
   > `execute_code` returns a typed ExecResult (status, exit_code,
   > stdout, stderr, duration_ms) — inspect `status` first. Use the
   > right tool for the job. Tool state (installed packages, files in
   > /tmp and /work) persists across calls in this conversation."*

2. **Per-tool descriptions** — written by the toolkit.
   Agent Framework introspects each tool's `@tool` decorator and its
   pydantic `Field(description=...)` and ships the result to the model
   as part of the function-calling schema. The toolkit's strings:

   | Tool | Description source |
   |---|---|
   | `run_shell` | [`run_shell.py`](../acas_toolkit/integrations/agent_framework/run_shell.py) — *"Shell command to execute in the sandbox … Has internet access. Use this to install packages, inspect the filesystem, or run non-Python interpreters. State persists across calls within the same agent run."* |
   | `execute_code` | [`execute_code.py`](../acas_toolkit/integrations/agent_framework/execute_code.py) — *"Python 3 source code to execute in the sandbox. Use print() for output. Each call runs in a FRESH Python subprocess (clean interpreter), but filesystem state in /work and any installed packages persist across calls within the same session."* |
   | `run_python` | [`run_python.py`](../acas_toolkit/integrations/agent_framework/run_python.py) — stringly-typed sibling of `execute_code`. |
   | `run_pytest` | [`run_pytest.py`](../acas_toolkit/integrations/agent_framework/run_pytest.py) — drives `pytest` over files the agent wrote. |

3. **The user prompt** — your actual ask.
   In example 08:

   > *"Use the run_shell tool to install the `sympy` package via
   > `pip install --quiet sympy`, then use execute_code to factor
   > 2\*\*64 - 1 using sympy.factorint. Report the factorization."*

That is the complete state the LLM operates on. **The toolkit does not
decide which tool to call — the model does**, based on those three
strings.

---

## Layer 2 — What happens when the model picks a tool

The toolkit does **not** spin up a sandbox per tool call. The sandbox
is leased once, before the agent even starts:

```python
with SandboxPool.from_env() as pool, pool.lease(disk="python-3.13") as sbx_id:
    execute_code = make_execute_code_tool(pool, sbx_id)
    run_shell    = make_run_shell_tool(pool, sbx_id)

    agent = Agent(client=..., instructions=..., tools=[execute_code, run_shell])
    result = await agent.run(PROMPT)
```

The two tools are **closures** that capture the already-leased
`sbx_id`. So when the model emits `run_shell("pip install --quiet sympy")`:

1. Agent Framework deserializes the tool call → invokes the closure with
   `command="pip install --quiet sympy"`.
2. The closure (a one-liner in
   [`run_shell.py`](../acas_toolkit/integrations/agent_framework/run_shell.py))
   calls `pool.exec(sbx_id, command)` — **no new sandbox**, just a
   `POST /exec` into the existing one.
3. The result (stdout, stderr, exit_code) is formatted and returned to
   the model. The model then decides what to do next.

Two tool calls → two `/exec` round-trips into the **same sandbox**.
That is why a package installed by call 1 is importable in call 2:
the filesystem is shared because it's the same container.

```
┌──────────┐  tool_call: run_shell("pip install sympy")    ┌──────────────┐
│          │ ────────────────────────────────────────────▶ │              │
│   LLM    │                                               │  Agent       │
│          │ ◀──────────────────────────────────────────── │  Framework   │
└──────────┘   tool_result: "exit_code=0\n--- stdout ---…" └──────┬───────┘
                                                                  │ pool.exec(sbx_id, cmd)
                                                                  ▼
                                                          ┌──────────────┐
                                                          │ ACAS         │
                                                          │ sandbox      │
                                                          │ (sbx_id)     │
                                                          └──────────────┘
```

---

## Layer 3 — Sandbox lifecycle: cold vs warm

This is the "from scratch or from a warm pool?" question. The
authoritative answer lives in
[`SandboxPool.acquire()`](../acas_toolkit/sandbox_pool.py).

```
acquire(disk=X, **kwargs)
│
├─ Is the warm pool enabled (warm_size > 0)
│  AND requested disk == warm_disk
│  AND no extra kwargs (egress_policy, snapshot_id, volumes, …)?
│  │
│  ├─ YES → try self._warm.get_nowait()
│  │        ├─ hit  → span attr  acas.sandbox.acquire.source = "warm"   (~50 ms)
│  │        └─ miss → fall through to cold
│  │
│  └─ NO  → cold path
│
└─ COLD: self.clients.client.create_sandbox(group, disk=X, **kwargs)
         span attr  acas.sandbox.acquire.source = "cold"   (~5–15 s)
```

Three things worth internalising:

* **Warm pool is opt-in.** `SandboxPool.from_env()` reads
  `ACAS_WARM_SIZE` from the environment; default is `0`
  (see [`.env.example`](../.env.example)). With size 0 there is no
  warmer thread and every `acquire` is cold.
  [`examples/02_warm_pool.py`](../examples/02_warm_pool.py) is the
  one example that opts in — running it shows the cold-vs-warm
  latency delta in your own telemetry.
* **Warm is bypassed whenever you customize the sandbox.** Pass
  `egress_policy=…`, `snapshot_id=…`, `volumes=…`, or any other kwarg
  → cold path. A warm sandbox is a generic blank with
  `defaultAction = Allow`; you can't reuse one if you want a
  locked-down or workspace-volume-mounted sandbox.
* **One sandbox per `lease()`, period.** `SandboxPool.lease()` is a
  context manager that does `acquire → yield sbx_id → release` in a
  `finally`. The sandbox is deleted when the `with` block exits.
  There is **no per-tool-call provisioning** and no implicit reuse
  across leases.

### Sequence: leased once, used many times

```
caller code               SandboxPool          ACAS control plane         sandbox container
─────────────             ──────────           ──────────────────         ─────────────────
pool.lease(disk=…) ┐
                   ├─acquire()──────────────▶  create_sandbox            (provisioning ~5-15s)
                   │                       ◀────────────────────────────  ready (sbx_id)
                   │
agent.run(prompt) ─┤
   tool call 1 ────┼─pool.exec(sbx_id,cmd)──▶ POST /sandboxes/{id}/exec ▶  run shell cmd
                   │                       ◀────────────────────────────  stdout/exit/etc.
   tool call 2 ────┼─pool.exec(sbx_id,…)───▶ POST /sandboxes/{id}/exec ▶  run code (state retained)
                   │                       ◀────────────────────────────  stdout/exit/etc.
                   │           …
exit `with` ───────┴─release()─────────────▶  delete_sandbox            ▶  torn down
```

---

## Common variants

| Want | How |
|---|---|
| Sub-second cold start for short-lived agent runs | Enable warm pool: `ACAS_WARM_SIZE=2`, `ACAS_WARM_DISK=python-3.13`. See [`examples/02_warm_pool.py`](../examples/02_warm_pool.py). |
| Locked-down egress from packet zero | Pass `egress_policy=…` to `lease(...)`. Warm pool is intentionally bypassed (see acquire decision tree). [`examples/03_egress_policy.py`](../examples/03_egress_policy.py). |
| Persistent files across runs | Mount a workspace volume: `pool.lease(volumes=[...])`. [`examples/04_workspace_volume.py`](../examples/04_workspace_volume.py). |
| One sandbox per user session that survives a restart | Use `SessionManager` instead of `pool.lease()` directly. [`examples/05_session_snapshots.py`](../examples/05_session_snapshots.py). |
| Approval gate on a tool call before it runs | Pass `approval_mode="always_require"` to `make_execute_code_tool(...)`. The model must emit an approval request that your caller must satisfy. |

---

## Where to read further

* [`examples/`](../examples/) — every example is one focused scenario.
* [`acas_toolkit/sandbox_pool.py`](../acas_toolkit/sandbox_pool.py) —
  `SandboxPool`, `acquire`, `release`, `lease`, warm-pool internals,
  `exec` passthrough.
* [`acas_toolkit/integrations/agent_framework/`](../acas_toolkit/integrations/agent_framework/) —
  the four tool wrappers; each is < 100 lines and the docstrings are
  the contract.
* [observability.md](observability.md) — the spans (`acas.sandbox.acquire`,
  `acas.sandbox.exec`, `acas.sandbox.release`, …) emitted by every
  step above, with the attributes you can query in KQL.

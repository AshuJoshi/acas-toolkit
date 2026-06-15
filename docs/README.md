# Docs index

* [ACA Sandboxes overview](ACASandboxes.md) — what Azure Container
  Apps Sandboxes are and the platform features the toolkit builds on.
* [Agent ↔ tool ↔ sandbox lifecycle](agent-tool-lifecycle.md) — what
  the model sees, what happens on each tool call, and when sandboxes
  are created (cold vs. warm).
* [Observability](observability.md) — OpenTelemetry spans the toolkit
  emits, console + Application Insights wiring, and the attributes you
  can query in KQL.

For narrower questions, the closest references in the codebase are:

* Example scripts under [`examples/`](../examples/) — each runs against
  a real sandbox and is the easiest way to see a feature in action.
* Probe scripts under [`probes/`](../probes/) — deeper, lower-level
  introspection that the docs above are based on.
* Module docstrings under [`acas_toolkit/`](../acas_toolkit/) — every
  public symbol has a docstring with the constraints we know about.

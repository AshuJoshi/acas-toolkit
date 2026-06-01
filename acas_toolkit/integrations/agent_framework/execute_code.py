"""
``execute_code`` — Agent Framework tool that returns a typed
:class:`acas_toolkit.types.ExecResult`.

The CodeAct ``execute_code`` tool that replaces the stringly-typed
:func:`make_run_python_tool` shim with a typed contract end-to-end.

Differences from :mod:`run_python`:

* **Clean interpreter state per call.** The underlying
  :func:`acas_toolkit.executor.execute` always spawns a fresh
  ``python3`` subprocess. ``run_python`` already did this implicitly
  because it wrote a fresh ``snippet.py`` and re-ran the interpreter,
  but the contract is now explicit.
* **Typed return.** The tool returns an :class:`ExecResult` pydantic
  model. AF serializes pydantic models for the model automatically, so
  the LLM sees structured JSON (status, exit_code, stdout, stderr,
  duration_ms) rather than a concatenated string. Downstream
  consumers can also handle :class:`ExecResult` directly via
  :attr:`status` rather than parsing free-form text.
* **Failure semantics.** Negative exit codes carry meaning — see
  :mod:`acas_toolkit.types`. The model can distinguish
  ``TIMEOUT`` from ``OOM`` from ``NONZERO_EXIT`` without parsing
  ``stderr``.

The factory pattern matches :mod:`run_python` and :mod:`run_shell`:
``(pool, sbx_id)`` is closed over at agent-construction time so the tool
stays stateless from the model's point of view.
"""

from __future__ import annotations

from typing import Annotated, Callable, Literal

from agent_framework import tool
from pydantic import Field

from acas_toolkit.executor import execute
from acas_toolkit.sandbox_pool import SandboxPool
from acas_toolkit.types import ExecRequest, ExecResult


def make_execute_code_tool(
    pool: SandboxPool,
    sbx_id: str,
    *,
    default_timeout_s: float | None = 60.0,
    approval_mode: Literal["never_require", "always_require"] = "never_require",
) -> Callable[..., ExecResult]:
    """Return an ``@tool``-decorated ``execute_code`` function bound to ``(pool, sbx_id)``.

    Parameters
    ----------
    pool:
        An opened :class:`SandboxPool`.
    sbx_id:
        Sandbox previously obtained from the pool / session manager.
    default_timeout_s:
        Default wall-clock timeout to apply when the model omits
        ``timeout_s``. The default of 60 s matches the
        :class:`ExecRequest` default. Pass ``None`` to disable the
        executor-side timeout (the platform may still enforce one).
    approval_mode:
        Forwarded to AF's ``@tool`` decorator. ``"never_require"``
        (default) is the dev / CodeAct path: the model invokes the tool
        directly. ``"always_require"`` opts in to AF's **native**
        approval round-trip, where the model emits an approval request
        that the caller must satisfy before the tool runs.
    """

    @tool(approval_mode=approval_mode)
    def execute_code(
        code: Annotated[
            str,
            Field(
                description=(
                    "Python 3 source code to execute in the sandbox. Use "
                    "print() for output. Each call runs in a FRESH Python "
                    "subprocess (clean interpreter), but filesystem state "
                    "in /work and any installed packages persist across "
                    "calls within the same session."
                ),
            ),
        ],
        timeout_s: Annotated[
            float | None,
            Field(
                default=None,
                description=(
                    "Wall-clock seconds before the executor kills the "
                    "process and reports status='timeout'. Omit to use "
                    "the tool's default."
                ),
            ),
        ] = None,
    ) -> ExecResult:
        """Execute Python 3 code in a sandbox and return a typed ExecResult."""
        request = ExecRequest(
            code=code,
            timeout_s=timeout_s if timeout_s is not None else default_timeout_s,
        )
        return execute(request, pool=pool, sbx_id=sbx_id)

    return execute_code


__all__ = ["make_execute_code_tool"]

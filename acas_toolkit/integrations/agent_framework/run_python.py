"""
``run_python`` — Agent Framework tool that executes Python in an ACA sandbox.

This is the simplest possible "code-act" tool: the model emits a Python
snippet, the toolkit runs it inside an isolated sandbox, and returns the
stdout / stderr / exit code as a string for the model to reason about.

The tool is built via a factory (:func:`make_run_python_tool`) rather than
being a plain module-level function because the underlying
:class:`~acas_toolkit.sandbox_pool.SandboxPool` and sandbox ID need to be
closed over at agent-construction time. Returning a freshly-decorated
function per agent run keeps the tool stateless from the model's point of
view while still binding it to the right sandbox.
"""

from __future__ import annotations

from typing import Annotated, Callable

from agent_framework import tool
from pydantic import Field

from acas_toolkit.sandbox_pool import SandboxPool


def make_run_python_tool(pool: SandboxPool, sbx_id: str) -> Callable[..., str]:
    """Return an ``@tool``-decorated function bound to ``(pool, sbx_id)``.

    Parameters
    ----------
    pool:
        An opened :class:`SandboxPool`.
    sbx_id:
        ID of a sandbox previously obtained from ``pool.acquire(...)``.
    """

    @tool(approval_mode="never_require")
    def run_python(
        code: Annotated[
            str,
            Field(
                description=(
                    "Python 3 source code to execute in an isolated Linux sandbox. "
                    "Use print() for output. The script runs with `python3`, has "
                    "internet access, and a fresh /tmp on each call. Standard "
                    "library only unless you install packages first via pip in a "
                    "previous call."
                ),
            ),
        ],
    ) -> str:
        """Execute Python 3 code in a sandbox and return stdout, stderr, and exit code."""
        pool.write_file(sbx_id, "/tmp/snippet.py", code.encode("utf-8"))
        # Pass ``code`` through to pool.exec so the original Python
        # source is attached as the sensitive ``acas.exec.code``
        # attribute on the ``acas.sandbox.exec`` span (per diary
        # 2026-05-27 #16 attribute table). Without this the
        # ``run_python`` path would silently omit the most useful
        # debug attribute — ``cmd`` only shows the wrapper invocation.
        result = pool.exec(sbx_id, "python3 /tmp/snippet.py", code=code)
        return (
            f"exit_code={result.exit_code}\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}\n"
        )

    return run_python


__all__ = ["make_run_python_tool"]

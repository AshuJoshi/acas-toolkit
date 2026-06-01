"""
``run_shell`` — Agent Framework tool that runs arbitrary shell commands
in an ACA sandbox.

Companion to :mod:`acas_toolkit.integrations.agent_framework.run_python`.
The agent should reach for this tool when it needs to:

* Install packages (``pip install ...``, ``apt-get install ...``).
* Inspect the filesystem or environment (``ls``, ``cat``, ``env``).
* Run non-Python interpreters (e.g. ``node script.js``).
* Drive ``curl`` / ``wget`` for ad-hoc HTTP.

Same factory pattern as ``run_python``: the underlying
:class:`SandboxPool` and sandbox ID are closed over at agent-construction
time so the tool itself stays stateless from the model's perspective.
"""

from __future__ import annotations

from typing import Annotated, Callable

from agent_framework import tool
from pydantic import Field

from acas_toolkit.sandbox_pool import SandboxPool


def make_run_shell_tool(pool: SandboxPool, sbx_id: str) -> Callable[..., str]:
    """Return an ``@tool``-decorated shell-exec function bound to ``(pool, sbx_id)``."""

    @tool(approval_mode="never_require")
    def run_shell(
        command: Annotated[
            str,
            Field(
                description=(
                    "Shell command to execute in the sandbox (run via the "
                    "sandbox's default shell). Has internet access. Use this "
                    "to install packages (e.g. `pip install sympy`), inspect "
                    "the filesystem, or run non-Python interpreters. State "
                    "persists across calls within the same agent run."
                ),
            ),
        ],
    ) -> str:
        """Run a shell command in the sandbox and return stdout, stderr, and exit code."""
        result = pool.exec(sbx_id, command)
        return (
            f"exit_code={result.exit_code}\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}\n"
        )

    return run_shell


__all__ = ["make_run_shell_tool"]

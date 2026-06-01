"""
``run_pytest`` — Agent Framework tool that runs pytest in a sandbox directory.

A thin convenience wrapper over :func:`make_run_shell_tool`. The agent
*could* just call ``run_shell`` with ``"cd /work && pytest -q"``, but
giving the model a semantically named ``run_pytest`` tool tends to make
"fix the failing tests" loops converge faster and produce cleaner
streaming traces (one tool name per intent).
"""

from __future__ import annotations

from typing import Annotated, Callable

from agent_framework import tool
from pydantic import Field

from acas_toolkit.sandbox_pool import SandboxPool


def make_run_pytest_tool(pool: SandboxPool, sbx_id: str) -> Callable[..., str]:
    """Return an ``@tool``-decorated pytest-runner bound to ``(pool, sbx_id)``."""

    @tool(approval_mode="never_require")
    def run_pytest(
        directory: Annotated[
            str,
            Field(
                description=(
                    "Directory to run pytest from (e.g. `/work`). The tool "
                    "will `cd` into it before invoking pytest."
                ),
            ),
        ] = "/work",
        args: Annotated[
            str,
            Field(
                description=(
                    "Extra arguments to pass to pytest, space-separated "
                    "(e.g. `-q`, `-x`, `-k test_name`)."
                ),
            ),
        ] = "-q",
    ) -> str:
        """Run pytest in the given directory and return stdout, stderr, exit code."""
        command = f"cd {directory} && python3 -m pytest {args}"
        result = pool.exec(sbx_id, command)
        return (
            f"exit_code={result.exit_code}\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}\n"
        )

    return run_pytest


__all__ = ["make_run_pytest_tool"]

"""
End-to-end demo: Agent Framework agent + ACA Sandboxes ``run_shell`` /
``execute_code`` tools.

A single sandbox is leased; the agent is given two tools bound to it, and
asked to install a package and use it. Demonstrates the core CodeAct
loop on top of ``SandboxPool`` + ``acas_toolkit.integrations.agent_framework``.

Env (loaded from ``.env``):
    AZURE_AI_PROJECT_ENDPOINT       Foundry project endpoint
    AZURE_AI_MODEL_DEPLOYMENT_NAME  Model deployment name (e.g. gpt-5-mini)
    ACAS_SUBSCRIPTION_ID            Subscription holding the sandbox group
    ACAS_RESOURCE_GROUP             Resource group holding the sandbox group
    ACAS_LOCATION                   Region (default: westus2)
    ACAS_SANDBOX_GROUP              Name of the sandbox group to lease from

Run:
    uv run python examples/08_codeact_agent.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

# Repo-root on sys.path so the package resolves when run as a script.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from agent_framework import Agent  # noqa: E402
from agent_framework.foundry import FoundryChatClient  # noqa: E402
from azure.identity import AzureCliCredential  # noqa: E402

from acas_toolkit import SandboxPool  # noqa: E402
from acas_toolkit.integrations.agent_framework import (  # noqa: E402
    make_execute_code_tool,
    make_run_shell_tool,
)


PROMPT = (
    "Use the run_shell tool to install the `sympy` package via "
    "`pip install --quiet sympy`, then use execute_code to factor "
    "2**64 - 1 using sympy.factorint. Report the factorization."
)


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    foundry_endpoint = os.environ["AZURE_AI_PROJECT_ENDPOINT"]
    model = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-5-mini")

    with SandboxPool.from_env() as pool, pool.lease(disk="python-3.13") as sbx_id:
        print(f"[demo] sandbox: {sbx_id} (disk=python-3.13)")
        print(f"[demo] regional endpoint: {pool.clients.regional_endpoint}")

        execute_code = make_execute_code_tool(pool, sbx_id)
        run_shell = make_run_shell_tool(pool, sbx_id)

        client = FoundryChatClient(
            project_endpoint=foundry_endpoint,
            model=model,
            credential=AzureCliCredential(),
        )
        agent = Agent(
            client=client,
            name="CodeActAgent",
            instructions=(
                "You are a code-execution agent with two tools: "
                "`run_shell` for shell commands (installs, file inspection, "
                "package management) and `execute_code` for Python 3 code "
                "(computation, library use). `execute_code` returns a typed "
                "ExecResult (status, exit_code, stdout, stderr, "
                "duration_ms) — inspect `status` first. Use the right tool "
                "for the job. Tool state (installed packages, files in /tmp "
                "and /work) persists across calls in this conversation."
            ),
            tools=[execute_code, run_shell],
        )

        print(f"[demo] prompt: {PROMPT}")
        result = await agent.run(PROMPT)
        print(f"[demo] agent reply:\n{result}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

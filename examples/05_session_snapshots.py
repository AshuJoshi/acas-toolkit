"""
Demo: durable agent sessions via snapshot/resume.

Run it twice with the same ``--session`` ID (or ``ACAS_SESSION_ID`` env)
and watch the second run resume from a snapshot of the first:

    # Run 1 — fresh sandbox; agent installs sympy.
    uv run python examples/05_session_snapshots.py --session demo-1 --turn install

    # Run 2 — sandbox hydrated from snapshot; sympy already there.
    uv run python examples/05_session_snapshots.py --session demo-1 --turn use

Cleanup (deletes snapshot and forgets the session):

    uv run python examples/05_session_snapshots.py --session demo-1 --discard

Locked-down posture
-------------------

Pass ``--locked-down`` to apply a zero-trust egress policy at sandbox
creation time — ``defaultAction = Deny`` plus an allowlist of just the
hosts ``pip install`` needs. The policy is set in a single round-trip
(it rides on the ``create_sandbox`` call) and applies to both fresh
sandboxes and snapshot rehydrates::

    uv run python examples/05_session_snapshots.py --session demo-1 --turn install --locked-down
    uv run python examples/05_session_snapshots.py --session demo-1 --turn use     --locked-down

Under ``--locked-down`` the agent can still ``pip install sympy`` (PyPI
is allowlisted) but any other outbound host returns HTTP 403.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from agent_framework import Agent  # noqa: E402
from agent_framework.foundry import FoundryChatClient  # noqa: E402
from azure.identity import AzureCliCredential  # noqa: E402

from acas_toolkit import EgressPolicyBuilder, SandboxPool, SessionManager  # noqa: E402
from acas_toolkit.integrations.agent_framework import make_run_python_tool, make_run_shell_tool  # noqa: E402


TURNS = {
    "install": (
        "Use run_shell to install the `sympy` package: "
        "`pip install --quiet sympy`. Confirm the install with "
        "`python3 -c 'import sympy; print(sympy.__version__)'` and report "
        "the version you installed."
    ),
    "use": (
        "Without re-installing anything, use run_python to import sympy "
        "and factor 2**32 - 1 with sympy.factorint. If the import fails, "
        "say so explicitly — do NOT install it."
    ),
}


async def run_turn(session_id: str, turn: str, *, stream: bool = True,
                   locked_down: bool = False) -> int:
    prompt = TURNS[turn]
    foundry_endpoint = os.environ["AZURE_AI_PROJECT_ENDPOINT"]
    model = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-5-mini")

    # Locked-down posture: deny-by-default + pip allowlist, applied at
    # sandbox creation (one round-trip, not N).
    egress_policy: dict | None = None
    if locked_down:
        egress_policy = EgressPolicyBuilder.pip_allowlist().build()
        print(f"[demo] locked-down egress policy: {egress_policy}")

    pool = SandboxPool.from_env()
    with SessionManager(pool) as mgr, mgr.session(session_id, egress_policy=egress_policy) as sbx_id:
        print(f"[demo] session={session_id} turn={turn}")
        print(f"[demo] sandbox: {sbx_id}")
        print(f"[demo] regional endpoint: {pool.clients.regional_endpoint}")

        run_python = make_run_python_tool(pool, sbx_id)
        run_shell = make_run_shell_tool(pool, sbx_id)

        client = FoundryChatClient(
            project_endpoint=foundry_endpoint,
            model=model,
            credential=AzureCliCredential(),
        )
        agent = Agent(
            client=client,
            name="SessionAgent",
            instructions=(
                "You are a code-execution agent with `run_shell` and "
                "`run_python` tools. Sandbox state (installed packages, "
                "files) persists across calls AND across conversations in "
                "this same session."
            ),
            tools=[run_python, run_shell],
        )

        print(f"[demo] prompt: {prompt}")
        if stream:
            print("[demo] agent reply (streaming):")
            await _stream_reply(agent, prompt)
            print()  # newline after streamed text
        else:
            result = await agent.run(prompt)
            print(f"[demo] agent reply:\n{result}")

    return 0


async def _stream_reply(agent: Agent, prompt: str) -> None:
    """Stream an agent response to stdout: incremental text + tool-call markers."""
    seen_calls: set[str] = set()
    seen_results: set[str] = set()
    stream = agent.run(prompt, stream=True)
    async for update in stream:
        for c in (update.contents or ()):
            ctype = getattr(c, "type", None)
            if ctype == "text":
                if c.text:
                    sys.stdout.write(c.text)
                    sys.stdout.flush()
            elif ctype == "function_call":
                call_id = c.call_id or ""
                if call_id and call_id not in seen_calls:
                    seen_calls.add(call_id)
                    sys.stdout.write(f"\n  → [tool call] {c.name}\n")
                    sys.stdout.flush()
            elif ctype == "function_result":
                call_id = c.call_id or ""
                if call_id and call_id not in seen_results:
                    seen_results.add(call_id)
                    status = "error" if getattr(c, "exception", None) else "ok"
                    sys.stdout.write(f"  ← [tool result {status}]\n")
                    sys.stdout.flush()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    parser = argparse.ArgumentParser()
    parser.add_argument("--session", default=os.environ.get("ACAS_SESSION_ID", "demo-1"))
    parser.add_argument("--turn", choices=list(TURNS.keys()), default="install")
    parser.add_argument("--discard", action="store_true",
                        help="Delete the session's snapshot and forget the session.")
    parser.add_argument("--no-stream", action="store_true",
                        help="Disable streaming output; print the full reply at end.")
    parser.add_argument("--locked-down", action="store_true",
                        help="Apply deny-by-default egress policy with a pip allowlist "
                             "at sandbox creation time (zero-trust posture).")
    args = parser.parse_args()

    if args.discard:
        with SandboxPool.from_env() as pool:
            mgr = SessionManager(pool)
            mgr.discard(args.session)
            print(f"[demo] discarded session {args.session!r}")
        return 0

    return asyncio.run(run_turn(
        args.session, args.turn,
        stream=not args.no_stream,
        locked_down=args.locked_down,
    ))


if __name__ == "__main__":
    sys.exit(main())

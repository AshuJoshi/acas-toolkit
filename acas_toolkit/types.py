"""
Backend-neutral contracts for the ``execute_code`` integration seam.

These types are the shared vocabulary callers use when running
agent-emitted code: ACAS today, alternative backends (a local-process
runner, third-party sandbox services, etc.) tomorrow. They live at
the package root so backends and consumers import from a single
neutral place rather than from one another:

* :func:`acas_toolkit.executor.execute` returns :class:`ExecResult`.
* :func:`acas_toolkit.integrations.agent_framework.make_execute_code_tool`
  surfaces it as an Agent Framework tool that returns
  :class:`ExecResult` directly (AF serializes pydantic models for the
  model).
* Any future ``LocalProcessProvider``-style backend would accept the
  same :class:`ExecRequest` and return the same :class:`ExecResult`,
  enabling parameterized parity tests.

This module is the upstream dependency of every backend and every
integration. Do not import from any of them here.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


#: Approval decision returned by an ``approval_policy(request)`` callable.
#: ``"allow"`` proceeds with execution; ``"deny"`` short-circuits with
#: a synthetic :class:`ExecResult` (``status=DENIED``,
#: ``exit_code=EXIT_DENIED``); ``"prompt"`` defers to a separately-
#: configured prompt function (a console helper ships today; future
#: integrations may route through SSE / WebSocket). Kept as a
#: ``Literal`` rather than an :class:`Enum` so callers can write the
#: bare strings inline without an import dance.
ApprovalDecision = Literal["allow", "deny", "prompt"]


# ---------------------------------------------------------------------------
# Failure-code constants
#
# Negative exit codes are reserved for executor-synthesized failures
# (the underlying process never sets them itself). Backends MUST stamp
# these values on :attr:`ExecResult.exit_code` when the corresponding
# condition is detected, so consumers can branch on a stable contract
# regardless of which backend ran the code.
# ---------------------------------------------------------------------------

#: Per-execution wall-clock timeout exceeded.
EXIT_TIMEOUT: int = -1

#: Out-of-memory kill (typically SIGKILL after cgroup hit memory.max).
EXIT_OOM: int = -2

#: Sandbox / executor crash, state drift, or any non-user-code failure
#: that prevented the executor from observing a real exit code.
EXIT_EXECUTOR_CRASH: int = -3

#: Denied by an ``approval_policy(request)`` callable before execution
#: started. No user code ran; ``stderr`` carries the human-readable
#: reason supplied by the policy.
EXIT_DENIED: int = -4


class ExecStatus(str, Enum):
    """High-level outcome of an :class:`ExecRequest`.

    Promotes the failure-mapping table above to a typed enum so
    consumers don't have to memorize the negative-int mapping. The
    relationship is::

        OK              ⇔ exit_code == 0
        NONZERO_EXIT    ⇔ exit_code  > 0
        TIMEOUT         ⇔ exit_code == EXIT_TIMEOUT          (-1)
        OOM             ⇔ exit_code == EXIT_OOM              (-2)
        EXECUTOR_CRASH  ⇔ exit_code == EXIT_EXECUTOR_CRASH   (-3)
        DENIED          ⇔ exit_code == EXIT_DENIED           (-4)

    Backends are responsible for setting both fields consistently.
    :meth:`ExecResult.from_exit_code` does the mapping in one place.
    """

    OK = "ok"
    NONZERO_EXIT = "nonzero_exit"
    TIMEOUT = "timeout"
    OOM = "oom"
    EXECUTOR_CRASH = "executor_crash"
    DENIED = "denied"


class ExecRequest(BaseModel):
    """Code-execution request handed to a backend.

    Python-only today; the ``language`` field is reserved for future
    multi-language support. ``timeout_s`` is enforced by the backend
    (ACAS wraps the command in ``timeout(1)``; a local-process backend
    would use ``subprocess.run(timeout=...)``); ``None`` means "no
    executor-side timeout" (the platform may still enforce one).
    """

    code: str = Field(
        description="Source code to execute. UTF-8. No length cap at the "
        "contract layer; backends may impose their own."
    )
    language: str = Field(
        default="python",
        description="Source language. Currently 'python' only.",
    )
    timeout_s: float | None = Field(
        default=60.0,
        ge=0.0,
        description="Wall-clock seconds before the backend kills the "
        "process and reports TIMEOUT. None disables executor-side "
        "timeout (the underlying platform may still enforce one).",
    )


class ExecResult(BaseModel):
    """Result of an :class:`ExecRequest`.

    The first three fields (``stdout`` / ``stderr`` / ``exit_code``)
    match the SDK's own ``ExecResult`` 1:1 so the translation from the
    raw SDK type is mechanical. ``status`` and ``duration_ms`` are
    executor-synthesized.
    """

    status: ExecStatus = Field(
        description="High-level outcome. Always set; derive structured "
        "failure handling from this rather than from exit_code alone."
    )
    exit_code: int = Field(
        description="Process exit code. Non-negative values come from "
        "the user's code; negative values are executor-synthesized per "
        "EXIT_TIMEOUT / EXIT_OOM / EXIT_EXECUTOR_CRASH."
    )
    stdout: str = Field(default="", description="Captured stdout.")
    stderr: str = Field(default="", description="Captured stderr.")
    duration_ms: int = Field(
        default=0,
        ge=0,
        description="Wall-clock duration of the executor call, in "
        "milliseconds. Includes time spent provisioning the subprocess "
        "but not time spent reaching the sandbox.",
    )
    truncated: bool = Field(
        default=False,
        description="True if stdout/stderr were truncated (e.g. on "
        "timeout the partial output is preserved and this is set).",
    )

    @classmethod
    def from_exit_code(
        cls,
        *,
        exit_code: int,
        stdout: str = "",
        stderr: str = "",
        duration_ms: int = 0,
        truncated: bool = False,
    ) -> "ExecResult":
        """Build an :class:`ExecResult` and derive :attr:`status` from ``exit_code``.

        The canonical way for backends to construct a result. Keeps the
        ``exit_code ↔ status`` mapping in one place so backends can't
        accidentally drift apart.
        """
        if exit_code == 0:
            status = ExecStatus.OK
        elif exit_code == EXIT_TIMEOUT:
            status = ExecStatus.TIMEOUT
        elif exit_code == EXIT_OOM:
            status = ExecStatus.OOM
        elif exit_code == EXIT_EXECUTOR_CRASH:
            status = ExecStatus.EXECUTOR_CRASH
        elif exit_code == EXIT_DENIED:
            status = ExecStatus.DENIED
        elif exit_code < 0:
            # Negative exit codes are reserved for executor-synthesized
            # failures (see module docstring). An unknown negative means a
            # backend introduced a new failure mode without updating the
            # mapping above, or the executor itself misbehaved. Either
            # way we don't have a real exit code from user code, so the
            # honest classification is EXECUTOR_CRASH ("we don't know
            # what happened") rather than NONZERO_EXIT ("user code
            # exited non-zero").
            status = ExecStatus.EXECUTOR_CRASH
        else:
            status = ExecStatus.NONZERO_EXIT
        return cls(
            status=status,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            truncated=truncated,
        )


__all__ = [
    "EXIT_TIMEOUT",
    "EXIT_OOM",
    "EXIT_EXECUTOR_CRASH",
    "EXIT_DENIED",
    "ApprovalDecision",
    "ExecStatus",
    "ExecRequest",
    "ExecResult",
]

"""
ACAS-backed implementation of the :class:`acas_toolkit.types.ExecRequest`
→ :class:`acas_toolkit.types.ExecResult` contract.

The executor gives each call a **clean interpreter state** — no
carry-over of module globals, locals, ``sys.modules`` patches, or
signal handlers between successive ``execute_code`` invocations on
the same logical session. This does NOT require a new VM per call;
a fresh OS subprocess inside the persistent sandbox is sufficient.

This module realizes that: every call writes the code to a per-call
file under ``/tmp/`` and runs ``python3 /tmp/<file>`` as a brand-new
subprocess in the sandbox. The sandbox's filesystem (``/work``,
installed packages) persists across calls; the interpreter does not.

Failure mapping:

* ``exit_code 0``                       → :attr:`ExecStatus.OK`
* ``exit_code > 0``                     → :attr:`ExecStatus.NONZERO_EXIT`
* ``coreutils timeout(1) killed it``    → ``EXIT_TIMEOUT`` (-1)
* ``SIGKILL / OOM`` (exit 137)          → ``EXIT_OOM`` (-2)
* SDK / sandbox crash before exit code  → ``EXIT_EXECUTOR_CRASH`` (-3)
"""

from __future__ import annotations

import logging
import shlex
import time
from uuid import uuid4

from acas_toolkit.sandbox_pool import SandboxPool
from acas_toolkit.types import (
    EXIT_EXECUTOR_CRASH,
    EXIT_OOM,
    EXIT_TIMEOUT,
    ExecRequest,
    ExecResult,
)


logger = logging.getLogger(__name__)


#: Exit code coreutils ``timeout(1)`` uses when it kills the child.
_COREUTILS_TIMEOUT_EXIT: int = 124

#: Exit code reported when a process is killed by SIGKILL (the default
#: kernel response to OOM in a memory cgroup).
_SIGKILL_EXIT: int = 137

#: Directory inside the sandbox used to stash per-call snippet files.
#: ``/tmp`` is always writable, exists on every disk image, and is the
#: conventional place for ephemeral content. We never clean files up
#: explicitly — the sandbox's ``/tmp`` is wiped at snapshot rehydrate.
_SNIPPET_DIR: str = "/tmp"


def execute(
    request: ExecRequest,
    *,
    pool: SandboxPool,
    sbx_id: str,
) -> ExecResult:
    """Run ``request.code`` in the sandbox and return a typed :class:`ExecResult`.

    A new subprocess is spawned for every call. The sandbox itself is
    reused — long-lived state in ``/work`` and any ``pip install`` done
    in a prior shell call is preserved.

    Parameters
    ----------
    request:
        The typed code-execution request. ``request.language`` must be
        ``"python"`` today; anything else raises ``ValueError``.
    pool:
        An opened :class:`SandboxPool`. The executor uses
        :meth:`SandboxPool.write_file` and :meth:`SandboxPool.exec`
        only — it does not own the sandbox lifecycle.
    sbx_id:
        A sandbox previously acquired (or rehydrated) for the caller
        via the pool or :class:`SessionManager`.
    """
    if request.language != "python":
        # Python is the only supported language today. Raising rather
        # than returning EXECUTOR_CRASH because the request itself is
        # malformed — the executor never even tried.
        raise ValueError(
            f"Unsupported language {request.language!r}; "
            "Phase 1 supports 'python' only.",
        )

    snippet_path = f"{_SNIPPET_DIR}/exec_{uuid4().hex}.py"

    # ``shlex.quote`` keeps the path safe even though we generate it
    # ourselves — defense in depth in case _SNIPPET_DIR ever becomes
    # caller-controlled.
    quoted_path = shlex.quote(snippet_path)
    if request.timeout_s is not None:
        # Use coreutils ``timeout(1)`` with default signal (SIGTERM).
        # Rationale: SIGTERM causes the child to exit normally, and
        # coreutils then reports exit 124 — our unambiguous TIMEOUT
        # signal. If we used ``--signal=KILL`` instead, the child
        # would die via SIGKILL and exit 137, which collides with the
        # OOM heuristic below. ``timeout`` also enforces a 10 s grace
        # window: a child that traps and ignores SIGTERM gets SIGKILL
        # after 10 s. That edge case (also exit 137, but at duration
        # ~= timeout_s + 10) is mis-classified as OOM today; treat as
        # known-and-acceptable in Phase 1.
        # ``timeout`` accepts decimal seconds, so just str() the float.
        cmd = (
            f"timeout {request.timeout_s} "
            f"python3 {quoted_path}"
        )
    else:
        cmd = f"python3 {quoted_path}"

    t0 = time.monotonic()
    try:
        pool.write_file(sbx_id, snippet_path, request.code.encode("utf-8"))
        # Pass the Python source as ``code`` so pool.exec can attach it
        # as the sensitive ``acas.exec.code`` attribute on the
        # ``acas.sandbox.exec`` span. The executor does NOT open its
        # own span — that would create nested ``acas.sandbox.exec``
        # spans with the same name. pool.exec is the wire-level
        # boundary; one span per wire call.
        raw = pool.exec(sbx_id, cmd, code=request.code)
    except Exception as exc:  # noqa: BLE001 — we want to catch absolutely anything
        # Anything the SDK raises here counts as an executor crash: we
        # never observed a real exit code from the user's code. Surface
        # the SDK error in stderr so it shows up in the model's view
        # and in any telemetry; the caller can drill into the operation
        # id from there.
        duration_ms = int((time.monotonic() - t0) * 1000)
        logger.warning(
            "executor: sandbox %s crashed during exec: %s", sbx_id, exc,
        )
        return ExecResult.from_exit_code(
            exit_code=EXIT_EXECUTOR_CRASH,
            stdout="",
            stderr=f"executor_crash: {type(exc).__name__}: {exc}",
            duration_ms=duration_ms,
            truncated=False,
        )

    duration_ms = int((time.monotonic() - t0) * 1000)

    # Translate platform-level exit codes onto our negative-int contract.
    if raw.exit_code == _COREUTILS_TIMEOUT_EXIT:
        return ExecResult.from_exit_code(
            exit_code=EXIT_TIMEOUT,
            stdout=raw.stdout,
            stderr=(
                f"{raw.stderr}\n"
                f"--- executor ---\n"
                f"TIMEOUT after {request.timeout_s}s "
                f"(coreutils timeout exit {_COREUTILS_TIMEOUT_EXIT})"
            ),
            duration_ms=duration_ms,
            truncated=True,
        )

    if raw.exit_code == _SIGKILL_EXIT:
        # Heuristic: in a sandbox we rarely send SIGKILL ourselves
        # except via ``timeout --signal=KILL`` above (handled), so 137
        # almost always means the cgroup OOM-killed the process.
        return ExecResult.from_exit_code(
            exit_code=EXIT_OOM,
            stdout=raw.stdout,
            stderr=(
                f"{raw.stderr}\n"
                f"--- executor ---\n"
                f"OOM kill suspected (exit {_SIGKILL_EXIT} / SIGKILL)"
            ),
            duration_ms=duration_ms,
            truncated=False,
        )

    return ExecResult.from_exit_code(
        exit_code=raw.exit_code,
        stdout=raw.stdout,
        stderr=raw.stderr,
        duration_ms=duration_ms,
        truncated=False,
    )


__all__ = ["execute"]

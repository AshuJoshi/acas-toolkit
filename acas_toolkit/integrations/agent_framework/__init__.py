"""Agent Framework tools backed by the ACAS sandbox pool.

This sub-package is the **integration seam** for Microsoft Agent
Framework. Today it ships thin tool factories — `make_execute_code_tool`,
`make_run_python_tool`, `make_run_pytest_tool`, `make_run_shell_tool` —
that each wrap a `SandboxPool` call. A planned `AcasProvider` (a full
`ContextProvider` that owns the `SandboxPool` and drives per-session
snapshot + rehydrate off the AF session id) will live alongside or
supersede these factories. See README §Roadmap for the timeline.
"""

try:
    from acas_toolkit.integrations.agent_framework.execute_code import make_execute_code_tool
    from acas_toolkit.integrations.agent_framework.run_python import make_run_python_tool
    from acas_toolkit.integrations.agent_framework.run_pytest import make_run_pytest_tool
    from acas_toolkit.integrations.agent_framework.run_shell import make_run_shell_tool
except ImportError as exc:  # pragma: no cover - exercised via the extras install
    raise ImportError(
        "acas_toolkit.integrations.agent_framework requires Microsoft Agent "
        "Framework, which is not part of the core install. Install it with:\n"
        '    pip install "acas-toolkit[agent-framework]"\n'
        f"(original error: {exc})"
    ) from exc

__all__ = [
    "make_execute_code_tool",
    "make_run_python_tool",
    "make_run_pytest_tool",
    "make_run_shell_tool",
]

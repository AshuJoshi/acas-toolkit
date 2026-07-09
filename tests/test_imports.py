"""Import-sanity smoke test.

If this fails, the package layout is broken even before any code runs.
"""

import importlib.util

import pytest


def _agent_framework_installed() -> bool:
    return importlib.util.find_spec("agent_framework") is not None


def test_core_imports() -> None:
    from acas_toolkit import (
        EgressPolicyBuilder,
        SandboxPool,
        SandboxPoolConfig,
        SessionManager,
        SessionStore,
        WorkspaceVolume,
        ensure_workspace_volume,
        make_control_client,
        make_data_client,
        make_sandbox_client,
        normalize_workspace_volume,
        resolve_regional_endpoint,
    )
    names = (
        EgressPolicyBuilder,
        SandboxPool,
        SandboxPoolConfig,
        SessionManager,
        SessionStore,
        WorkspaceVolume,
        ensure_workspace_volume,
        make_control_client,
        make_data_client,
        make_sandbox_client,
        normalize_workspace_volume,
        resolve_regional_endpoint,
    )
    assert all(x is not None for x in names)


def test_executor_module() -> None:
    from acas_toolkit.executor import ExecRequest, ExecResult, execute
    assert all(x is not None for x in (ExecRequest, ExecResult, execute))


def test_core_import_does_not_require_agent_framework() -> None:
    """The core package must import without Agent Framework present.

    Agent Framework lives behind the ``[agent-framework]`` extra; importing
    ``acas_toolkit`` (sandbox management) must never pull it in.
    """
    import acas_toolkit  # noqa: F401  (import side-effect is the assertion)

    core_public = set(acas_toolkit.__all__)
    # None of the core exports should be the AF tool factories.
    assert not core_public.intersection(
        {
            "make_execute_code_tool",
            "make_run_python_tool",
            "make_run_pytest_tool",
            "make_run_shell_tool",
        }
    )


@pytest.mark.skipif(
    not _agent_framework_installed(),
    reason="Agent Framework not installed; install acas-toolkit[agent-framework]",
)
def test_agent_framework_integration() -> None:
    # Only runs when the [agent-framework] extra is installed.
    from acas_toolkit.integrations.agent_framework import (
        make_execute_code_tool,
        make_run_python_tool,
        make_run_pytest_tool,
        make_run_shell_tool,
    )
    assert all(
        x is not None
        for x in (
            make_execute_code_tool,
            make_run_python_tool,
            make_run_pytest_tool,
            make_run_shell_tool,
        )
    )


def test_agent_framework_integration_guarded_without_af() -> None:
    """Without Agent Framework, importing the integration must raise a
    helpful ImportError pointing at the extra (not a bare ModuleNotFound)."""
    if _agent_framework_installed():
        pytest.skip("Agent Framework is installed; guard path not exercised")
    with pytest.raises(ImportError, match=r"acas-toolkit\[agent-framework\]"):
        import acas_toolkit.integrations.agent_framework  # noqa: F401



def test_group_client_adapter_egress_passthrough() -> None:
    """``GroupClientAdapter.get_egress_decisions`` must route through the
    per-sandbox ``SandboxClient`` (the new SDK puts egress audit
    there). Probe ``probes/sandbox_egress_audit.py`` and example
    ``examples/03_egress_policy.py`` both depend on this passthrough.
    """
    from acas_toolkit.data_client import GroupClientAdapter

    # Method must exist on the adapter (not just on the underlying
    # SDK SandboxClient).
    assert callable(getattr(GroupClientAdapter, "get_egress_decisions", None))


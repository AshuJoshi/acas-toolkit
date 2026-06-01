"""Import-sanity smoke test.

If this fails, the package layout is broken even before any code runs.
"""


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


def test_agent_framework_integration() -> None:
    # AF is a core dep in this scaffold, so this should always import.
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


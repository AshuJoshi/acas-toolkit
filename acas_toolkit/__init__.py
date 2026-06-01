"""ACAS Toolkit — build agents that run code in Azure Container Apps Sandboxes.

Quickstart::

    from acas_toolkit import SandboxPool, SandboxPoolConfig
    from acas_toolkit.executor import ExecRequest, execute

    cfg = SandboxPoolConfig.from_env()
    with SandboxPool(cfg) as pool:
        with pool.lease() as sbx_id:
            result = execute(
                ExecRequest(language="python", code="print('hello')"),
                pool=pool,
                sbx_id=sbx_id,
            )
            print(result.stdout)
"""

from acas_toolkit.control_client import (
    make_control_client,
    resolve_regional_endpoint,
)
from acas_toolkit.data_client import make_data_client
from acas_toolkit.egress import EgressPolicyBuilder
from acas_toolkit.sandbox_factory import (
    SandboxClients,
    make_sandbox_client,
)
from acas_toolkit.sandbox_pool import SandboxPool, SandboxPoolConfig
from acas_toolkit.session_manager import SessionManager
from acas_toolkit.session_store import SessionEntry, SessionStore
from acas_toolkit.workspace import (
    WorkspaceVolume,
    WorkspaceVolumeArg,
    ensure_workspace_volume,
    normalize_workspace_volume,
)

__all__ = [
    "EgressPolicyBuilder",
    "SandboxClients",
    "SandboxPool",
    "SandboxPoolConfig",
    "SessionEntry",
    "SessionManager",
    "SessionStore",
    "WorkspaceVolume",
    "WorkspaceVolumeArg",
    "ensure_workspace_volume",
    "make_control_client",
    "make_data_client",
    "make_sandbox_client",
    "normalize_workspace_volume",
    "resolve_regional_endpoint",
]

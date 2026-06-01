"""ACAS control-plane (ARM) client.

This module owns everything that hits the **ARM control plane** —
``Microsoft.App/sandboxGroups`` CRUD, RBAC anchor, and the regional-endpoint
lookup. It is intentionally tiny because the ARM surface for ACAS is tiny:
the only ARM resource is the sandbox group itself; everything below it is
data plane.

Notes on the SDK port
---------------------
``azure-containerapps-sandbox 0.1.0b1`` (the PyPI build uploaded
2026-05-30) renamed the ARM management client from
``SandboxGroupClient`` → :class:`SandboxGroupManagementClient`. The
preview-private wheel of the same version number we vendored earlier
exposed it as ``SandboxGroupClient`` — they are not the same package.

The new client's constructor signature is also different (credential
is positional, the rest are keyword-only).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from azure.identity import AzureCliCredential
from azure.containerapps.sandbox import (
    SandboxGroup,
    SandboxGroupManagementClient,
)

if TYPE_CHECKING:
    from azure.core.credentials import TokenCredential


def make_control_client(
    subscription_id: str,
    resource_group: str,
    *,
    credential: "TokenCredential | None" = None,
) -> SandboxGroupManagementClient:
    """Build a control-plane (ARM) client for ACAS sandbox groups.

    Parameters
    ----------
    subscription_id:
        Azure subscription containing the sandbox group.
    resource_group:
        ARM resource group containing the sandbox group.
    credential:
        Optional Azure credential. Defaults to :class:`AzureCliCredential`,
        which is ~1 s faster per token than ``DefaultAzureCredential`` on
        WSL / local because it skips the IMDS probe that always times out.
        For production / managed-identity deployments pass
        ``DefaultAzureCredential()`` explicitly.
    """
    credential = credential or AzureCliCredential()
    return SandboxGroupManagementClient(
        credential,
        subscription_id=subscription_id,
        resource_group=resource_group,
    )


def resolve_regional_endpoint(group: SandboxGroup) -> str:
    """Pull ``properties.managementEndpoint`` off a sandbox group, validated.

    Kept for back-compat. New callers in known-region scenarios can use
    :func:`azure.containerapps.sandbox.endpoint_for_region` directly.

    Raises
    ------
    RuntimeError
        If the group exists but does not expose ``managementEndpoint``.
    """
    endpoint = group.properties.get("managementEndpoint") if group.properties else None
    if not endpoint:
        raise RuntimeError(
            f"Sandbox group {group.name!r} did not return a "
            f"`properties.managementEndpoint`. Group payload: {group.properties!r}"
        )
    return endpoint.rstrip("/")


__all__ = ["make_control_client", "resolve_regional_endpoint"]

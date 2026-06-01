"""Compose ACAS control-plane + data-plane clients for one sandbox group.

Exposes :func:`make_sandbox_client`, which returns a :class:`SandboxClients`
record containing:

* ``mgmt`` — :class:`SandboxGroupManagementClient` (ARM, group CRUD)
* ``client`` — :class:`GroupClientAdapter` (data plane, old call shape)
* ``regional_endpoint`` — the ``properties.managementEndpoint`` string
* ``sandbox_group`` — the group name

The data-plane URL is pulled off the sandbox group's
``properties.managementEndpoint`` so we can talk to the correct regional
endpoint without a hardcoded global constant. On the vendored preview
wheel this needed a monkey-patch (``client._dp = httpx.Client(...)``);
the PyPI build of 0.1.0b1 takes the endpoint as a positional constructor
argument, which is what the adapter uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from azure.identity import AzureCliCredential

from acas_toolkit.control_client import make_control_client, resolve_regional_endpoint
from acas_toolkit.data_client import GroupClientAdapter

if TYPE_CHECKING:
    from azure.containerapps.sandbox import SandboxGroupManagementClient
    from azure.core.credentials import TokenCredential


@dataclass
class SandboxClients:
    """Bundle of clients + endpoint metadata for one sandbox group."""

    client: GroupClientAdapter
    mgmt: "SandboxGroupManagementClient"
    regional_endpoint: str
    sandbox_group: str


def make_sandbox_client(
    subscription_id: str,
    resource_group: str,
    sandbox_group: str,
    *,
    credential: "TokenCredential | None" = None,
) -> SandboxClients:
    """Build :class:`SandboxClients` for an existing sandbox group.

    The group **must** already exist (callers like
    :class:`acas_toolkit.SandboxPool` upsert it before calling this).
    We read ``properties.managementEndpoint`` to discover the regional
    data-plane URL, then build a :class:`GroupClientAdapter` pointed
    at it.
    """
    cred = credential or AzureCliCredential()
    mgmt = make_control_client(subscription_id, resource_group, credential=cred)
    group = mgmt.get_group(sandbox_group)
    regional_endpoint = resolve_regional_endpoint(group)

    client = GroupClientAdapter(
        regional_endpoint,
        cred,
        subscription_id=subscription_id,
        resource_group=resource_group,
        sandbox_group=sandbox_group,
    )

    return SandboxClients(
        client=client,
        mgmt=mgmt,
        regional_endpoint=regional_endpoint,
        sandbox_group=sandbox_group,
    )


__all__ = ["SandboxClients", "make_sandbox_client"]

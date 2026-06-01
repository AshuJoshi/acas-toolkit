"""Workspace volumes — durable cross-session storage for sandboxes.

ACAS sandboxes are ephemeral by default: every ``create_sandbox`` call
gives you a fresh ``/dev/vdb`` rootfs, and even snapshot-rehydrate is a
filesystem checkpoint of that disk. To share data **across sandbox
lifetimes** (and across sandbox groups, if you mount the same volume
into both), the sandbox group exposes a *volume* primitive — today
backed by Azure Blob via ``blobfuse2``.

This module is a thin, opinionated wrapper around the SDK's volume CRUD
and the ``volumes=[...]`` kwarg on ``create_sandbox``. It exists so:

* Callers say ``workspace_volume="my-workspace"`` (or ``"name@/mnt/x"``)
  instead of hand-rolling ``{"volumeName": ..., "mountpoint": ...}``
  dicts — the SDK's accepted wire shape was empirically discovered
  (server errors named the exact keys) and is not what you'd guess
  from the SDK's Python ``Volume`` model.
* :class:`WorkspaceVolume` and :class:`SandboxPool` share one
  normalization path, so the volume dict shipped on the warm-path-
  bypassed cold create is byte-identical to the one shipped on the
  durable-session fresh-create branch and the snapshot-rehydrate branch.
* Optional ``auto_create=True`` makes first-run-from-empty-subscription
  ergonomic without forcing users to write idempotency themselves.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Union

if TYPE_CHECKING:
    from azure.containerapps.sandbox import SandboxClient


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkspaceVolume:
    """A reference to a workspace volume to be mounted into sandboxes.

    Attributes
    ----------
    name:
        The volume name as it exists in the sandbox group. Must be DNS-safe.
    mountpoint:
        Absolute path inside the sandbox where the volume is mounted.
        Defaults to ``/workspace``.
    auto_create:
        If ``True`` and the volume does not exist when first referenced,
        :func:`ensure_workspace_volume` will create it. Defaults to
        ``False`` (fail-fast on typos — most production deployments
        provision volumes out-of-band via Bicep / Terraform).
    type:
        Volume backing type. Today only ``"AzureBlob"`` is supported by
        ACAS in preview. Honored only when ``auto_create=True``.
    """

    name: str
    mountpoint: str = "/workspace"
    auto_create: bool = False
    type: str = "AzureBlob"

    def as_mount_dict(self) -> dict[str, str]:
        """Return the wire-shape dict accepted by ``create_sandbox(volumes=...)``.

        The keys are ``volumeName`` and ``mountpoint`` — discovered
        empirically from server validation errors. ``mountPath`` and
        ``path`` are both rejected; ``name`` (the natural Python
        camel→snake mapping) is also rejected.
        """
        return {"volumeName": self.name, "mountpoint": self.mountpoint}


#: Accepted shapes for a ``workspace_volume`` argument.
#:
#: * ``None`` — no workspace mount.
#: * ``str`` — bare volume name, mounted at ``/workspace`` (no auto-create).
#: * ``str`` with ``"@"`` separator — ``"name@/mnt/path"`` (still no auto-create).
#: * ``dict`` — keyword args for :class:`WorkspaceVolume` (e.g.
#:   ``{"name": "ws", "auto_create": True}``).
#: * :class:`WorkspaceVolume` — passed through.
WorkspaceVolumeArg = Union[str, dict, WorkspaceVolume, None]


def normalize_workspace_volume(
    arg: WorkspaceVolumeArg,
) -> WorkspaceVolume | None:
    """Coerce any accepted ``workspace_volume`` shape to ``WorkspaceVolume``.

    Returns ``None`` when ``arg is None`` so callers can use the result
    as a truthiness check.
    """
    if arg is None:
        return None
    if isinstance(arg, WorkspaceVolume):
        return arg
    if isinstance(arg, str):
        if "@" in arg:
            name, _, mp = arg.partition("@")
            if not name or not mp:
                raise ValueError(
                    f"workspace_volume string with '@' must be "
                    f"'name@/mountpoint', got {arg!r}"
                )
            return WorkspaceVolume(name=name, mountpoint=mp)
        return WorkspaceVolume(name=arg)
    if isinstance(arg, dict):
        if "name" not in arg:
            raise ValueError(
                "workspace_volume dict must contain 'name' "
                f"(got keys {list(arg)!r})"
            )
        return WorkspaceVolume(**arg)
    raise TypeError(
        f"workspace_volume must be None, str, dict, or WorkspaceVolume; "
        f"got {type(arg).__name__}"
    )


def ensure_workspace_volume(
    client: "SandboxClient",
    sandbox_group: str,
    volume: WorkspaceVolume,
    *,
    resource_group: str | None = None,
) -> None:
    """Idempotently ensure ``volume`` exists in ``sandbox_group``.

    No-op when ``volume.auto_create`` is ``False`` (caller's contract
    is "this volume already exists; fail loud if it doesn't").

    Otherwise: try ``get_volume`` first (cheap), and only call
    ``create_volume`` on ``ResourceNotFoundError``. This avoids the
    "create + catch 409" pattern, which logs noisy errors in App
    Insights on every steady-state acquire.
    """
    if not volume.auto_create:
        return
    from azure.core.exceptions import ResourceNotFoundError

    kwargs: dict[str, Any] = {"sandbox_group": sandbox_group}
    if resource_group is not None:
        kwargs["resource_group"] = resource_group
    try:
        client.get_volume(volume.name, **kwargs)
        logger.debug(
            "workspace volume %r already exists in %r", volume.name, sandbox_group,
        )
        return
    except ResourceNotFoundError:
        pass
    logger.info(
        "creating workspace volume %r (type=%s) in sandbox group %r",
        volume.name, volume.type, sandbox_group,
    )
    create_kwargs: dict[str, Any] = {"name": volume.name, "type": volume.type}
    if resource_group is not None:
        create_kwargs["resource_group"] = resource_group
    client.create_volume(sandbox_group=sandbox_group, **create_kwargs)

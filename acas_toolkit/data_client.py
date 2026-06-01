"""ACAS data-plane client adapter.

This module bridges the **old** call shapes used by
:mod:`acas_toolkit.sandbox_pool` and :mod:`acas_toolkit.session_manager`
to the **new** PyPI build of ``azure-containerapps-sandbox`` (0.1.0b1,
uploaded 2026-05-30).

Why an adapter?
---------------
The PyPI build is a full autorest-generated rewrite that broke nearly
every method signature even though the version string is identical to
the preview-private wheel we vendored earlier. Major shape
changes:

* The data-plane client is now constructed per **sandbox group**
  (``SandboxGroupClient(endpoint, credential, subscription_id=,
  resource_group=, sandbox_group=)``) — the endpoint is now the first
  positional arg so the regional-routing workaround (Bug #2 — manually
  re-pointing ``client._dp``) is no longer needed.
* Per-sandbox operations now hang off a ``SandboxClient`` you obtain
  via ``group_client.get_sandbox_client(sandbox_id)`` (or as the result
  of ``begin_create_sandbox().result()``), not as
  ``group_client.exec(sbx_id, group, cmd)`` on the group client.
* Create / delete sandbox is an LRO: ``begin_create_sandbox(...)
  .result()`` returns a ``SandboxClient``, not a ``Sandbox`` value
  object with ``.id``.
* ``EgressPolicy`` is now a dataclass (``default_action``,
  ``host_rules``, ``rules``), not a wire-shape dict.

Rather than rewrite every call site in sandbox_pool / session_manager,
this module exposes a :class:`GroupClientAdapter` whose method
signatures match the **old** SDK 1:1 — that's where the
``sandbox_group`` positional arg comes from on ``delete_sandbox``,
``exec``, etc.

Cached per-sandbox clients
--------------------------
``SandboxClient`` instances are slightly more than a handle (they hold
an LRO state machine for begin_* operations), so we cache them on the
adapter — one per ``sandbox_id`` — to avoid re-allocating an authed
client on every ``exec`` / ``read_file`` / ``write_file`` call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterable

from azure.containerapps.sandbox import (
    EgressHostRule,
    EgressPolicy,
    SandboxClient,
    SandboxGroupClient,
    SandboxVolume,
)

if TYPE_CHECKING:
    from azure.core.credentials import TokenCredential


class _SbHandle:
    """Minimal back-compat shim: exposes ``.id`` like the old ``Sandbox`` value object.

    The new SDK's ``begin_create_sandbox().result()`` returns a
    ``SandboxClient`` whose identifier lives on ``.sandbox_id``. The
    old SDK returned a ``Sandbox`` value object whose identifier
    lived on ``.id``. Callers of this adapter still consume ``.id``.
    """

    __slots__ = ("id",)

    def __init__(self, sandbox_id: str) -> None:
        self.id = sandbox_id


def _coerce_egress_policy(ep: Any) -> EgressPolicy | None:
    """Accept dict-shaped policies (from :class:`EgressPolicyBuilder`) or pre-built models."""
    if ep is None or isinstance(ep, EgressPolicy):
        return ep
    if isinstance(ep, dict):
        host_rules = [
            EgressHostRule(pattern=r["pattern"], action=r["action"])
            for r in ep.get("hostRules", []) or []
        ]
        return EgressPolicy(
            default_action=ep.get("defaultAction", "Allow"),
            host_rules=host_rules or None,
            rules=ep.get("rules") or None,
        )
    raise TypeError(
        f"egress_policy must be dict, EgressPolicy, or None; got {type(ep).__name__}"
    )


def _coerce_volumes(vols: Iterable[Any] | None) -> list[SandboxVolume] | None:
    """Accept dict-shaped volume mounts or pre-built :class:`SandboxVolume` instances.

    Workspace mounts come in the wire shape
    ``{"volumeName": ..., "mountpoint": ...}`` from
    :meth:`acas_toolkit.workspace.WorkspaceVolume.as_mount_dict`; this
    coerces them to the typed model the new SDK expects.
    """
    if not vols:
        return None
    out: list[SandboxVolume] = []
    for v in vols:
        if isinstance(v, SandboxVolume):
            out.append(v)
            continue
        if isinstance(v, dict):
            out.append(
                SandboxVolume(
                    volume_name=v.get("volumeName") or v.get("volume_name") or "",
                    mountpoint=v.get("mountpoint") or "",
                    read_only=v.get("readOnly") if "readOnly" in v else v.get("read_only"),
                )
            )
            continue
        raise TypeError(
            f"volumes entries must be dict or SandboxVolume; got {type(v).__name__}"
        )
    return out


class GroupClientAdapter:
    """Old-style facade over a new-SDK :class:`SandboxGroupClient`.

    Constructor params mirror the new SDK's ``SandboxGroupClient`` —
    ``endpoint`` first (regional ``managementEndpoint``), credential
    second, then the keyword-only subscription/resource_group/sandbox_group
    that the new client requires. ``audience`` is forwarded if supplied.
    """

    def __init__(
        self,
        endpoint: str,
        credential: "TokenCredential",
        *,
        subscription_id: str,
        resource_group: str,
        sandbox_group: str,
        audience: str | None = None,
    ) -> None:
        kwargs: dict[str, Any] = dict(
            subscription_id=subscription_id,
            resource_group=resource_group,
            sandbox_group=sandbox_group,
        )
        if audience is not None:
            kwargs["audience"] = audience
        self._gc: SandboxGroupClient = SandboxGroupClient(
            endpoint, credential, **kwargs,
        )
        self._sb_clients: dict[str, SandboxClient] = {}

    # ----- per-sandbox handle caching ------------------------------------

    def _sb(self, sbx_id: str) -> SandboxClient:
        sb = self._sb_clients.get(sbx_id)
        if sb is None:
            sb = self._gc.get_sandbox_client(sbx_id)
            self._sb_clients[sbx_id] = sb
        return sb

    # ----- sandbox lifecycle (old shape: positional sandbox_group) -------

    def create_sandbox(
        self,
        sandbox_group: str,  # ignored — already bound on the underlying client
        *,
        disk: str = "ubuntu",
        egress_policy: Any = None,
        volumes: Iterable[Any] | None = None,
        **kwargs: Any,
    ) -> _SbHandle:
        """Create a sandbox (LRO, blocks until ``Running``). Returns ``_SbHandle``."""
        ep = _coerce_egress_policy(egress_policy)
        vols = _coerce_volumes(volumes)
        call_kwargs: dict[str, Any] = dict(kwargs)
        call_kwargs["disk"] = disk
        if ep is not None:
            call_kwargs["egress_policy"] = ep
        if vols is not None:
            call_kwargs["volumes"] = vols
        poller = self._gc.begin_create_sandbox(**call_kwargs)
        sb: SandboxClient = poller.result()
        self._sb_clients[sb.sandbox_id] = sb
        return _SbHandle(sb.sandbox_id)

    def delete_sandbox(self, sbx_id: str, sandbox_group: str | None = None) -> None:
        self._sb_clients.pop(sbx_id, None)
        self._gc.delete_sandbox(sbx_id)

    def get_sandbox(self, sbx_id: str, sandbox_group: str | None = None):
        return self._gc.get_sandbox(sbx_id)

    def list_sandboxes(self, sandbox_group: str | None = None):
        return self._gc.list_sandboxes()

    # ----- per-sandbox ops (old shape: positional sandbox_group) ---------

    def exec(self, sbx_id: str, sandbox_group: str, command: str, **kwargs: Any):
        return self._sb(sbx_id).exec(command, **kwargs)

    def write_file(
        self,
        sbx_id: str,
        sandbox_group: str,
        path: str,
        content: bytes,
        **kwargs: Any,
    ) -> None:
        self._sb(sbx_id).write_file(path, content, **kwargs)

    def read_file(
        self, sbx_id: str, sandbox_group: str, path: str, **kwargs: Any,
    ) -> bytes:
        return self._sb(sbx_id).read_file(path, **kwargs)

    # ----- egress audit ---------------------------------------------------

    def get_egress_decisions(self, sbx_id: str, sandbox_group: str | None = None):
        """Return the per-sandbox egress decisions log.

        Routes through the per-sandbox ``SandboxClient`` (the new SDK
        puts egress audit there, not on the group client). The
        ``sandbox_group`` argument is accepted for parity with the
        legacy call shape used by ``probes/sandbox_egress_audit.py``
        and example 03; it is otherwise ignored.

        Known SDK quirk on the ``0.1.0b1`` PyPI build: the ``allowed``
        side of the response is sparse — typically 2–3 of 5 issued
        requests come back. The ``denied`` side is reliable after a
        ~30 s settle. Callers that want a complete allowed log should
        poll.
        """
        return self._sb(sbx_id).get_egress_decisions()

    # ----- stats ----------------------------------------------------------

    def get_stats(self, sbx_id: str, sandbox_group: str | None = None):
        """Return the per-sandbox runtime stats (CPU, mem, etc).

        Routes through the per-sandbox ``SandboxClient`` (the new SDK
        puts stats there, not on the group client). The
        ``sandbox_group`` argument is accepted for parity with the
        legacy call shape used by ``probes/sandbox_introspect.py``;
        it is otherwise ignored.
        """
        return self._sb(sbx_id).get_stats()

    # ----- snapshots ------------------------------------------------------

    def create_snapshot(
        self, sbx_id: str, sandbox_group: str | None = None, *, name: str | None = None,
    ):
        return self._sb(sbx_id).create_snapshot(name=name)

    def delete_snapshot(self, snapshot_id: str, sandbox_group: str | None = None) -> None:
        self._gc.delete_snapshot(snapshot_id)

    def get_snapshot(self, snapshot_id: str, sandbox_group: str | None = None):
        return self._gc.get_snapshot(snapshot_id)

    def list_snapshots(self, sandbox_group: str | None = None):
        return self._gc.list_snapshots()

    # ----- volumes (group-bound; sandbox_group/resource_group args ignored) -

    def create_volume(
        self,
        *,
        sandbox_group: str | None = None,
        resource_group: str | None = None,
        name: str,
        type: str = "AzureBlob",
        size: int | None = None,
        labels: dict[str, str] | None = None,
    ):
        return self._gc.create_volume(name, size=size, type=type, labels=labels)

    def get_volume(
        self,
        volume_name: str,
        *,
        sandbox_group: str | None = None,
        resource_group: str | None = None,
    ):
        return self._gc.get_volume(volume_name)

    def delete_volume(
        self,
        volume_name: str,
        *,
        sandbox_group: str | None = None,
        resource_group: str | None = None,
    ) -> None:
        self._gc.delete_volume(volume_name)

    def list_volumes(self, sandbox_group: str | None = None):
        return self._gc.list_volumes()

    # ----- shutdown -------------------------------------------------------

    def close(self) -> None:
        """No-op — the new SDK has no persistent transport for us to close.

        Kept so the legacy ``self.clients.client._dp.close()`` site has
        a one-liner replacement (``self.clients.client.close()``).
        """
        return None


__all__ = ["GroupClientAdapter", "make_data_client"]


def make_data_client(
    subscription_id: str,
    resource_group: str,
    sandbox_group: str,
    *,
    regional_endpoint: str,
    credential: "TokenCredential | None" = None,
) -> GroupClientAdapter:
    """Build a data-plane :class:`GroupClientAdapter` directly.

    Use :func:`acas_toolkit.make_sandbox_client` instead if you don't
    already know ``regional_endpoint`` — that helper looks it up via
    ARM. This function is kept for callers that have the endpoint in
    hand (e.g. via
    :func:`azure.containerapps.sandbox.endpoint_for_region`) and want
    to skip the ARM round-trip.
    """
    cred = credential
    if cred is None:
        from azure.identity import AzureCliCredential
        cred = AzureCliCredential()
    return GroupClientAdapter(
        regional_endpoint,
        cred,
        subscription_id=subscription_id,
        resource_group=resource_group,
        sandbox_group=sandbox_group,
    )

"""
``SessionManager`` — durable sessions on top of :class:`SandboxPool`.

Concept
-------
A *session* is a named, long-lived conversation that survives process
restarts. Behind the scenes it's a snapshot ID stored in a
:class:`SessionStore`; on each ``open`` we hydrate a fresh sandbox from
that snapshot, and on ``close`` (or ``checkpoint``) we snapshot the
current state back.

Lifecycle
---------
::

    mgr = SessionManager.from_env()

    # Run 1 (fresh):
    sbx_id = mgr.open("alice-conversation-1")           # new sandbox
    pool.exec(sbx_id, "pip install sympy")
    mgr.close("alice-conversation-1")                    # snapshot + delete

    # Run 2 (rehydrate):
    sbx_id = mgr.open("alice-conversation-1")            # sandbox created
                                                         # from snapshot
    pool.exec(sbx_id, "python3 -c 'import sympy'")       # already installed
    mgr.close("alice-conversation-1")                    # new snapshot

Snapshots are NOT free (each is a stored disk image). The manager rotates
them: on every ``checkpoint`` it creates the new snapshot, then deletes
the previous one. So a session has at most one snapshot at a time.

Use :meth:`discard` to permanently end a session and free the snapshot.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Iterator

from opentelemetry.trace import Status, StatusCode

from acas_toolkit._telemetry import get_tracer
from acas_toolkit.sandbox_pool import SandboxPool
from acas_toolkit.session_store import SessionEntry, SessionStore

logger = logging.getLogger(__name__)


class SessionManager:
    def __init__(self, pool: SandboxPool, store: SessionStore | None = None):
        self.pool = pool
        self.store = store or SessionStore()
        # Track live sandboxes per session so close() knows what to snapshot.
        self._live: dict[str, str] = {}  # session_id -> sandbox_id

    @classmethod
    def from_env(cls, pool: SandboxPool | None = None) -> "SessionManager":
        return cls(pool=pool or SandboxPool.from_env())

    # ----- pool plumbing --------------------------------------------------

    def __enter__(self) -> "SessionManager":
        self.pool.open()
        return self

    def __exit__(self, *exc):
        # Best-effort: any sessions still live get checkpointed + sandbox released.
        for sid in list(self._live):
            try:
                self.close(sid)
            except Exception as e:  # noqa: BLE001
                logger.warning("close(%s) failed during exit: %s", sid, e)
        self.pool.close()

    # ----- session API ---------------------------------------------------

    def open(self, session_id: str, *, disk: str = "python-3.13",
             egress_policy: dict | None = None,
             volumes: list[dict] | None = None) -> str:
        """Get a sandbox ID for ``session_id``. Rehydrate from snapshot if any.

        If ``egress_policy`` is provided (typically from
        :class:`acas_toolkit.EgressPolicyBuilder`), it is
        applied at sandbox creation — both on the fresh-create path and
        the snapshot-rehydrate path. The policy is *not* persisted in
        the session store; pass it on every ``open()`` if you want a
        locked-down posture across resumes.

        If ``volumes`` is provided (a list of mount dicts in the wire
        shape ``{"volumeName": ..., "mountpoint": ...}``), the same
        list is forwarded to both branches: cold create and snapshot
        rehydrate. Snapshots only checkpoint the sandbox rootfs
        (``/dev/vdb``), so external volume mounts must be re-specified
        on every rehydrate or they'd silently disappear. Like
        ``egress_policy``, ``volumes`` is **not** persisted in the
        session store — the provider re-supplies it on every ``open()``.

        Emits an ``acas.session.open`` span wrapping the whole
        method. On the rehydrate branch it adds a child
        ``acas.sandbox.rehydrate`` span around the
        ``create_sandbox(snapshot_id=…)`` SDK call. On the fresh
        branch the child span is ``acas.sandbox.acquire`` (emitted by
        ``pool.acquire``). The ``acas.session.fresh`` attribute on the
        outer span tells which branch ran.
        """
        if session_id in self._live:
            return self._live[session_id]

        tracer = get_tracer()
        with tracer.start_as_current_span("acas.session.open") as outer_span:
            outer_span.set_attribute("acas.session.id", session_id)
            try:
                entry = self.store.get(session_id)
                cfg = self.pool.config

                if entry and entry.snapshot_id:
                    outer_span.set_attribute("acas.session.fresh", False)
                    outer_span.set_attribute("acas.snapshot.id", entry.snapshot_id)
                    logger.info(
                        "rehydrating session %r from snapshot %s",
                        session_id, entry.snapshot_id,
                    )
                    create_kwargs: dict = {"snapshot_id": entry.snapshot_id}
                    if egress_policy is not None:
                        create_kwargs["egress_policy"] = egress_policy
                        if isinstance(egress_policy, dict):
                            default_action = egress_policy.get("defaultAction")
                        else:
                            default_action = getattr(
                                egress_policy, "default_action", None,
                            )
                        if default_action is not None:
                            outer_span.set_attribute(
                                "acas.egress.default_action", default_action,
                            )
                    if volumes:
                        create_kwargs["volumes"] = volumes
                        outer_span.set_attribute(
                            "acas.workspace.volume_count", len(volumes),
                        )
                    with tracer.start_as_current_span(
                        "acas.sandbox.rehydrate"
                    ) as rehydrate_span:
                        rehydrate_span.set_attribute("acas.session.id", session_id)
                        rehydrate_span.set_attribute(
                            "acas.snapshot.id", entry.snapshot_id,
                        )
                        sbx = self.pool.clients.client.create_sandbox(
                            cfg.sandbox_group, **create_kwargs,
                        )
                        rehydrate_span.set_attribute("acas.sandbox.id", sbx.id)
                    disk_to_record = entry.disk
                else:
                    outer_span.set_attribute("acas.session.fresh", True)
                    logger.info(
                        "creating fresh sandbox for session %r (disk=%s)",
                        session_id, disk,
                    )
                    acquire_kwargs: dict = {"disk": disk}
                    if egress_policy is not None:
                        acquire_kwargs["egress_policy"] = egress_policy
                    if volumes:
                        acquire_kwargs["volumes"] = volumes
                        outer_span.set_attribute(
                            "acas.workspace.volume_count", len(volumes),
                        )
                    # pool.acquire opens its own acas.sandbox.acquire span;
                    # it becomes a child of acas.session.open via OTel
                    # context propagation. No extra wiring needed here.
                    sbx_id = self.pool.acquire(**acquire_kwargs)

                    class _Holder:  # noqa: E701  # tiny shim, body intentionally inline
                        pass

                    sbx = _Holder()
                    sbx.id = sbx_id
                    disk_to_record = disk

                self._live[session_id] = sbx.id
                outer_span.set_attribute("acas.sandbox.id", sbx.id)
                # Track in pool's _active set so context-manager close
                # cleans up if needed.
                self.pool._active.add(sbx.id)

                if entry is None:
                    entry = SessionEntry(
                        subscription_id=cfg.subscription_id,
                        resource_group=cfg.resource_group,
                        sandbox_group=cfg.sandbox_group,
                        disk=disk_to_record,
                    )
                self.store.put(session_id, entry)

                return sbx.id
            except Exception as exc:
                outer_span.record_exception(exc)
                outer_span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise

    def checkpoint(self, session_id: str, *, name: str | None = None) -> str:
        """Snapshot the running sandbox. Rotates: deletes the previous snapshot.

        Emits an ``acas.sandbox.snapshot`` span with ``acas.sandbox.id``,
        ``acas.session.id``, ``acas.snapshot.id`` (newly created), and
        — when present — ``acas.snapshot.previous_id`` so traces show
        the rotation explicitly.
        """
        sbx_id = self._require_live(session_id)
        entry = self.store.get(session_id)
        assert entry is not None  # we set it on open()

        cfg = self.pool.config
        tracer = get_tracer()
        with tracer.start_as_current_span("acas.sandbox.snapshot") as span:
            span.set_attribute("acas.session.id", session_id)
            span.set_attribute("acas.sandbox.id", sbx_id)
            try:
                logger.info("checkpointing session %r (sandbox=%s)", session_id, sbx_id)
                new_snap = self.pool.clients.client.create_snapshot(
                    sbx_id, cfg.sandbox_group,
                    name=name or f"session-{session_id}",
                )
                span.set_attribute("acas.snapshot.id", new_snap.id)

                old_snap_id = entry.snapshot_id
                entry.snapshot_id = new_snap.id
                self.store.put(session_id, entry)

                if old_snap_id and old_snap_id != new_snap.id:
                    span.set_attribute("acas.snapshot.previous_id", old_snap_id)
                    try:
                        self.pool.clients.client.delete_snapshot(
                            old_snap_id, cfg.sandbox_group,
                        )
                        logger.info("deleted previous snapshot %s", old_snap_id)
                    except Exception as e:  # noqa: BLE001
                        # Rotation cleanup is best-effort: the new
                        # snapshot is already stored, so the session is
                        # safe. Surface the failure on the span (as an
                        # event, not ERROR status — the checkpoint
                        # itself succeeded) so it's still queryable.
                        span.add_event(
                            "previous_snapshot_delete_failed",
                            attributes={
                                "acas.snapshot.previous_id": old_snap_id,
                                "exception.type": type(e).__name__,
                                "exception.message": str(e),
                            },
                        )
                        logger.warning(
                            "delete previous snapshot %s failed: %s",
                            old_snap_id, e,
                        )

                return new_snap.id
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise

    def close(self, session_id: str, *, checkpoint: bool = True) -> None:
        """Snapshot (optional) and tear down the live sandbox.

        Emits an ``acas.session.close`` span carrying ``acas.session.id``
        and ``acas.session.checkpoint`` (bool, so traces distinguish
        "shut down cleanly with snapshot" from "shut down without
        snapshot"). The inner snapshot + release calls each produce
        their own child spans (``acas.sandbox.snapshot``,
        ``acas.sandbox.release``).
        """
        sbx_id = self._live.get(session_id)
        if sbx_id is None:
            return
        tracer = get_tracer()
        with tracer.start_as_current_span("acas.session.close") as span:
            span.set_attribute("acas.session.id", session_id)
            span.set_attribute("acas.session.checkpoint", checkpoint)
            span.set_attribute("acas.sandbox.id", sbx_id)
            try:
                try:
                    if checkpoint:
                        self.checkpoint(session_id)
                finally:
                    self._live.pop(session_id, None)
                    try:
                        self.pool.release(sbx_id)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("release(%s) failed: %s", sbx_id, e)
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise

    def discard(self, session_id: str) -> None:
        """End a session permanently. Deletes the snapshot if one exists."""
        try:
            self.close(session_id, checkpoint=False)
        except Exception:
            pass
        entry = self.store.get(session_id)
        if entry and entry.snapshot_id:
            try:
                self.pool.clients.client.delete_snapshot(
                    entry.snapshot_id, self.pool.config.sandbox_group,
                )
                logger.info("deleted snapshot %s for session %r", entry.snapshot_id, session_id)
            except Exception as e:  # noqa: BLE001
                logger.warning("delete_snapshot failed: %s", e)
        self.store.delete(session_id)

    @contextlib.contextmanager
    def session(self, session_id: str, *, disk: str = "python-3.13",
                checkpoint_on_exit: bool = True,
                egress_policy: dict | None = None,
                volumes: list[dict] | None = None) -> Iterator[str]:
        sbx_id = self.open(
            session_id, disk=disk, egress_policy=egress_policy, volumes=volumes,
        )
        try:
            yield sbx_id
        finally:
            self.close(session_id, checkpoint=checkpoint_on_exit)

    # ----- helpers --------------------------------------------------------

    def _require_live(self, session_id: str) -> str:
        sbx_id = self._live.get(session_id)
        if sbx_id is None:
            raise RuntimeError(
                f"session {session_id!r} is not open; call open() first"
            )
        return sbx_id


__all__ = ["SessionManager"]

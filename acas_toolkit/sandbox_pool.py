"""
Minimal sandbox lifecycle wrapper on top of
:mod:`acas_toolkit.sandbox_factory`.

Why a pool?
-----------
The Agent Framework tool functions (see
:mod:`acas_toolkit.integrations.agent_framework.run_python`) need to
share a single live sandbox across many tool invocations within one
agent run. Sandboxes are not free to create — the cold-start path is
``create_group`` (if missing) → ``create_sandbox`` (~seconds) — so we
acquire one up front and keep it warm.

For now "pool" is a misnomer: it's a 1-sandbox holder. That's deliberate
— the Agent Framework demo only needs one. The class is shaped like a
pool so it can grow to N without churning callers.

Usage
-----

.. code-block:: python

    with SandboxPool.from_env() as pool:
        sbx_id = pool.acquire(disk="ubuntu")
        result = pool.exec(sbx_id, "echo hi")
        pool.release(sbx_id)

Or context-managed end-to-end:

.. code-block:: python

    with SandboxPool.from_env() as pool, pool.lease(disk="ubuntu") as sbx_id:
        pool.exec(sbx_id, "echo hi")
"""

from __future__ import annotations

import contextlib
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator

from opentelemetry.trace import Status, StatusCode

from acas_toolkit._telemetry import (
    get_tracer,
    set_sensitive_attr,
)
from acas_toolkit.sandbox_factory import SandboxClients, make_sandbox_client

if TYPE_CHECKING:
    from azure.containerapps.sandbox import ExecResult

logger = logging.getLogger(__name__)

# Required env vars for :meth:`SandboxPoolConfig.from_env`. ``ACAS_LOCATION``
# is optional and defaults to ``westus2`` (the SDK-verified region as of the
# 2026-05-31 ACAS SDK port).
_REQUIRED_ENV_VARS = (
    "ACAS_SUBSCRIPTION_ID",
    "ACAS_RESOURCE_GROUP",
    "ACAS_SANDBOX_GROUP",
)
_DEFAULT_LOCATION = "westus2"


@dataclass
class SandboxPoolConfig:
    subscription_id: str
    resource_group: str
    location: str
    sandbox_group: str
    warm_size: int = 0
    warm_disk: str = "python-3.13"

    @classmethod
    def from_env(cls) -> "SandboxPoolConfig":
        """Build a config from environment variables.

        Loads ``.env`` from the current working directory if present (via
        ``python-dotenv``) so callers don't have to import it themselves.
        Raises ``RuntimeError`` if any of
        :data:`_REQUIRED_ENV_VARS` is missing — we deliberately do **not**
        ship subscription/RG/group defaults so a missing ``.env`` fails
        loudly instead of silently targeting the wrong resources.
        """
        # Soft import so the toolkit still works in environments that
        # trimmed the optional ``python-dotenv`` dep.
        try:
            from dotenv import load_dotenv  # type: ignore[import-not-found]

            load_dotenv()
        except ImportError:
            pass

        missing = [v for v in _REQUIRED_ENV_VARS if not os.environ.get(v)]
        if missing:
            raise RuntimeError(
                "SandboxPoolConfig.from_env() is missing required environment "
                f"variable(s): {', '.join(missing)}. Populate them in .env or "
                "export them in the shell. See .env.example."
            )

        return cls(
            subscription_id=os.environ["ACAS_SUBSCRIPTION_ID"],
            resource_group=os.environ["ACAS_RESOURCE_GROUP"],
            location=os.environ.get("ACAS_LOCATION") or _DEFAULT_LOCATION,
            sandbox_group=os.environ["ACAS_SANDBOX_GROUP"],
            warm_size=int(os.environ.get("ACAS_WARM_SIZE") or "0"),
            warm_disk=os.environ.get("ACAS_WARM_DISK") or "python-3.13",
        )


class SandboxPool:
    """One-sandbox holder backed by :func:`make_sandbox_client`.

    The pool ensures the ARM resource group + sandbox group exist (idempotent
    upserts), then hands out sandbox IDs via :meth:`acquire`.
    """

    def __init__(self, config: SandboxPoolConfig):
        self.config = config
        self._clients: SandboxClients | None = None
        self._active: set[str] = set()
        # Warm-pool state. Idle warmers live in _warm; the background thread
        # refills it toward config.warm_size. Sandboxes pulled from _warm are
        # also tracked in _active so close() cleans them up if release() is
        # never called.
        self._warm: queue.Queue[str] = queue.Queue()
        self._warm_stop = threading.Event()
        self._warm_refill = threading.Event()  # signalled on acquire/release
        self._warm_thread: threading.Thread | None = None
        self._warm_lock = threading.Lock()  # serialise warmer's create_sandbox

    # ----- construction ---------------------------------------------------

    @classmethod
    def from_env(cls) -> "SandboxPool":
        return cls(SandboxPoolConfig.from_env())

    def open(self) -> "SandboxPool":
        """Ensure RG + sandbox group exist, then build the clients."""
        cfg = self.config

        # Resource group via az CLI (cheaper than pulling in azure-mgmt-resource
        # just for one idempotent call). create is upsert.
        self._ensure_resource_group(cfg.resource_group, cfg.location, cfg.subscription_id)

        # Sandbox group: prefer GET-first so we skip a PUT and a propagation
        # wait on the common case (group already exists). The new
        # PyPI build of azure-containerapps-sandbox (0.1.0b1, 2026-05-30)
        # renamed the ARM client to SandboxGroupManagementClient and made
        # credential positional / subscription/rg keyword-only.
        from azure.containerapps.sandbox import SandboxGroupManagementClient
        from azure.core.exceptions import ResourceNotFoundError
        from azure.identity import AzureCliCredential

        bootstrap_mgmt = SandboxGroupManagementClient(
            AzureCliCredential(),
            subscription_id=cfg.subscription_id,
            resource_group=cfg.resource_group,
        )

        try:
            group = bootstrap_mgmt.get_group(cfg.sandbox_group)
            if group.properties.get("managementEndpoint"):
                logger.info(
                    "sandbox group %r already exists with managementEndpoint; skipping create",
                    cfg.sandbox_group,
                )
            else:
                # Edge case: group exists but endpoint not yet populated
                # (e.g. just created in another process). Wait briefly.
                self._wait_for_management_endpoint(bootstrap_mgmt, cfg.sandbox_group)
        except ResourceNotFoundError:
            logger.info(
                "creating sandbox group %r in %s/%s ...",
                cfg.sandbox_group, cfg.resource_group, cfg.location,
            )
            bootstrap_mgmt.create_group(cfg.sandbox_group, location=cfg.location)
            # Brand-new group: managementEndpoint takes 1-2 s to populate.
            self._wait_for_management_endpoint(bootstrap_mgmt, cfg.sandbox_group)

        self._clients = make_sandbox_client(
            subscription_id=cfg.subscription_id,
            resource_group=cfg.resource_group,
            sandbox_group=cfg.sandbox_group,
        )
        logger.info("sandbox pool ready: %s", self._clients.regional_endpoint)

        if cfg.warm_size > 0:
            self._start_warmer()

        return self

    @staticmethod
    def _wait_for_management_endpoint(mgmt, name: str, *, attempts: int = 6, delay_s: float = 2.0):
        for _ in range(attempts):
            group = mgmt.get_group(name)
            if group.properties.get("managementEndpoint"):
                return
            time.sleep(delay_s)
        raise RuntimeError(
            f"sandbox group {name!r} never exposed properties.managementEndpoint"
        )

    def close(self) -> None:
        """Delete all sandboxes acquired through this pool. RG/group survive."""
        if not self._clients:
            return
        # Stop the warmer first so it doesn't race us creating new sandboxes.
        self._stop_warmer()
        # Drain any remaining warm sandboxes that weren't acquired.
        while True:
            try:
                sbx_id = self._warm.get_nowait()
            except queue.Empty:
                break
            self._active.add(sbx_id)  # ensure deletion below
        for sbx_id in list(self._active):
            try:
                self._clients.client.delete_sandbox(sbx_id, self.config.sandbox_group)
            except Exception as e:  # noqa: BLE001
                logger.warning("delete_sandbox(%s) failed: %s", sbx_id, e)
            self._active.discard(sbx_id)
        try:
            self._clients.client.close()
        except Exception:
            pass
        self._clients = None

    def __enter__(self) -> "SandboxPool":
        return self.open()

    def __exit__(self, *exc):
        self.close()

    # ----- runtime --------------------------------------------------------

    @property
    def clients(self) -> SandboxClients:
        if self._clients is None:
            raise RuntimeError("SandboxPool not opened; call .open() or use as context manager")
        return self._clients

    def acquire(self, *, disk: str = "ubuntu", **kwargs) -> str:
        """Create a sandbox and return its ID.

        If the warm pool is enabled and the requested disk matches
        ``config.warm_disk`` and no extra ``kwargs`` are passed, this returns
        a pre-provisioned sandbox immediately. Otherwise falls back to a
        synchronous create.

        Any ``kwargs`` are forwarded to ``SandboxClient.create_sandbox``.
        Notable kwargs:

          - ``egress_policy=…`` — a dict from
            :class:`acas_toolkit.EgressPolicyBuilder` (or any
            wire-shape policy dict). Applied at sandbox creation time,
            so the policy is enforced from the very first packet — no
            post-create round-trip. Passing this bypasses the warm pool
            (warm sandboxes have ``defaultAction = Allow``).
          - ``cpu``, ``memory``, ``auto_suspend_seconds``, ``labels``,
            ``environment``, ``connections``, ``volumes``, ``ports``,
            ``snapshot_id``, ``preset``, ``agent_identity`` etc. — see
            the ACAS SDK ``create_sandbox`` signature for the full set.

        Emits an ``acas.sandbox.acquire`` span. Always-on attrs:
        ``acas.sandbox.disk``,
        ``acas.sandbox.acquire.source`` (``"warm"`` or ``"cold"``),
        ``acas.sandbox.id`` (set once known), and
        ``acas.egress.default_action`` when an ``egress_policy`` is
        applied (so traces tell you whether this sandbox is locked
        down without exposing the full rule list).
        """
        cfg = self.config
        tracer = get_tracer()
        # The egress policy (when supplied) is either an
        # ``EgressPolicy`` dataclass (new SDK) or a wire-shape dict
        # (legacy / from :class:`EgressPolicyBuilder`). Only
        # ``defaultAction`` is universally interesting at the span
        # level. Pull it out once for both branches.
        egress_policy = kwargs.get("egress_policy")
        default_action: str | None = None
        if isinstance(egress_policy, dict):
            default_action = egress_policy.get("defaultAction")
        elif egress_policy is not None:
            default_action = getattr(egress_policy, "default_action", None)
        with tracer.start_as_current_span("acas.sandbox.acquire") as span:
            span.set_attribute("acas.sandbox.disk", disk)
            if default_action is not None:
                span.set_attribute("acas.egress.default_action", default_action)
            try:
                if cfg.warm_size > 0 and disk == cfg.warm_disk and not kwargs:
                    try:
                        sbx_id = self._warm.get_nowait()
                        span.set_attribute("acas.sandbox.acquire.source", "warm")
                        span.set_attribute("acas.sandbox.id", sbx_id)
                        logger.info(
                            "acquired sandbox %s from warm pool (disk=%s)",
                            sbx_id, disk,
                        )
                        self._warm_refill.set()
                        return sbx_id
                    except queue.Empty:
                        logger.info("warm pool empty; falling back to cold create")

                span.set_attribute("acas.sandbox.acquire.source", "cold")
                sbx = self.clients.client.create_sandbox(
                    self.config.sandbox_group, disk=disk, **kwargs,
                )
                self._active.add(sbx.id)
                span.set_attribute("acas.sandbox.id", sbx.id)
                logger.info("acquired sandbox %s (disk=%s)", sbx.id, disk)
                return sbx.id
            except Exception as exc:
                # OTel convention: record_exception + ERROR status, then
                # let the exception propagate to the caller unchanged.
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise

    def release(self, sbx_id: str) -> None:
        """Delete a previously acquired sandbox.

        Emits an ``acas.sandbox.release`` span with the
        ``acas.sandbox.id`` attribute. The span captures the SDK
        ``delete_sandbox`` call only; the local bookkeeping (removing
        from ``_active``, signalling the warmer) runs inside the
        ``finally`` block of the original implementation and is not
        worth its own span — too cheap, no failure modes worth
        triaging in a trace.
        """
        tracer = get_tracer()
        with tracer.start_as_current_span("acas.sandbox.release") as span:
            span.set_attribute("acas.sandbox.id", sbx_id)
            try:
                try:
                    self.clients.client.delete_sandbox(sbx_id, self.config.sandbox_group)
                finally:
                    self._active.discard(sbx_id)
                    logger.info("released sandbox %s", sbx_id)
                # Signal the warmer in case it was paused waiting for capacity.
                self._warm_refill.set()
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise

    def wait_warm(self, *, n: int | None = None, timeout: float = 30.0) -> int:
        """Block until at least ``n`` warm sandboxes are ready.

        Defaults to ``config.warm_size``. Returns the queue size actually
        observed. Raises ``TimeoutError`` if the deadline elapses.
        """
        target = n if n is not None else self.config.warm_size
        if target <= 0 or self._warm_thread is None:
            return self._warm.qsize()
        deadline = time.monotonic() + timeout
        while self._warm.qsize() < target:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"warm pool only reached {self._warm.qsize()}/{target} before {timeout}s"
                )
            time.sleep(0.1)
        return self._warm.qsize()

    @contextlib.contextmanager
    def lease(self, *, disk: str = "ubuntu", **kwargs) -> Iterator[str]:
        """Acquire a sandbox for the duration of a ``with`` block."""
        sbx_id = self.acquire(disk=disk, **kwargs)
        try:
            yield sbx_id
        finally:
            self.release(sbx_id)

    # ----- passthroughs (so tool functions don't reach for `.clients.client`) --

    def exec(self, sbx_id: str, command: str, *, code: str | None = None) -> "ExecResult":
        """Run a shell ``command`` inside ``sbx_id``; return the typed ExecResult.

        Emits an ``acas.sandbox.exec`` span — the universal
        wire-level boundary for "the agent ran something in the
        sandbox". Always-on attrs: ``acas.sandbox.id``,
        ``acas.exec.exit_code``, ``acas.exec.duration_ms``,
        ``acas.exec.status`` (``"ok"`` if ``exit_code == 0`` else
        ``"error"``). Sensitive (gated on ``ENABLE_SENSITIVE_DATA``,
        4 KB truncated): ``acas.exec.cmd``, ``acas.exec.stdout``,
        ``acas.exec.stderr``.

        ``code`` (optional, sensitive) lets the Python executor
        attach the original source as ``acas.exec.code`` without
        opening its own span. Ad-hoc shell callers (``run_shell``,
        ``checkpoint`` housekeeping) don't pass it.
        """
        tracer = get_tracer()
        with tracer.start_as_current_span("acas.sandbox.exec") as span:
            span.set_attribute("acas.sandbox.id", sbx_id)
            set_sensitive_attr(span, "acas.exec.cmd", command)
            set_sensitive_attr(span, "acas.exec.code", code)
            t0 = time.monotonic()
            try:
                result = self.clients.client.exec(
                    sbx_id, self.config.sandbox_group, command,
                )
            except Exception as exc:
                # The SDK raised before we ever got an exit code — the
                # span's exit_code stays unset; the caller (executor)
                # translates this to EXIT_EXECUTOR_CRASH and surfaces
                # a synthetic ExecResult to the model. Recording the
                # exception here keeps the trace honest.
                duration_ms = int((time.monotonic() - t0) * 1000)
                span.set_attribute("acas.exec.duration_ms", duration_ms)
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise

            duration_ms = int((time.monotonic() - t0) * 1000)
            span.set_attribute("acas.exec.exit_code", result.exit_code)
            span.set_attribute("acas.exec.duration_ms", duration_ms)
            # Status is a coarse signal at this layer — exit_code 0 vs
            # not. The executor's richer ExecStatus enum (TIMEOUT, OOM,
            # NONZERO_EXIT) is a Python-side classification of the same
            # exit_code; consumers querying spans should branch on the
            # exit_code itself when they need that distinction (124 →
            # timeout, 137 → OOM, etc).
            span.set_attribute(
                "acas.exec.status", "ok" if result.exit_code == 0 else "error",
            )
            set_sensitive_attr(span, "acas.exec.stdout", result.stdout)
            set_sensitive_attr(span, "acas.exec.stderr", result.stderr)
            return result

    def write_file(self, sbx_id: str, path: str, content: bytes) -> None:
        self.clients.client.write_file(
            sbx_id, self.config.sandbox_group, path, content,
        )

    def read_file(self, sbx_id: str, path: str) -> bytes:
        return self.clients.client.read_file(
            sbx_id, self.config.sandbox_group, path,
        )

    # ----- helpers --------------------------------------------------------

    @staticmethod
    def _ensure_resource_group(name: str, location: str, subscription_id: str) -> None:
        import subprocess

        subprocess.run(
            [
                "az", "group", "create",
                "--name", name,
                "--location", location,
                "--subscription", subscription_id,
                "--output", "none",
            ],
            check=True,
        )

    # ----- warmer ---------------------------------------------------------

    def _start_warmer(self) -> None:
        if self._warm_thread is not None:
            return
        self._warm_stop.clear()
        self._warm_refill.set()  # do an initial fill
        t = threading.Thread(
            target=self._warmer_loop, name="sandbox-warmer", daemon=True,
        )
        t.start()
        self._warm_thread = t
        logger.info(
            "warm pool enabled: size=%d disk=%s",
            self.config.warm_size, self.config.warm_disk,
        )

    def _stop_warmer(self) -> None:
        if self._warm_thread is None:
            return
        self._warm_stop.set()
        self._warm_refill.set()  # wake the loop so it can exit
        self._warm_thread.join(timeout=5.0)
        self._warm_thread = None

    def _warmer_loop(self) -> None:
        cfg = self.config
        while not self._warm_stop.is_set():
            try:
                if self._warm.qsize() >= cfg.warm_size:
                    self._warm_refill.wait(timeout=1.0)
                    self._warm_refill.clear()
                    continue
                # Create one warmer.
                with self._warm_lock:
                    if self._warm_stop.is_set():
                        return
                    sbx = self._clients.client.create_sandbox(  # type: ignore[union-attr]
                        cfg.sandbox_group, disk=cfg.warm_disk,
                    )
                self._active.add(sbx.id)
                self._warm.put(sbx.id)
                logger.info("warm pool +1 sandbox %s (queue=%d/%d)",
                            sbx.id, self._warm.qsize(), cfg.warm_size)
            except Exception as e:  # noqa: BLE001
                if self._warm_stop.is_set():
                    return
                logger.warning("warmer create failed: %s; backing off", e)
                self._warm_stop.wait(timeout=2.0)


__all__ = ["SandboxPool", "SandboxPoolConfig"]

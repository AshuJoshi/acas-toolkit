"""Build ACAS egress policies declaratively, ship as a single dict.

Why this exists
---------------

The convenience helpers on the SDK (``set_egress_default``,
``add_egress_host_rule``, ...) are one HTTP round-trip per change. A
typical 3-rule policy (default=Deny + allowlist PyPI + allow files
host) currently costs 3+ ``PUT /egresspolicy`` calls *after* the
sandbox is already running — and they each refetch+merge+rewrite the
same document on the server. See the egress-demo log under
``temp/validation-2026-05-20/07-egress.log`` for a concrete example
where 3 logical changes produced 6 round-trips.

The fix is to build the whole policy on the laptop side and either:

  - Pass it once to ``create_sandbox(egress_policy=…)`` (preferred —
    the policy is enforced from the first packet the VM sends, with
    zero post-create round-trips).
  - Apply it once post-create via ``set_egress_policy(...)`` (one PUT
    instead of N).

Usage
-----

Sandbox-pool path (locked down from creation)::

    from acas_toolkit import SandboxPool, EgressPolicyBuilder

    policy = (EgressPolicyBuilder()
        .deny_by_default()
        .allow_host("pypi.org")
        .allow_host("*.pypi.org")
        .allow_host("files.pythonhosted.org")
        .build())

    with SandboxPool.from_env() as pool:
        sbx = pool.acquire(disk="python-3.13", egress_policy=policy)
        try:
            pool.exec(sbx, "pip install sympy")           # works
            pool.exec(sbx, "curl https://example.com")    # 403 (denied)
        finally:
            pool.release(sbx)

Session path (policy applies to both fresh and rehydrated sandboxes)::

    with SessionManager(pool) as mgr, mgr.session("sess-1", egress_policy=policy) as sbx_id:
        ...

Wire format
-----------

``build()`` returns a dict in the ACAS data-plane JSON shape::

    {
        "defaultAction": "Deny",
        "hostRules": [
            {"pattern": "pypi.org", "action": "Allow"},
            ...
        ],
        "rules": [...]   # advanced per-method/per-path rules; usually empty
    }

This dict is the body of ``PUT .../egresspolicy`` and the
``egressPolicy`` field of the ``PUT .../sandboxes`` body. The SDK's
``EgressPolicy._from_dict`` parses the same shape.

"""

from __future__ import annotations

from typing import Iterable


class EgressPolicyBuilder:
    """Fluent builder for an ACAS egress policy.

    Default state: ``defaultAction = "Allow"``, no host rules, no
    per-request rules. Call ``.deny_by_default()`` (or
    ``.allow_by_default()``) and then chain ``.allow_host(...)`` /
    ``.deny_host(...)`` to assemble the policy. Finalize with
    ``.build()``.
    """

    _ALLOWED_ACTIONS = ("Allow", "Deny")

    def __init__(self) -> None:
        self._default_action: str = "Allow"
        self._host_rules: list[dict] = []
        self._rules: list[dict] = []

    # ---- defaults --------------------------------------------------------

    def deny_by_default(self) -> "EgressPolicyBuilder":
        """Set ``defaultAction = "Deny"`` (zero-trust posture)."""
        self._default_action = "Deny"
        return self

    def allow_by_default(self) -> "EgressPolicyBuilder":
        """Set ``defaultAction = "Allow"`` (open posture, the SDK default)."""
        self._default_action = "Allow"
        return self

    # ---- host rules ------------------------------------------------------

    def allow_host(self, pattern: str) -> "EgressPolicyBuilder":
        """Add a host pattern to the allowlist.

        Patterns are matched against the request host by the egress
        proxy. Wildcards are supported (e.g. ``"*.pypi.org"``).
        """
        self._host_rules.append({"pattern": pattern, "action": "Allow"})
        return self

    def deny_host(self, pattern: str) -> "EgressPolicyBuilder":
        """Add a host pattern to the denylist."""
        self._host_rules.append({"pattern": pattern, "action": "Deny"})
        return self

    def allow_hosts(self, patterns: Iterable[str]) -> "EgressPolicyBuilder":
        """Add multiple host patterns to the allowlist in one call."""
        for p in patterns:
            self.allow_host(p)
        return self

    def deny_hosts(self, patterns: Iterable[str]) -> "EgressPolicyBuilder":
        """Add multiple host patterns to the denylist in one call."""
        for p in patterns:
            self.deny_host(p)
        return self

    # ---- advanced per-request rules -------------------------------------

    def allow_rule(
        self,
        *,
        host: str,
        path: str | None = None,
        methods: Iterable[str] | None = None,
        name: str | None = None,
    ) -> "EgressPolicyBuilder":
        """Add a per-request Allow rule with path/method match.

        Use ``allow_host`` for the common case of "allow this hostname".
        Use this for path or method narrowing (e.g. allow only ``GET``
        on ``files.pythonhosted.org/packages/...``).
        """
        return self._add_rule(host=host, path=path, methods=methods,
                              action="Allow", name=name)

    def deny_rule(
        self,
        *,
        host: str,
        path: str | None = None,
        methods: Iterable[str] | None = None,
        name: str | None = None,
    ) -> "EgressPolicyBuilder":
        """Add a per-request Deny rule with path/method match."""
        return self._add_rule(host=host, path=path, methods=methods,
                              action="Deny", name=name)

    def _add_rule(
        self,
        *,
        host: str,
        path: str | None,
        methods: Iterable[str] | None,
        action: str,
        name: str | None,
    ) -> "EgressPolicyBuilder":
        match: dict = {"host": host}
        if path is not None:
            match["path"] = path
        if methods is not None:
            match["methods"] = list(methods)
        rule: dict = {
            "match": match,
            "action": {"type": action},
        }
        if name is not None:
            rule["name"] = name
        self._rules.append(rule)
        return self

    # ---- terminals -------------------------------------------------------

    def build(self) -> dict:
        """Return the policy as a wire-shape dict.

        Suitable for ``SandboxClient.create_sandbox(egress_policy=…)``
        and ``SandboxClient.set_egress_policy(...)``.
        """
        policy: dict = {"defaultAction": self._default_action}
        if self._host_rules:
            # Copy the inner dicts so callers can't mutate builder state
            # by mutating the returned policy.
            policy["hostRules"] = [dict(r) for r in self._host_rules]
        if self._rules:
            policy["rules"] = [self._deep_copy_rule(r) for r in self._rules]
        return policy

    @staticmethod
    def _deep_copy_rule(rule: dict) -> dict:
        out = {"match": dict(rule["match"]), "action": dict(rule["action"])}
        if "name" in rule:
            out["name"] = rule["name"]
        if "methods" in out["match"]:
            out["match"]["methods"] = list(out["match"]["methods"])
        return out

    # ---- convenience presets --------------------------------------------

    @classmethod
    def pip_allowlist(cls) -> "EgressPolicyBuilder":
        """A common preset: deny-by-default + the hosts ``pip install`` needs.

        ``pypi.org``, ``*.pypi.org``, and ``files.pythonhosted.org`` are
        the hosts pip touches by default to resolve and fetch packages.
        Returns a fresh builder you can extend further before
        ``.build()``.
        """
        return (cls()
                .deny_by_default()
                .allow_hosts(["pypi.org", "*.pypi.org", "files.pythonhosted.org"]))


__all__ = ["EgressPolicyBuilder"]

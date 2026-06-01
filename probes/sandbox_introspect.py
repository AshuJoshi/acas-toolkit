"""
Probe A1: sandbox system-level introspection.

Goal
----
Find out what an ACAS sandbox actually *is* at the resource level — what the
SDK tells us, what the in-VM environment looks like (IP, hostname, kernel,
filesystem, networking) — and how those facts differ between a freshly
created sandbox and one rehydrated from a snapshot of the first.

The probe produces two clearly-labelled report blocks (FRESH and REHYDRATED)
followed by a side-by-side diff. Each block contains:

  1. SDK view: ``client.get_sandbox(...)`` dump and ``client.get_stats(...)``.
  2. In-sandbox view: a battery of shell commands captured via ``pool.exec``.
  3. A "scratch marker" file written before snapshot — used to verify FS
     persistence across rehydrate.

Run
---
    uv run python probes/sandbox_introspect.py | tee temp/sandbox_introspect.txt

This script DOES NOT use a warm pool. It exercises the cold path on purpose
so the resource-level picture isn't perturbed by long-lived pool sandboxes.
"""

from __future__ import annotations

import dataclasses
import logging
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from acas_toolkit import SandboxPool, SandboxPoolConfig  # noqa: E402


# Battery of shell commands run inside the sandbox. Each entry is (label, cmd).
# Keep them defensive: missing binaries should not abort the run.
PROBES: list[tuple[str, str]] = [
    ("hostname",        "hostname"),
    ("uname",           "uname -a"),
    ("os-release",      "cat /etc/os-release 2>/dev/null | head -n 20"),
    ("kernel-cmdline",  "cat /proc/cmdline 2>/dev/null"),
    ("uptime",          "cat /proc/uptime 2>/dev/null"),
    ("cpu-summary",     "grep -E 'model name|processor|cpu cores' /proc/cpuinfo | head -n 8"),
    ("nproc",           "nproc"),
    ("mem",             "free -h 2>/dev/null || cat /proc/meminfo | head -n 5"),
    ("disk",            "df -hT 2>/dev/null | head -n 20"),
    ("mounts",          "mount | head -n 30"),
    ("ip-addr",         "ip -o addr 2>/dev/null || ifconfig -a 2>/dev/null | head -n 40"),
    ("ip-link",         "ip -o link 2>/dev/null"),
    ("ip-route",        "ip route 2>/dev/null || route -n 2>/dev/null"),
    ("resolv",          "cat /etc/resolv.conf 2>/dev/null"),
    ("hosts",           "cat /etc/hosts 2>/dev/null"),
    ("listening-ports", "ss -tlnp 2>/dev/null | head -n 30 || netstat -tlnp 2>/dev/null | head -n 30"),
    ("default-iface",   "ip -o -4 route show to default 2>/dev/null"),
    ("public-egress-ip","curl -sS --max-time 5 https://api.ipify.org 2>/dev/null || echo '(blocked or no curl)'"),
    ("env",             "env | sort | head -n 40"),
    ("whoami",          "id"),
    ("cgroups",         "cat /proc/1/cgroup 2>/dev/null"),
    ("virt-detect",     "systemd-detect-virt 2>/dev/null || echo '(systemd-detect-virt unavailable)'"),
    ("dmesg-virt",      "dmesg 2>/dev/null | grep -iE 'hypervisor|kvm|firecracker|cloud[- ]hypervisor|virtio' | head -n 10 || echo '(no dmesg access)'"),
    ("scratch-marker",  "cat /tmp/probe-marker.txt 2>/dev/null || echo '(no marker)'"),
]


def _print_h1(s: str) -> None:
    print()
    print("=" * 78)
    print(s)
    print("=" * 78)


def _print_h2(s: str) -> None:
    print()
    print("-- " + s + " " + "-" * max(0, 74 - len(s)))


def _sdk_dump(pool: SandboxPool, sbx_id: str) -> dict[str, object]:
    """Pull the SDK-level view of the sandbox into a dict."""
    cfg = pool.config
    client = pool.clients.client
    sbx = client.get_sandbox(sbx_id, cfg.sandbox_group)

    out: dict[str, object] = {}
    out["id"] = sbx.id
    out["state"] = sbx.state
    out["labels"] = dict(sbx.labels or {})
    out["vmm_type"] = sbx.vmm_type
    out["preset_sandbox_type"] = sbx.preset_sandbox_type
    out["sandbox_group_id"] = sbx.sandbox_group_id
    out["resources"] = dataclasses.asdict(sbx.resources) if sbx.resources else None
    out["ports"] = [dataclasses.asdict(p) for p in (sbx.ports or [])]
    out["connections"] = list(sbx.connections or [])
    out["environment_keys"] = sorted((sbx.environment or {}).keys())
    out["sources_ref"] = dataclasses.asdict(sbx.sources_ref) if sbx.sources_ref else None
    out["lifecycle"] = dataclasses.asdict(sbx.lifecycle) if sbx.lifecycle else None
    out["egress_default"] = (sbx.egress_policy.default_action
                             if sbx.egress_policy else None)

    try:
        stats = client.get_stats(sbx_id, cfg.sandbox_group)
        out["stats"] = dataclasses.asdict(stats)
    except Exception as e:  # noqa: BLE001
        out["stats_error"] = repr(e)

    return out


def _print_dict(d: dict[str, object]) -> None:
    for k in sorted(d):
        v = d[k]
        print(f"  {k}: {v}")


def _run_probes(pool: SandboxPool, sbx_id: str) -> dict[str, str]:
    """Run the in-sandbox probe battery; return a dict of label -> output."""
    out: dict[str, str] = {}
    for label, cmd in PROBES:
        try:
            r = pool.exec(sbx_id, cmd)
            text = (r.stdout or "").rstrip()
            if r.exit_code != 0 and r.stderr:
                text += f"\n  [stderr rc={r.exit_code}] {r.stderr.strip()[:300]}"
            out[label] = text or f"(empty, rc={r.exit_code})"
        except Exception as e:  # noqa: BLE001
            out[label] = f"(exec error: {e!r})"
    return out


def _print_probes(probes: dict[str, str]) -> None:
    for label, _cmd in PROBES:
        _print_h2(label)
        text = probes.get(label, "(missing)")
        for line in text.splitlines() or ["(empty)"]:
            print("  " + line)


def _diff_sdk(a: dict[str, object], b: dict[str, object]) -> list[str]:
    """Show keys whose values differ. Used to surface fresh vs rehydrate deltas."""
    keys = sorted(set(a) | set(b))
    rows: list[str] = []
    for k in keys:
        va, vb = a.get(k), b.get(k)
        if va != vb:
            rows.append(f"  - {k}: FRESH={va!r}  REHYDRATED={vb!r}")
    return rows


def _diff_probes(a: dict[str, str], b: dict[str, str]) -> list[str]:
    rows: list[str] = []
    for label, _cmd in PROBES:
        va, vb = (a.get(label) or "").strip(), (b.get(label) or "").strip()
        if va != vb:
            rows.append(f"  - {label}: changed")
            # show short snippets for context
            rows.append(f"      FRESH      : {va.splitlines()[0] if va else ''!r}")
            rows.append(f"      REHYDRATED : {vb.splitlines()[0] if vb else ''!r}")
    return rows


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    cfg = SandboxPoolConfig.from_env()
    print(f"[probe] subscription={cfg.subscription_id}")
    print(f"[probe] rg={cfg.resource_group} group={cfg.sandbox_group} region={cfg.location}")

    fresh_sdk: dict[str, object] = {}
    fresh_probes: dict[str, str] = {}
    rehydrate_sdk: dict[str, object] = {}
    rehydrate_probes: dict[str, str] = {}

    with SandboxPool(cfg) as pool:
        # --- 1. Fresh sandbox ---------------------------------------------
        _print_h1("PHASE 1 — fresh sandbox (cold create)")
        t0 = time.monotonic()
        sbx1 = pool.acquire(disk="python-3.13")
        print(f"[probe] cold acquire: {sbx1} in {(time.monotonic() - t0)*1000:.0f} ms")

        # Write a scratch marker BEFORE snapshot so we can detect FS persistence.
        marker_value = f"hello from sbx1 at {time.time():.3f}"
        pool.exec(sbx1, f"printf %s {marker_value!r} > /tmp/probe-marker.txt")

        _print_h2("SDK view (fresh)")
        fresh_sdk = _sdk_dump(pool, sbx1)
        _print_dict(fresh_sdk)

        _print_h2("In-sandbox probes (fresh)")
        fresh_probes = _run_probes(pool, sbx1)
        _print_probes(fresh_probes)

        # --- 2. Snapshot & rehydrate -------------------------------------
        _print_h1("PHASE 2 — snapshot then rehydrate")
        t0 = time.monotonic()
        snap = pool.clients.client.create_snapshot(sbx1, cfg.sandbox_group, name="probe-a1")
        snap_ms = (time.monotonic() - t0) * 1000
        print(f"[probe] create_snapshot: {snap.id} in {snap_ms:.0f} ms")

        # Tear down the original so the new one is a clean rehydrate.
        pool.release(sbx1)
        print(f"[probe] released original sandbox {sbx1}")

        # Create a NEW sandbox from the snapshot. Use the raw SDK call so we
        # bypass the warm-pool path entirely and can pass snapshot_id.
        t0 = time.monotonic()
        sbx2_obj = pool.clients.client.create_sandbox(
            cfg.sandbox_group, snapshot_id=snap.id,
        )
        sbx2 = sbx2_obj.id
        # Make sure the pool will tear it down on exit.
        pool._active.add(sbx2)  # type: ignore[attr-defined]
        rehydrate_ms = (time.monotonic() - t0) * 1000
        print(f"[probe] rehydrate create_sandbox(snapshot_id=...): {sbx2} in {rehydrate_ms:.0f} ms")

        _print_h2("SDK view (rehydrated)")
        rehydrate_sdk = _sdk_dump(pool, sbx2)
        _print_dict(rehydrate_sdk)

        _print_h2("In-sandbox probes (rehydrated)")
        rehydrate_probes = _run_probes(pool, sbx2)
        _print_probes(rehydrate_probes)

        # Tear down the snapshot so we don't leak storage.
        try:
            pool.clients.client.delete_snapshot(snap.id, cfg.sandbox_group)
            print(f"[probe] deleted snapshot {snap.id}")
        except Exception as e:  # noqa: BLE001
            print(f"[probe] snapshot delete failed: {e!r}")

    # --- 3. Diff -----------------------------------------------------------
    _print_h1("PHASE 3 — fresh vs rehydrated diff")
    _print_h2("SDK-level deltas")
    sdk_diff = _diff_sdk(fresh_sdk, rehydrate_sdk)
    print("\n".join(sdk_diff) if sdk_diff else "  (no differences)")

    _print_h2("In-sandbox deltas (first line shown)")
    p_diff = _diff_probes(fresh_probes, rehydrate_probes)
    print("\n".join(p_diff) if p_diff else "  (no differences)")

    _print_h2("Key questions, answered")
    print(f"  - Scratch marker survived rehydrate? "
          f"{'YES' if 'hello from sbx1' in (rehydrate_probes.get('scratch-marker') or '') else 'NO'}")
    print(f"  - Hostname changed across rehydrate? "
          f"{'YES' if fresh_probes.get('hostname') != rehydrate_probes.get('hostname') else 'NO'}")
    print(f"  - IP address(es) changed?           "
          f"{'YES' if fresh_probes.get('ip-addr') != rehydrate_probes.get('ip-addr') else 'NO'}")
    print(f"  - Default route changed?            "
          f"{'YES' if fresh_probes.get('default-iface') != rehydrate_probes.get('default-iface') else 'NO'}")
    print(f"  - Public egress IP changed?         "
          f"{'YES' if fresh_probes.get('public-egress-ip') != rehydrate_probes.get('public-egress-ip') else 'NO'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

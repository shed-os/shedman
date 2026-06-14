#!/usr/bin/env python3
"""pilot.py — test suite for shedos-doctor.

Each case spins up a disposable /etc + /var/lib/shedos + a stub systemctl
and invokes shedos-doctor as a real subprocess. Hooks for notify/waybar
refresh/apply are captured via SHEDOS_DOCTOR_*_CMD env overrides so we
can assert on what shedos-doctor *intended* to do without touching the
live desktop.

Covers every mode in the CLI:

  bare             exit 0 when aligned, 1 with drift, 2 on schema error
  --json           shape: {aligned, drift_count, changes, plan_hash}
  --waybar         shape: {text, tooltip, class, alt}; class flips
  --tick           notifies once per drift fingerprint; refreshes waybar
  --fix            execs shedos-apply via the override hook
  --diff           renders the plan's diffs (non-empty on drift)
  --reset-notify-state  resets the hysteresis so next tick re-notifies
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL = REPO_ROOT / "packaging/shedos-system/tree/usr/libexec/shedman/doctor"
LIB_ROOT = REPO_ROOT / "packaging/shedos-system/tree/usr/lib/shedos"


def _write_stub_systemctl(dst: Path, enabled: list[str],
                          user_enabled: list[str]) -> None:
    """Stub responds to `list-unit-files --state=enabled` with fixture
    content; any other invocation returns 0 (the planner only reads)."""
    enabled_path = dst.parent / "enabled.txt"
    user_path = dst.parent / "user-enabled.txt"
    enabled_path.write_text("\n".join(enabled) + "\n" if enabled else "")
    user_path.write_text("\n".join(user_enabled) + "\n" if user_enabled else "")
    dst.write_text(f"""#!/usr/bin/env bash
scope=system
if [[ "$1" == "--user" ]]; then
    scope=user; shift
    [[ "$1" == "--global" ]] && shift
fi
if [[ "$1" == "list-unit-files" ]]; then
    list={enabled_path!s}
    [[ "$scope" == "user" ]] && list={user_path!s}
    while IFS= read -r u; do
        [[ -z "$u" ]] && continue
        printf '%s enabled enabled\\n' "$u"
    done <"$list"
    exit 0
fi
exit 0
""")
    dst.chmod(0o755)


def _make_env(td: Path, *, notify_log: Path, refresh_log: Path,
              apply_log: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "SHEDOS_APPLY_ETC_ROOT":     str(td / "etc"),
        "SHEDOS_APPLY_STATE_ROOT":   str(td / "state"),
        "SHEDOS_APPLY_SYSTEMCTL":    str(td / "stubs" / "systemctl"),
        "SHEDOS_LIB_ROOT":           str(LIB_ROOT),
        "NO_COLOR":                  "1",
        "SHEDOS_DOCTOR_NOTIFY_CMD":  f"sh -c 'printf \"%s\\t%s\\n\" \"$0\" \"$1\" >> {notify_log}'",
        "SHEDOS_DOCTOR_REFRESH_CMD": f"sh -c 'echo REFRESHED >> {refresh_log}'",
        "SHEDOS_DOCTOR_APPLY_CMD":   f"sh -c 'echo FIXED: \"$@\" >> {apply_log}'",
        # Sandbox the post-boot breadcrumb so cases never read the real one.
        "SHEDOS_MOUNT_REPORT_STATE": str(td / "mount-missed.json"),
    })
    return env


def _setup(td: Path, *, toml: str,
           enabled: list[str] | None = None,
           user_enabled: list[str] | None = None) -> None:
    etc = td / "etc"; etc.mkdir(parents=True, exist_ok=True)
    (etc / "shedos").mkdir(exist_ok=True)
    (etc / "shedos" / "system.toml").write_text(toml)
    (td / "state").mkdir(exist_ok=True)
    stubs = td / "stubs"; stubs.mkdir(exist_ok=True)
    _write_stub_systemctl(stubs / "systemctl",
                          enabled or [], user_enabled or [])


def _run(td: Path, *args: str, env_extra: dict[str, str] | None = None
        ) -> subprocess.CompletedProcess:
    notify = td / "notify.log"; notify.touch()
    refresh = td / "refresh.log"; refresh.touch()
    apply_log = td / "apply.log"; apply_log.touch()
    env = _make_env(td, notify_log=notify, refresh_log=refresh,
                    apply_log=apply_log)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [str(TOOL), "--config", str(td / "etc" / "shedos" / "system.toml"),
         *args],
        capture_output=True, text=True, env=env, cwd=str(td),
    )


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


def case_aligned_bare(td: Path) -> None:
    _setup(td, toml='schema = 1\n[systemd.system]\nenable=["a.service"]\n',
           enabled=["a.service"])
    r = _run(td)
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert "aligned" in r.stdout, r.stdout


def case_aligned_json(td: Path) -> None:
    _setup(td, toml='schema = 1\n')
    r = _run(td, "--json")
    assert r.returncode == 0
    doc = json.loads(r.stdout)
    assert doc["aligned"] is True
    assert doc["drift_count"] == 0
    assert "plan_hash" in doc


def case_aligned_waybar(td: Path) -> None:
    _setup(td, toml='schema = 1\n')
    r = _run(td, "--waybar")
    assert r.returncode == 0
    wb = json.loads(r.stdout)
    assert wb["class"] == "aligned"
    assert wb["text"] == ""


def case_drift_bare(td: Path) -> None:
    _setup(td,
           toml='schema = 1\n[systemd.system]\nenable=["missing.service"]\n',
           enabled=[])
    r = _run(td)
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert "drift" in r.stdout.lower()
    assert "missing.service" in r.stdout


def case_drift_json(td: Path) -> None:
    _setup(td,
           toml='schema = 1\n[systemd.system]\nenable=["foo.service"]\ndisable=["bar.service"]\n',
           enabled=["bar.service"])
    r = _run(td, "--json")
    assert r.returncode == 1
    doc = json.loads(r.stdout)
    assert doc["aligned"] is False
    assert doc["drift_count"] == 2
    summaries = [c["summary"] for c in doc["changes"]]
    assert any("enable foo.service" in s for s in summaries), summaries
    assert any("disable bar.service" in s for s in summaries), summaries


def case_drift_waybar(td: Path) -> None:
    _setup(td, toml='schema = 1\n[systemd.system]\nenable=["x.service"]\n')
    r = _run(td, "--waybar")
    assert r.returncode == 0
    wb = json.loads(r.stdout)
    assert wb["class"] == "drift"
    assert "1" in wb["text"]
    assert "x.service" in wb["tooltip"]


def case_tick_notifies_once(td: Path) -> None:
    _setup(td, toml='schema = 1\n[systemd.system]\nenable=["q.service"]\n')
    r1 = _run(td, "--tick")
    assert r1.returncode == 0
    first = (td / "notify.log").read_text()
    assert "q.service" in first, first
    assert (td / "refresh.log").read_text().count("REFRESHED") == 1

    r2 = _run(td, "--tick")
    assert r2.returncode == 0
    # Hysteresis: second tick with same drift does not re-notify.
    second = (td / "notify.log").read_text()
    assert second == first, f"re-notified: {second!r}"


def case_tick_different_drift_renotifies(td: Path) -> None:
    _setup(td, toml='schema = 1\n[systemd.system]\nenable=["q.service"]\n')
    _run(td, "--tick")  # primes the hysteresis
    # Change the drift.
    (td / "etc" / "shedos" / "system.toml").write_text(
        'schema = 1\n[systemd.system]\nenable=["r.service"]\n')
    _run(td, "--tick")
    log = (td / "notify.log").read_text()
    # Two distinct notifications.
    assert log.count("\n") >= 2, log


def case_tick_clears_when_drift_resolved(td: Path) -> None:
    _setup(td, toml='schema = 1\n[systemd.system]\nenable=["z.service"]\n')
    _run(td, "--tick")
    # Resolve drift by adding the unit to the enabled stub.
    _write_stub_systemctl(td / "stubs" / "systemctl",
                          ["z.service"], [])
    r = _run(td, "--tick")
    assert r.returncode == 0
    # Hysteresis file should now hold empty hash; next-drift notify must fire.
    (td / "etc" / "shedos" / "system.toml").write_text(
        'schema = 1\n[systemd.system]\nenable=["new.service"]\n')
    before = (td / "notify.log").read_text()
    _run(td, "--tick")
    after = (td / "notify.log").read_text()
    assert "new.service" in after.replace(before, ""), (before, after)


def case_fix_delegates(td: Path) -> None:
    _setup(td, toml='schema = 1\n[systemd.system]\nenable=["w.service"]\n')
    r = _run(td, "--fix")
    assert r.returncode == 0
    log = (td / "apply.log").read_text()
    assert "FIXED" in log, log


def case_schema_error(td: Path) -> None:
    _setup(td, toml='schema = 99\n')
    r = _run(td)
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert "99" in r.stderr, r.stderr


def case_diff_renders(td: Path) -> None:
    _setup(td,
           toml='schema = 1\n[drop-ins]\n"sysctl.d/99.conf"="vm.swappiness=10\\n"\n')
    r = _run(td, "--diff")
    assert r.returncode == 1, (r.returncode, r.stderr)
    # drop-in diff is unified; summary + content line both show up
    assert "99.conf" in r.stdout, r.stdout


def case_reset_notify_state(td: Path) -> None:
    _setup(td, toml='schema = 1\n[systemd.system]\nenable=["k.service"]\n')
    _run(td, "--tick")
    r = _run(td, "--reset-notify-state")
    assert r.returncode == 0
    state = json.loads((td / "state" / "doctor.state.json").read_text())
    assert state.get("last_notified_hash", "") == "", state
    assert state.get("last_missed_fp", "") == "", state


def case_plan_is_side_effect_free(td: Path) -> None:
    # Ledger A3: doctor (and apply --dry-run) used to seed permanent
    # per-section baselines and rewrite state files during PLANNING.
    # Drive a plan over reconciler-backed sections and assert the state
    # dir comes through byte-identical — twice, for determinism.
    _setup(td, toml=(
        'schema = 1\n'
        '[network.firewall]\n'
        'enabled = false\n'
    ))
    ufw = td / "stubs" / "ufw"
    ufw.write_text('#!/usr/bin/env bash\necho "Status: inactive"\nexit 0\n')
    ufw.chmod(0o755)
    env = {"SHEDOS_APPLY_UFW": str(ufw)}

    def snapshot() -> dict[str, bytes]:
        state = td / "state"
        return {str(p): p.read_bytes() for p in sorted(state.rglob("*"))
                if p.is_file()}

    before = snapshot()
    r1 = _run(td, env_extra=env)
    assert r1.returncode in (0, 1), (r1.returncode, r1.stderr)
    after1 = snapshot()
    assert before == after1, (
        f"planning wrote state: {set(after1) ^ set(before)} "
        f"or mutated contents"
    )
    r2 = _run(td, env_extra=env)
    assert r2.returncode == r1.returncode
    assert snapshot() == before, "second plan wrote state"


def case_snapper_unreadable_is_note_not_crash(td: Path) -> None:
    # Ledger A1: the snapper config is root-readable only (0640); a
    # non-root doctor must degrade to a note, not die with Errno 13.
    if os.geteuid() == 0:
        return  # root can read anything; the case only means something unprivileged
    _setup(td, toml='schema = 1\n[snapper.timeline]\ndaily = 7\n')
    snap_cfg = td / "etc" / "snapper" / "configs" / "root"
    snap_cfg.parent.mkdir(parents=True, exist_ok=True)
    snap_cfg.write_text('TIMELINE_LIMIT_DAILY="10"\n')
    snap_cfg.chmod(0o000)
    try:
        r = _run(td)
        assert r.returncode in (0, 1), (r.returncode, r.stderr)
        assert "root-readable only" in r.stdout, r.stdout
        j = _run(td, "--json")
        doc = json.loads(j.stdout)
        assert any("root-readable" in n for n in doc.get("notes", [])), doc
    finally:
        snap_cfg.chmod(0o644)


_RISKY_FSTAB = (
    "UUID=ROOT / btrfs subvol=/@,defaults 0 0\n"
    "UUID=ROOT /home btrfs subvol=/@home,defaults 0 0\n"        # same fs as root -> safe
    "UUID=BKP /mnt/backup btrfs subvol=@backup,defaults 0 0\n"  # separate, no nofail -> risk
)
_SAFE_FSTAB = (
    "UUID=ROOT / btrfs subvol=/@,defaults 0 0\n"
    "UUID=ROOT /home btrfs subvol=/@home,defaults 0 0\n"
)


def case_mount_safety_warns(td: Path) -> None:
    _setup(td, toml='schema = 1\n')
    (td / "etc" / "fstab").write_text(_RISKY_FSTAB)
    r = _run(td)
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert "boot safety" in r.stdout, r.stdout
    assert "/mnt/backup" in r.stdout, r.stdout
    # the same-fs /home subvol must NOT be flagged
    assert "/home" not in r.stdout, r.stdout


def case_mount_safety_json(td: Path) -> None:
    _setup(td, toml='schema = 1\n')
    (td / "etc" / "fstab").write_text(_RISKY_FSTAB)
    r = _run(td, "--json")
    assert r.returncode == 1, (r.returncode, r.stderr)
    doc = json.loads(r.stdout)
    ms = doc.get("mount_safety", [])
    assert [m["target"] for m in ms] == ["/mnt/backup"], ms


def case_mount_safety_aligned_when_safe(td: Path) -> None:
    _setup(td, toml='schema = 1\n')
    (td / "etc" / "fstab").write_text(_SAFE_FSTAB)
    r = _run(td)
    assert r.returncode == 0, (r.returncode, r.stdout)
    assert "boot safety" not in r.stdout, r.stdout


def case_mount_safety_fix(td: Path) -> None:
    _setup(td, toml='schema = 1\n')
    fstab = td / "etc" / "fstab"
    fstab.write_text(_RISKY_FSTAB)
    r = _run(td, "--fix-mounts", "--yes")
    assert r.returncode == 0, (r.returncode, r.stderr)
    txt = fstab.read_text()
    assert "subvol=@backup,defaults,nofail,x-systemd.device-timeout=5s" in txt, txt
    assert "UUID=ROOT / btrfs subvol=/@,defaults 0 0" in txt, txt  # untouched
    bak = td / "etc" / "fstab.shedos-bak"
    assert bak.exists() and bak.read_text() == _RISKY_FSTAB, "backup missing/wrong"
    # the fix is complete: a re-audit is clean
    r2 = _run(td)
    assert r2.returncode == 0, (r2.returncode, r2.stdout)
    assert "boot safety" not in r2.stdout, r2.stdout


_MISSED_BREADCRUMB = json.dumps({"missed": [
    {"target": "/mnt/data", "device": "UUID=GONE", "fstype": "ext4"}]})


def case_mount_missed_warns(td: Path) -> None:
    _setup(td, toml='schema = 1\n')
    (td / "mount-missed.json").write_text(_MISSED_BREADCRUMB)
    r = _run(td)
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert "did not mount this boot" in r.stdout, r.stdout
    assert "/mnt/data" in r.stdout, r.stdout


def case_mount_missed_json(td: Path) -> None:
    _setup(td, toml='schema = 1\n')
    (td / "mount-missed.json").write_text(_MISSED_BREADCRUMB)
    r = _run(td, "--json")
    assert r.returncode == 1, (r.returncode, r.stderr)
    doc = json.loads(r.stdout)
    assert [m["target"] for m in doc.get("mount_missed", [])] == ["/mnt/data"], doc


def case_mount_missed_absent_is_clean(td: Path) -> None:
    _setup(td, toml='schema = 1\n')
    # no breadcrumb written -> nothing missed, nothing to report
    r = _run(td)
    assert r.returncode == 0, (r.returncode, r.stdout)
    assert "did not mount" not in r.stdout, r.stdout


def case_mount_missed_tick_critical(td: Path) -> None:
    _setup(td, toml='schema = 1\n')
    (td / "mount-missed.json").write_text(_MISSED_BREADCRUMB)
    log = td / "urgency.log"; log.touch()
    env = {"SHEDOS_DOCTOR_NOTIFY_CMD":
           f"sh -c 'printf \"%s|%s|%s\\n\" \"$0\" \"$1\" \"$2\" >> {log}'"}
    r = _run(td, "--tick", env_extra=env)
    assert r.returncode == 0, (r.returncode, r.stderr)
    fired = log.read_text()
    assert "a disk did not mount this boot" in fired, fired
    assert "critical" in fired, fired


CASES = [
    ("aligned-bare",              case_aligned_bare),
    ("aligned-json",              case_aligned_json),
    ("aligned-waybar",            case_aligned_waybar),
    ("drift-bare",                case_drift_bare),
    ("drift-json",                case_drift_json),
    ("drift-waybar",              case_drift_waybar),
    ("tick-notifies-once",        case_tick_notifies_once),
    ("tick-renotifies-on-change", case_tick_different_drift_renotifies),
    ("tick-clears-on-resolve",    case_tick_clears_when_drift_resolved),
    ("fix-delegates",             case_fix_delegates),
    ("schema-error",              case_schema_error),
    ("diff-renders",              case_diff_renders),
    ("reset-notify-state",        case_reset_notify_state),
    ("plan-side-effect-free",     case_plan_is_side_effect_free),
    ("snapper-unreadable-note",   case_snapper_unreadable_is_note_not_crash),
    ("mount-safety-warns",        case_mount_safety_warns),
    ("mount-safety-json",         case_mount_safety_json),
    ("mount-safety-aligned-safe", case_mount_safety_aligned_when_safe),
    ("mount-safety-fix",          case_mount_safety_fix),
    ("mount-missed-warns",        case_mount_missed_warns),
    ("mount-missed-json",         case_mount_missed_json),
    ("mount-missed-absent-clean", case_mount_missed_absent_is_clean),
    ("mount-missed-tick-critical", case_mount_missed_tick_critical),
]


def main() -> int:
    if not TOOL.exists():
        print(f"FATAL: {TOOL} missing", file=sys.stderr)
        return 2

    only = sys.argv[1:]
    cases = [(n, f) for n, f in CASES if not only or n in only]
    if only and not cases:
        print(f"no matching cases: {only}", file=sys.stderr)
        return 2

    passed = failed = 0
    failures: list[tuple[str, str]] = []
    for name, func in cases:
        with tempfile.TemporaryDirectory(prefix="shedos-doctor-test.") as td:
            try:
                func(Path(td))
                print(f"PASS {name}")
                passed += 1
            except AssertionError as e:
                print(f"FAIL {name}: {e}")
                failures.append((name, str(e)))
                failed += 1
            except Exception as e:
                print(f"FAIL {name}: {type(e).__name__}: {e}")
                failures.append((name, f"{type(e).__name__}: {e}"))
                failed += 1

    print()
    print(f"Summary: {passed} passed, {failed} failed")
    if failed:
        for n, w in failures:
            print(f"  {n}: {w}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

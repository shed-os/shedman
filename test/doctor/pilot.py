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
import shutil
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
    state = (td / "state" / "doctor.state.json").read_text()
    assert '"last_notified_hash": ""' in state, state


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

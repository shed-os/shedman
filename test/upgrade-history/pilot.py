#!/usr/bin/env python3
"""pilot.py — test suite for shedos-upgrade-history.

Two layers:

  - Pure-helper assertions for ``parse_snapper_list``,
    ``group_into_runs``, ``parse_snapper_status``, ``summarize_changes``,
    and the date normalizer. These exercise the parsers against synthetic
    JSON + `snapper status` fixtures; no Textual needed.

  - Pilot cases that spin up the real ``UpgradeHistoryApp`` via
    ``App.run_test()`` and drive the snapper / rollback subprocesses
    through the documented env-var hooks
    (``SHEDOS_HISTORY_SNAPPER_LIST_CMD``, ``SHEDOS_HISTORY_SNAPPER_STATUS_CMD``,
    ``SHEDOS_HISTORY_ROLLBACK_CMD``).

The fixtures are assembled in-process — one well-formed JSON blob with
two upgrade pairs and a manual snapshot, one `snapper status` stream
that yields (1 added, 1 removed, 1 updated), and a harmless "rollback
command" that just echoes a success string.
"""

from __future__ import annotations

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL_PATH = REPO_ROOT / "tree/usr/libexec/shedman/upgrade-history"


def _load_module():
    loader = importlib.machinery.SourceFileLoader("shedos_uh", str(TOOL_PATH))
    spec = importlib.util.spec_from_loader("shedos_uh", loader)
    m = importlib.util.module_from_spec(spec)
    sys.modules["shedos_uh"] = m
    loader.exec_module(m)
    return m


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _fixture_json() -> str:
    doc = {"root": [
        {"number": 0, "type": "single", "date": "2025-01-01 00:00:00",
         "description": "current", "userdata": {}},
        {"number": 30, "type": "pre",  "date": "2026-04-18 14:02:11",
         "description": "shedos-update pre",
         "userdata": {"source": "shedos-update", "kind": "pre"}},
        {"number": 31, "type": "post", "date": "2026-04-18 14:05:22",
         "description": "shedos-update post",
         "userdata": {"source": "shedos-update", "kind": "post"},
         "pre-number": 30},
        {"number": 32, "type": "pre",  "date": "2026-04-21 09:15:00",
         "description": "shedos-update pre",
         "userdata": {"source": "shedos-update", "kind": "pre"}},
        {"number": 33, "type": "post", "date": "2026-04-21 09:17:00",
         "description": "shedos-update post",
         "userdata": {"source": "shedos-update", "kind": "post"},
         "pre-number": 32},
        {"number": 40, "type": "single", "date": "2026-04-22 10:00:00",
         "description": "manual snap", "userdata": {}},
    ]}
    return json.dumps(doc)


def _fixture_status() -> str:
    return (
        "c..... /boot/vmlinuz-linux\n"
        "+..... /var/lib/pacman/local/newpkg-1.2.3-1/\n"
        "+..... /var/lib/pacman/local/newpkg-1.2.3-1/desc\n"
        "-..... /var/lib/pacman/local/oldpkg-0.9-3/\n"
        "-..... /var/lib/pacman/local/oldpkg-0.9-3/desc\n"
        "+..... /var/lib/pacman/local/bumped-pkg-2.0-1/\n"
        "-..... /var/lib/pacman/local/bumped-pkg-1.9-1/\n"
        "c..... /etc/fstab\n"
    )


def _write_fixtures(tmp: Path) -> tuple[Path, Path, Path]:
    j = tmp / "snapper.json";  j.write_text(_fixture_json())
    s = tmp / "status.txt";    s.write_text(_fixture_status())
    r = tmp / "rollback.txt";  r.write_text("rollback queued (mock)\n")
    return j, s, r


# ---------------------------------------------------------------------------
# Pure-helper tests
# ---------------------------------------------------------------------------


def test_helpers(m) -> None:
    snaps = m.parse_snapper_list(_fixture_json())
    assert len(snaps) == 6, f"parse: expected 6, got {len(snaps)}"
    view = m.group_into_runs(snaps)
    assert len(view.runs) == 2, f"runs: expected 2, got {len(view.runs)}"
    assert view.runs[0].pre.number == 32
    assert view.runs[1].pre.number == 30
    assert len(view.manual) == 1 and view.manual[0].number == 40
    assert len(view.orphans) == 0

    # userdata-as-list variant (older snapper versions)
    alt = json.dumps({"root": [
        {"number": 1, "type": "pre",  "date": "2026-01-01 00:00:00",
         "description": "pre",
         "userdata": ["source=shedos-update", "kind=pre"]},
        {"number": 2, "type": "post", "date": "2026-01-01 00:01:00",
         "description": "post",
         "userdata": ["source=shedos-update", "kind=post"],
         "pre-number": 1},
    ]})
    alt_view = m.group_into_runs(m.parse_snapper_list(alt))
    assert len(alt_view.runs) == 1, "userdata-as-list: pairs missed"

    assert m.parse_snapper_list("") == []
    assert m.parse_snapper_list("not-json") == []

    changes = m.parse_snapper_status(_fixture_status())
    by_name = {c.name: c for c in changes}
    assert by_name["newpkg"].kind == "added"
    assert by_name["oldpkg"].kind == "removed"
    assert by_name["bumped-pkg"].kind == "updated"
    assert by_name["bumped-pkg"].from_version == "1.9-1"
    assert by_name["bumped-pkg"].to_version == "2.0-1"

    added, removed, updated = m.summarize_changes(changes)
    assert (added, removed, updated) == (1, 1, 1), (added, removed, updated)

    assert m._normalize_date("2026-04-18 14:02:11") == "2026-04-18 14:02"
    assert m._normalize_date("2026-04-18T14:02:11") == "2026-04-18 14:02"
    assert m._normalize_date("2026-04-18 14:02:11.123456+00:00") == "2026-04-18 14:02"
    assert m._normalize_date("") == "(no date)"
    assert m._normalize_date("not a date") == "not a date"


# ---------------------------------------------------------------------------
# Pilot cases
# ---------------------------------------------------------------------------


async def _settle(pilot, ticks: int = 4) -> None:
    for _ in range(ticks):
        await pilot.pause()


async def case_runs_populated(m, app, pilot) -> None:
    from textual.widgets import DataTable
    table = app.query_one(DataTable)
    # 2 upgrade-run rows + divider + 1 manual = 4
    assert table.row_count == 4, f"expected 4 rows, got {table.row_count}"


async def case_status_summary(m, app, pilot) -> None:
    from textual.widgets import Static
    status = app.query_one("#status-line", Static)
    rendered = status.render()
    text = rendered.plain if hasattr(rendered, "plain") else str(rendered)
    assert "2 upgrade runs" in text, f"bad status line: {text!r}"
    assert "1 manual" in text, f"manual count missing: {text!r}"


async def case_diff_opens(m, app, pilot) -> None:
    from textual.widgets import DataTable
    from textual.screen import ModalScreen
    table = app.query_one(DataTable)
    table.move_cursor(row=0)
    await pilot.press("enter")
    await _settle(pilot, ticks=10)
    assert isinstance(app.screen, ModalScreen), "diff screen should have opened"
    await pilot.press("escape")
    await _settle(pilot)


async def case_rollback_cancel(m, app, pilot) -> None:
    from textual.widgets import DataTable, Static
    from textual.screen import ModalScreen
    table = app.query_one(DataTable)
    table.move_cursor(row=0)
    await pilot.press("r")
    await _settle(pilot)
    assert isinstance(app.screen, ModalScreen), "confirm should have opened"
    await pilot.press("n")
    await _settle(pilot)
    status = app.query_one("#status-line", Static)
    rendered = status.render()
    text = rendered.plain if hasattr(rendered, "plain") else str(rendered)
    assert "cancelled" in text.lower(), f"expected cancelled: {text!r}"


async def case_rollback_confirm_fires(m, app, pilot) -> None:
    from textual.widgets import DataTable
    table = app.query_one(DataTable)
    table.move_cursor(row=0)
    await pilot.press("r")
    await _settle(pilot)
    await pilot.press("y")
    # Confirm records the rollback target and exits the TUI; the
    # interactive rollback itself runs afterwards, outside the pilot.
    assert app._pending_rollback is not None, \
        f"confirm should record a rollback target, got {app._pending_rollback!r}"


async def case_rollback_on_manual_row(m, app, pilot) -> None:
    from textual.widgets import DataTable, Static
    table = app.query_one(DataTable)
    table.move_cursor(row=3)  # manual entry (row 2 is the divider)
    await pilot.press("r")
    await _settle(pilot)
    status = app.query_one("#status-line", Static)
    rendered = status.render()
    text = rendered.plain if hasattr(rendered, "plain") else str(rendered)
    assert "needs an upgrade run" in text, \
        f"expected warn on manual row: {text!r}"


async def case_help_modal(m, app, pilot) -> None:
    from textual.screen import ModalScreen
    await pilot.press("question_mark")
    await _settle(pilot)
    assert isinstance(app.screen, ModalScreen), "help should be on top"
    await pilot.press("escape")
    await _settle(pilot)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


CASES = [
    ("runs-populated",          case_runs_populated),
    ("status-summary",          case_status_summary),
    ("diff-opens",              case_diff_opens),
    ("rollback-cancel",         case_rollback_cancel),
    ("rollback-confirm-fires",  case_rollback_confirm_fires),
    ("rollback-on-manual-row",  case_rollback_on_manual_row),
    ("help-modal",              case_help_modal),
]


async def _run_case(m, App, fixtures, name, func) -> tuple[str, bool, str]:
    j, s, r = fixtures
    os.environ["SHEDOS_HISTORY_SNAPPER_LIST_CMD"] = f"cat {j}"
    os.environ["SHEDOS_HISTORY_SNAPPER_STATUS_CMD"] = f"cat {s}"
    os.environ["SHEDOS_HISTORY_ROLLBACK_CMD"] = f"cat {r}"
    app = App()
    try:
        async with app.run_test(size=(120, 32)) as pilot:
            await _settle(pilot, ticks=6)
            await func(m, app, pilot)
        return (name, True, "")
    except AssertionError as e:
        return (name, False, f"assertion: {e}")
    except Exception as e:
        return (name, False, f"{type(e).__name__}: {e}")


def main() -> int:
    if not TOOL_PATH.exists():
        print(f"FATAL: {TOOL_PATH} missing", file=sys.stderr)
        return 2
    m = _load_module()

    try:
        test_helpers(m)
    except AssertionError as e:
        print(f"FAIL helpers: {e}")
        return 1
    print("PASS helpers")

    App = m.build_app_class()

    with tempfile.TemporaryDirectory(prefix="shedos-uh-test.") as td:
        fixtures = _write_fixtures(Path(td))
        only = sys.argv[1:]
        cases = [c for c in CASES if not only or c[0] in only]
        if only and not cases:
            print(f"no matching cases: {only}", file=sys.stderr)
            return 2

        passed = failed = 0
        failures: list[tuple[str, str]] = []
        for name, func in cases:
            name, ok, why = asyncio.run(_run_case(m, App, fixtures, name, func))
            if ok:
                print(f"PASS {name}"); passed += 1
            else:
                print(f"FAIL {name}: {why}")
                failures.append((name, why))
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

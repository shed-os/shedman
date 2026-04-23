#!/usr/bin/env python3
"""pilot.py — Textual-pilot test suite for shedos-logs.

Each ``_case_*`` is an async function that takes the loaded shedos-logs
module and a freshly-built ``LogsApp`` pilot. Cases cover the paths that
matter in the field:

  - unit list populated from the ``SHEDOS_LOGS_UNITS_CMD`` hook
  - journal snapshot populated from ``SHEDOS_LOGS_JOURNAL_CMD``
  - filter-bar cycling (severity / time window)
  - regex validation (both valid and invalid)
  - help modal opens + closes
  - empty-journal fixture renders the placeholder line
  - malformed systemctl output doesn't crash the UI

The top-level runner also exercises the pure helpers (``parse_units``,
``FilterState``, ``validate_regex``, ``journal_stream_argv``) because
those are the seams test writers will hit first when a regression creeps
in; keeping them alongside the pilot cases means one `make` target covers
the whole tool.
"""

from __future__ import annotations

import asyncio
import importlib.machinery
import importlib.util
import os
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL_PATH = REPO_ROOT / "packaging/shedos-system/tree/usr/bin/shedos-logs"


def _load_module():
    loader = importlib.machinery.SourceFileLoader("shedos_logs", str(TOOL_PATH))
    spec = importlib.util.spec_from_loader("shedos_logs", loader)
    m = importlib.util.module_from_spec(spec)
    sys.modules["shedos_logs"] = m
    loader.exec_module(m)
    return m


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _make_fixtures(tmp: Path) -> tuple[Path, Path, Path]:
    units = tmp / "units.txt"
    _write(units,
           "alsa-restore.service loaded active exited Save/Restore Sound Card State\n"
           "bluetooth.service loaded active running Bluetooth service\n"
           "sshd.service loaded active running OpenSSH server\n")
    journal = tmp / "journal.txt"
    _write(journal,
           "2026-04-23T10:00:00.000000+0000 host bluetoothd[1]: Bluetooth daemon 5.66\n"
           "2026-04-23T10:00:01.000000+0000 host bluetoothd[1]: Starting SDP server\n"
           "2026-04-23T10:00:02.000000+0000 host bluetoothd[1]: Management 1.22\n")
    empty_journal = tmp / "empty.txt"
    _write(empty_journal, "")
    return units, journal, empty_journal


# ---------------------------------------------------------------------------
# Pure-helper checks — cheap and don't need Textual.
# ---------------------------------------------------------------------------


def test_helpers(m) -> None:
    sample = (
        "alsa-restore.service                                  loaded active     exited        Save/Restore Sound Card State\n"
        "bluetooth.service                                     loaded active     running       Bluetooth service\n"
        "●  failing.service                                    loaded failed     failed        Failing service\n"
    )
    units = m.parse_units(sample)
    assert len(units) == 3, f"parse_units: expected 3, got {len(units)}: {units}"
    assert units[2].name == "failing.service", units[2]

    assert m.parse_units("") == []
    assert m.parse_units("garbage\njust two tokens\n") == []

    fs = m.FilterState()
    assert fs.severity_label == "all"
    assert fs.severity_arg is None
    assert fs.window_label == "1h"
    assert fs.since_arg == "-1 hour ago"
    fs.cycle_severity(); assert fs.severity_label == "emerg"
    fs.cycle_window();  assert fs.window_label == "6h"

    assert m.validate_regex("^foo.*bar$") is None
    assert m.validate_regex("") is None
    assert m.validate_regex("*bad") is not None

    argv = m.journal_stream_argv("sshd.service", severity="0..3",
                                 since="-1 hour ago", grep="error")
    assert argv[:2] == ["journalctl", "-u"]
    assert "sshd.service" in argv and "-f" in argv
    assert "-p" in argv and "0..3" in argv
    assert "-g" in argv and "error" in argv

    argv = m.journal_stream_argv("sshd.service", severity=None,
                                 since=None, grep=None)
    assert "--boot" in argv and "-p" not in argv and "-g" not in argv


# ---------------------------------------------------------------------------
# Pilot cases — each takes ``app`` + ``pilot`` after on_mount settles.
# ---------------------------------------------------------------------------


async def _settle(pilot, ticks: int = 4) -> None:
    for _ in range(ticks):
        await pilot.pause()


async def case_units_loaded(m, app, pilot) -> None:
    from textual.widgets import DataTable
    table = app.query_one(DataTable)
    assert table.row_count == 3, f"expected 3 rows, got {table.row_count}"


async def case_filter_cycles(m, app, pilot) -> None:
    from textual.widgets import Static
    await pilot.press("f")
    await pilot.press("t")
    await _settle(pilot)
    bar = app.query_one("#filter-bar", Static)
    rendered = bar.render()
    text = rendered.plain if hasattr(rendered, "plain") else str(rendered)
    assert "emerg" in text, f"severity cycle missing: {text!r}"
    assert "6h" in text, f"window cycle missing: {text!r}"


async def case_journal_snapshot(m, app, pilot) -> None:
    from textual.widgets import DataTable, RichLog
    table = app.query_one(DataTable)
    table.move_cursor(row=1)
    await pilot.press("enter")
    await _settle(pilot, ticks=10)
    log = app.query_one(RichLog)
    assert len(log.lines) >= 3, f"expected ≥3 journal lines, got {len(log.lines)}"


async def case_help_modal(m, app, pilot) -> None:
    from textual.screen import ModalScreen
    await pilot.press("question_mark")
    await _settle(pilot)
    assert isinstance(app.screen, ModalScreen), "help modal should be on top"
    await pilot.press("escape")
    await _settle(pilot)
    assert not isinstance(app.screen, ModalScreen), "help should have closed"


async def case_empty_journal_placeholder(m, app, pilot) -> None:
    # Swap the journal hook to the empty fixture mid-flight, then reload.
    from textual.widgets import DataTable, RichLog
    os.environ["SHEDOS_LOGS_JOURNAL_CMD"] = f"cat {app._test_empty_journal}"
    table = app.query_one(DataTable)
    table.move_cursor(row=2)  # sshd.service
    await pilot.press("enter")
    await _settle(pilot, ticks=10)
    log = app.query_one(RichLog)
    rendered_lines = [line.text for line in log.lines]
    assert any("no log entries" in t for t in rendered_lines), \
        f"expected placeholder in: {rendered_lines!r}"


async def case_regex_invalid(m, app, pilot) -> None:
    from textual.widgets import Static, Input
    # Open prompt, type invalid regex, press enter. The Input lives on the
    # modal screen — query via app.screen (the modal is the top screen
    # while it's open).
    await pilot.press("slash")
    await _settle(pilot)
    app.screen.query_one(Input).value = "*bad"
    await pilot.press("enter")
    await _settle(pilot)
    # Status line should flag the invalid pattern; filters.regex unchanged.
    assert app.filters.regex is None, \
        f"invalid regex should not have been saved; got {app.filters.regex!r}"
    status = app.query_one("#status-line", Static)
    rendered = status.render()
    text = rendered.plain if hasattr(rendered, "plain") else str(rendered)
    assert "invalid" in text.lower(), f"expected invalid-regex warning: {text!r}"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


CASES = [
    ("units-loaded",              case_units_loaded),
    ("filter-cycles",             case_filter_cycles),
    ("journal-snapshot",          case_journal_snapshot),
    ("help-modal",                case_help_modal),
    ("empty-journal-placeholder", case_empty_journal_placeholder),
    ("regex-invalid",             case_regex_invalid),
]


async def _run_case(m, LogsApp, fixtures, name, func) -> tuple[str, bool, str]:
    units, journal, empty_journal = fixtures
    os.environ["SHEDOS_LOGS_UNITS_CMD"] = f"cat {units}"
    os.environ["SHEDOS_LOGS_JOURNAL_CMD"] = f"cat {journal}"
    app = LogsApp()
    app._test_empty_journal = empty_journal  # stash for the one case that swaps
    try:
        async with app.run_test(size=(120, 32)) as pilot:
            await _settle(pilot)
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

    LogsApp = m.build_app_class()

    with tempfile.TemporaryDirectory(prefix="shedos-logs-test.") as td:
        fixtures = _make_fixtures(Path(td))

        only = sys.argv[1:]
        cases = [c for c in CASES if not only or c[0] in only]
        if only and not cases:
            print(f"no matching cases: {only}", file=sys.stderr)
            return 2

        passed = failed = 0
        failures: list[tuple[str, str]] = []
        for name, func in cases:
            result = asyncio.run(_run_case(m, LogsApp, fixtures, name, func))
            _, ok, why = result
            if ok:
                print(f"PASS {name}")
                passed += 1
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

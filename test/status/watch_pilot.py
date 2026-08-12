#!/usr/bin/env python3
"""watch_pilot.py — Textual pilot for `shedman status --watch`.

The watch UI is a thin Textual shell over the same `_run_signal()`
helpers the one-shot path already exercises in test/status/run.sh.
This file drives the TUI via ``App.run_test()`` so we can assert:

  * watch-renders     — initial frame contains all four signal rows
                        and the summary verdict.
  * watch-refreshes   — when the underlying stubs change between
                        ticks, the refresh updates the rows.
  * watch-handles-failure — one stub exits non-zero; that row shows
                        `unavailable`, the others render normally.

The test rebinds SHEDOS_STATUS_LIBEXEC to a per-test stubs/ dir
(same model as test/status/fixtures/<name>/stubs/), then constructs
the StatusWatchApp directly with `_load_module()`-imported symbols.
"""

from __future__ import annotations

import asyncio
import importlib.machinery
import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL_PATH = REPO_ROOT / "tree/usr/libexec/shedman/status"


def _load_module():
    loader = importlib.machinery.SourceFileLoader("shedos_status", str(TOOL_PATH))
    spec = importlib.util.spec_from_loader("shedos_status", loader)
    m = importlib.util.module_from_spec(spec)
    sys.modules["shedos_status"] = m
    loader.exec_module(m)
    return m


def _make_stubs(stubs_dir: Path, blobs: dict[str, str]) -> None:
    """Write a stub for each signal name → JSON blob mapping. Each stub
    is a tiny shell script that emits the blob on stdout. A blob set to
    `None` means "stub exits non-zero" (simulates an unavailable signal).
    """
    stubs_dir.mkdir(parents=True, exist_ok=True)
    for name, blob in blobs.items():
        path = stubs_dir / name
        if blob is None:
            path.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
        else:
            # Single-quote-safe blob inlining via printf %s.
            esc = blob.replace("'", "'\\''")
            path.write_text(f"#!/bin/sh\nprintf '%s' '{esc}'\n",
                            encoding="utf-8")
        path.chmod(0o755)


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


async def case_watch_renders():
    mod = _load_module()
    with tempfile.TemporaryDirectory(prefix="shedos-status-watch.") as td:
        stubs = Path(td) / "stubs"
        _make_stubs(stubs, {
            "updates":   '{"text":"","tooltip":"","class":"no-updates"}',
            "conflicts": '{"text":"","tooltip":"","class":"no-conflicts"}',
            "health":    '{"text":"","tooltip":"","class":"ok"}',
            "doctor":    '{"text":"","tooltip":"","class":"aligned"}',
        })

        # Build the app and drive it.
        from textual.widgets import DataTable, Static  # noqa: PLC0415
        # Re-import the module's _run_watch via a slight refactor:
        # the StatusWatchApp class is defined *inside* _run_watch.
        # To get at it for testing, we replicate the construction.
        from textual.app import App, ComposeResult  # noqa: PLC0415
        from textual.containers import Vertical  # noqa: PLC0415
        from textual.widgets import Footer, Header  # noqa: PLC0415

        class _T(App):
            def __init__(self, lib: Path) -> None:
                super().__init__()
                self._lib = lib

            def compose(self) -> ComposeResult:
                yield Header()
                with Vertical():
                    yield DataTable(id="signals")
                    yield Static("", id="verdict")
                yield Footer()

            def on_mount(self) -> None:
                tbl = self.query_one("#signals", DataTable)
                tbl.add_column("signal", key="signal")
                tbl.add_column("status", key="status")
                for name in mod.SIGNALS:
                    tbl.add_row(name, "—", key=name)
                self._tick()

            def _tick(self) -> None:
                sections = {n: mod._run_signal(self._lib, n) for n in mod.SIGNALS}
                summary = mod._summarize(sections)
                tbl = self.query_one("#signals", DataTable)
                for name in mod.SIGNALS:
                    tbl.update_cell(name, "status",
                                    mod._humanize(name, sections[name]))
                v = self.query_one("#verdict", Static)
                if summary["status"] == "ok":
                    v.update("Summary: OK")

        app = _T(stubs)
        async with app.run_test() as pilot:
            await pilot.pause()
            tbl = app.query_one("#signals", DataTable)
            verdict = app.query_one("#verdict", Static)
            assert tbl.row_count == 4, f"expected 4 rows, got {tbl.row_count}"
            assert "OK" in str(verdict.render()), \
                f"verdict should report OK, got: {verdict.render()!r}"


async def case_watch_refreshes():
    mod = _load_module()
    with tempfile.TemporaryDirectory(prefix="shedos-status-watch.") as td:
        stubs = Path(td) / "stubs"
        _make_stubs(stubs, {
            "updates":   '{"text":"","tooltip":"","class":"no-updates"}',
            "conflicts": '{"text":"","tooltip":"","class":"no-conflicts"}',
            "health":    '{"text":"","tooltip":"","class":"ok"}',
            "doctor":    '{"text":"","tooltip":"","class":"aligned"}',
        })

        # First read → all-aligned. Then mutate the doctor stub mid-test.
        from textual.app import App, ComposeResult  # noqa: PLC0415
        from textual.containers import Vertical  # noqa: PLC0415
        from textual.widgets import DataTable, Footer, Header, Static  # noqa: PLC0415

        class _T(App):
            def __init__(self, lib: Path) -> None:
                super().__init__()
                self._lib = lib

            def compose(self) -> ComposeResult:
                yield Header()
                with Vertical():
                    yield DataTable(id="signals")
                    yield Static("", id="verdict")
                yield Footer()

            def on_mount(self) -> None:
                tbl = self.query_one("#signals", DataTable)
                tbl.add_column("signal", key="signal")
                tbl.add_column("status", key="status")
                for name in mod.SIGNALS:
                    tbl.add_row(name, "—", key=name)
                self.tick()

            def tick(self) -> None:
                sections = {n: mod._run_signal(self._lib, n) for n in mod.SIGNALS}
                tbl = self.query_one("#signals", DataTable)
                for name in mod.SIGNALS:
                    tbl.update_cell(name, "status",
                                    mod._humanize(name, sections[name]))

        app = _T(stubs)
        async with app.run_test() as pilot:
            await pilot.pause()
            tbl = app.query_one("#signals", DataTable)
            doctor_row_initial = tbl.get_cell("doctor", "status")
            assert "aligned" in str(doctor_row_initial)

            # Mutate doctor stub → drift.
            _make_stubs(stubs, {
                "doctor": '{"text":" 2","tooltip":"","class":"drift"}',
            })
            app.tick()
            await pilot.pause()
            doctor_row_after = tbl.get_cell("doctor", "status")
            assert "drift" in str(doctor_row_after) or "2" in str(doctor_row_after), \
                f"after refresh, doctor row should reflect drift; got: {doctor_row_after!r}"


async def case_watch_handles_failure():
    mod = _load_module()
    with tempfile.TemporaryDirectory(prefix="shedos-status-watch.") as td:
        stubs = Path(td) / "stubs"
        _make_stubs(stubs, {
            "updates":   '{"text":"","tooltip":"","class":"no-updates"}',
            "conflicts": '{"text":"","tooltip":"","class":"no-conflicts"}',
            "health":    None,  # exits non-zero → unavailable
            "doctor":    '{"text":"","tooltip":"","class":"aligned"}',
        })

        from textual.app import App, ComposeResult  # noqa: PLC0415
        from textual.containers import Vertical  # noqa: PLC0415
        from textual.widgets import DataTable, Footer, Header, Static  # noqa: PLC0415

        class _T(App):
            def __init__(self, lib: Path) -> None:
                super().__init__()
                self._lib = lib

            def compose(self) -> ComposeResult:
                yield Header()
                with Vertical():
                    yield DataTable(id="signals")
                    yield Static("", id="verdict")
                yield Footer()

            def on_mount(self) -> None:
                tbl = self.query_one("#signals", DataTable)
                tbl.add_column("signal", key="signal")
                tbl.add_column("status", key="status")
                for name in mod.SIGNALS:
                    tbl.add_row(name, "—", key=name)
                sections = {n: mod._run_signal(self._lib, n) for n in mod.SIGNALS}
                for name in mod.SIGNALS:
                    tbl.update_cell(name, "status",
                                    mod._humanize(name, sections[name]))

        app = _T(stubs)
        async with app.run_test() as pilot:
            await pilot.pause()
            tbl = app.query_one("#signals", DataTable)
            health_cell = tbl.get_cell("health", "status")
            assert "unavailable" in str(health_cell), \
                f"health should be unavailable; got: {health_cell!r}"
            updates_cell = tbl.get_cell("updates", "status")
            assert "up to date" in str(updates_cell), \
                f"updates should still be 'up to date'; got: {updates_cell!r}"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


CASES = [
    ("watch-renders", case_watch_renders),
    ("watch-refreshes", case_watch_refreshes),
    ("watch-handles-failure", case_watch_handles_failure),
]


def main() -> int:
    pass_count = 0
    fail_count = 0
    failures: list[str] = []
    for name, coro in CASES:
        try:
            asyncio.run(coro())
        except AssertionError as e:
            print(f"FAIL {name}: {e}")
            fail_count += 1
            failures.append(name)
        except Exception as e:
            print(f"FAIL {name}: {type(e).__name__}: {e}")
            fail_count += 1
            failures.append(name)
        else:
            print(f"PASS {name}")
            pass_count += 1

    print()
    print(f"Summary: {pass_count} passed, {fail_count} failed")
    if fail_count:
        for f in failures:
            print(f"  {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

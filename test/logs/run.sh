#!/usr/bin/env bash
# run.sh — test harness for shedos-logs.
#
# The tool is a Textual TUI, so the real assertions live in pilot.py where
# we can drive the app via ``App.run_test()``. This wrapper only exists so
# the Makefile target looks like the other test suites (test-sync-configs,
# test-check-health, etc).

set -uo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$here/../.." && pwd)
tool=$repo_root/tree/usr/libexec/shedman/logs

if [[ ! -x $tool ]]; then
    echo "FATAL: $tool not executable" >&2
    exit 2
fi

if ! python3 -c "import textual" >/dev/null 2>&1; then
    echo "FATAL: python-textual is required (pilot tests use App.run_test)" >&2
    exit 2
fi

exec python3 "$here/pilot.py" "$@"

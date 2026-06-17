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
tool=$repo_root/packaging/shedos-system/tree/usr/libexec/shedman/logs
# The TUI imports the shared shedos_palette module from SHEDOS_LIB_ROOT
# (default /usr/lib/shedos, the installed path); point it at the tree.
export SHEDOS_LIB_ROOT="$repo_root/packaging/shedos-system/tree/usr/lib/shedos"

if [[ ! -x $tool ]]; then
    echo "FATAL: $tool not executable" >&2
    exit 2
fi

if ! python3 -c "import textual" >/dev/null 2>&1; then
    echo "FATAL: python-textual is required (pilot tests use App.run_test)" >&2
    exit 2
fi

exec python3 "$here/pilot.py" "$@"

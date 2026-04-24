#!/usr/bin/env bash
# run.sh — test harness for shedos-upgrade-history.
#
# Same shape as test/logs/run.sh: thin bash wrapper whose only job is to
# hand off to a Python pilot. Snapper is mocked via the env-var hooks the
# tool ships with, so the harness does not need a real snapper config.

set -uo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$here/../.." && pwd)
tool=$repo_root/packaging/shedos-system/tree/usr/libexec/shedman/upgrade-history

if [[ ! -x $tool ]]; then
    echo "FATAL: $tool not executable" >&2
    exit 2
fi

if ! python3 -c "import textual" >/dev/null 2>&1; then
    echo "FATAL: python-textual is required (pilot tests use App.run_test)" >&2
    exit 2
fi

exec python3 "$here/pilot.py" "$@"

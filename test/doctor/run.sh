#!/usr/bin/env bash
# run.sh — test harness for shedos-doctor.
#
# Thin wrapper around a Python pilot. shedos-doctor is CLI-only (no TUI),
# but it emits JSON in several modes and it matters that exit codes and
# JSON shapes line up — asserting those in pure bash gets messy, so we
# lean on Python instead. Same spirit as the sync-configs fixture runner,
# different surface.

set -uo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$here/../.." && pwd)
tool=$repo_root/packaging/shedos-system/tree/usr/bin/shedos-doctor

if [[ ! -x $tool ]]; then
    echo "FATAL: $tool not executable" >&2
    exit 2
fi

exec python3 "$here/pilot.py" "$@"

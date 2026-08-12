#!/usr/bin/env bash
# run.sh — test harness for `shedman status`.
#
# Each fixture under fixtures/<name>/ models one dashboard scenario:
#
#   stubs/           directory of stub binaries for updates/conflicts/health/
#                    doctor. The harness points SHEDOS_STATUS_LIBEXEC at
#                    this dir so the tool exec's the stubs in place of the
#                    real subcommands. Omit a stub to simulate that signal
#                    being unavailable (the tool sees "binary missing").
#   expected-text    expected stdout for `shedman status` (plain mode).
#   expected-json    (optional) expected stdout for `shedman status --json`.
#
# Usage: test/status/run.sh [fixture-name ...]
#        (no args = run every fixture)
#
# Exit: 0 all pass, 1 any failure.

set -uo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$here/../.." && pwd)
tool=$repo_root/tree/usr/libexec/shedman/status
# The --watch TUI imports the shared shedos_palette module from
# SHEDOS_LIB_ROOT (default /usr/lib/shedos, the installed path); point it
# at the tree.
export SHEDOS_LIB_ROOT="$repo_root/tree/usr/lib/shedos"

if [[ ! -x $tool ]]; then
    echo "FATAL: $tool not executable" >&2
    exit 2
fi

if (( $# > 0 )); then
    fixtures=("$@")
else
    fixtures=()
    while IFS= read -r -d '' d; do
        fixtures+=("$(basename "$d")")
    done < <(find "$here/fixtures" -mindepth 1 -maxdepth 1 -type d -print0 | sort -z)
fi

pass=0
fail=0
failures=()

_diff_blob() {
    local label=$1 expected=$2 actual=$3
    if ! diff -u "$expected" "$actual" >/dev/null 2>&1; then
        echo "  $label mismatch:"
        diff -u "$expected" "$actual" | sed 's/^/    /' | head -60
        return 1
    fi
    return 0
}

_run_one() {
    local name=$1
    local fdir=$here/fixtures/$name
    [[ -d $fdir ]] || { echo "skip $name (no such fixture)"; return; }
    [[ -d $fdir/stubs ]] || { echo "skip $name (missing stubs/)"; return; }
    [[ -f $fdir/expected-text ]] || {
        echo "skip $name (missing expected-text)"; return
    }

    local tmp text_out json_out rc bad=0
    tmp=$(mktemp -d -t shedos-status-test.XXXXXX)
    trap 'rm -rf -- "$tmp"' RETURN

    text_out=$tmp/text
    if ! SHEDOS_STATUS_LIBEXEC=$fdir/stubs "$tool" >"$text_out" 2>&1; then
        rc=$?
        echo "FAIL $name (text): exit $rc"
        sed 's/^/    /' "$text_out"
        failures+=("$name")
        ((fail++))
        return
    fi
    _diff_blob "text" "$fdir/expected-text" "$text_out" || bad=1

    if [[ -f $fdir/expected-json ]]; then
        json_out=$tmp/json
        if ! SHEDOS_STATUS_LIBEXEC=$fdir/stubs "$tool" --json >"$json_out" 2>&1; then
            rc=$?
            echo "FAIL $name (json): exit $rc"
            sed 's/^/    /' "$json_out"
            failures+=("$name")
            ((fail++))
            return
        fi
        _diff_blob "json" "$fdir/expected-json" "$json_out" || bad=1
    fi

    if (( bad )); then
        echo "FAIL $name"
        failures+=("$name")
        ((fail++))
        return
    fi
    echo "PASS $name"
    ((pass++))
}

for f in "${fixtures[@]}"; do
    _run_one "$f"
done

# Watch-mode pilot tests, only when no specific fixture was requested
# (so `bash run.sh all-ok` doesn't accidentally run unrelated TUI cases).
if (( $# == 0 )); then
    if python3 "$here/watch_pilot.py"; then
        :
    else
        fail=$((fail + 1))
        failures+=("watch_pilot")
    fi
fi

echo
echo "Summary: $pass passed, $fail failed"
if (( fail > 0 )); then
    printf '  %s\n' "${failures[@]}"
    exit 1
fi
exit 0

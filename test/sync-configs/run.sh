#!/usr/bin/env bash
# run.sh — test harness for shedos-sync-configs.
#
# Each fixture under fixtures/<name>/ models one end-to-end sync scenario:
#
#   fixture.sh       (optional) shell vars. Supported:
#                       SYNC_ARGS    extra args for the tool (default: --yes)
#                       EXIT_CODE    expected exit code (default: 0)
#   defaults/        tree mirroring $SHEDOS_DEFAULTS_ROOT. Typical layout:
#                       defaults/<pkg>/defaults/<relpath>
#   initial-home/    tree copied into $HOME before the run (optional)
#   initial-state/   tree copied into $XDG_STATE_HOME/shedos before the run.
#                    Use it to pre-seed last-seen/<relpath>.sha256 and
#                    last-seen-content/<relpath> (optional)
#   sync-exclude     contents for $HOME/.config/shedos/sync-exclude (optional)
#   expected-home/   expected $HOME tree after the run (required)
#   expected-state/  expected $XDG_STATE_HOME/shedos tree (optional check)
#
# Usage: test/sync-configs/run.sh [fixture-name ...]
#        (no args = run every fixture)
#
# Exit: 0 all pass, 1 any failure.

set -uo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$here/../.." && pwd)
tool=$repo_root/packaging/shedos-system/tree/usr/bin/shedos-sync-configs

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

# Recursively diff two trees, emitting a human-readable report. Missing tree
# on either side is a failure unless both are missing.
_tree_diff() {
    local label=$1 expected=$2 actual=$3
    if [[ ! -d $expected && ! -d $actual ]]; then
        return 0
    fi
    if [[ ! -d $expected ]]; then
        echo "  $label: expected tree missing from fixture ($expected)"
        return 1
    fi
    if [[ ! -d $actual ]]; then
        echo "  $label: actual tree missing ($actual)"
        return 1
    fi
    # diff -rq reports only differing entries. --no-dereference keeps symlinks
    # honest. Filter empty-line noise.
    local report
    report=$(diff -rq "$expected" "$actual" 2>&1 || true)
    if [[ -n $report ]]; then
        echo "  $label mismatch:"
        printf '    %s\n' $report
        return 1
    fi
    return 0
}

_run_one() {
    local name=$1
    local fdir=$here/fixtures/$name
    [[ -d $fdir ]] || { echo "skip $name (no such fixture)"; return; }
    [[ -d $fdir/defaults ]] || { echo "skip $name (missing defaults/)"; return; }
    [[ -d $fdir/expected-home ]] || { echo "skip $name (missing expected-home/)"; return; }

    local SYNC_ARGS="--yes"
    local EXIT_CODE=0
    if [[ -f $fdir/fixture.sh ]]; then
        # shellcheck disable=SC1091
        source "$fdir/fixture.sh"
    fi

    local tmp
    tmp=$(mktemp -d -t shedos-sync-test.XXXXXX)
    trap 'rm -rf -- "$tmp"' RETURN

    local home=$tmp/home
    local state_root=$tmp/state
    mkdir -p "$home" "$state_root/shedos"

    if [[ -d $fdir/initial-home ]]; then
        cp -a "$fdir/initial-home/." "$home/"
    fi
    if [[ -d $fdir/initial-state ]]; then
        cp -a "$fdir/initial-state/." "$state_root/shedos/"
    fi
    if [[ -f $fdir/sync-exclude ]]; then
        install -Dm644 "$fdir/sync-exclude" "$home/.config/shedos/sync-exclude"
    fi

    # Run the tool with the synthetic defaults root. shedos-check-conflicts is
    # invoked at the tail of the script — it's not on PATH in the test env,
    # so stub with a no-op via PATH prepending.
    local stubdir=$tmp/stubs
    mkdir -p "$stubdir"
    printf '#!/bin/sh\nexit 0\n' > "$stubdir/shedos-check-conflicts"
    chmod +x "$stubdir/shedos-check-conflicts"

    local rc out
    # shellcheck disable=SC2086  # intentional word-splitting of SYNC_ARGS
    out=$(
        HOME=$home \
        XDG_CONFIG_HOME=$home/.config \
        XDG_STATE_HOME=$state_root \
        SHEDOS_DEFAULTS_ROOT=$fdir/defaults \
        PATH="$stubdir:$PATH" \
        "$tool" $SYNC_ARGS 2>&1
    )
    rc=$?

    if (( rc != EXIT_CODE )); then
        echo "FAIL $name: exit $rc (expected $EXIT_CODE)"
        echo "  output:"
        printf '    %s\n' "${out//$'\n'/$'\n    '}"
        failures+=("$name")
        ((fail++))
        return
    fi

    local bad=0
    _tree_diff "HOME"  "$fdir/expected-home"  "$home"                  || bad=1
    if [[ -d $fdir/expected-state ]]; then
        _tree_diff "STATE" "$fdir/expected-state" "$state_root/shedos" || bad=1
    fi

    if (( bad )); then
        echo "FAIL $name"
        echo "  tool output:"
        printf '    %s\n' "${out//$'\n'/$'\n    '}"
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

echo
echo "Summary: $pass passed, $fail failed"
if (( fail > 0 )); then
    printf '  %s\n' "${failures[@]}"
    exit 1
fi
exit 0

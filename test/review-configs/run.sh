#!/usr/bin/env bash
# run.sh — test harness for shedos-review-configs.
#
# Each fixture under fixtures/<name>/ holds a snapshot of a merge scenario:
#
#   fixture.sh      shell vars: PKG=<pkg-name>  RELPATH=<path under $HOME>
#   src             pristine default shipped by the package
#   base            last-seen BASE snapshot (optional — omit for 2-way fixtures)
#   yours           the user's live $HOME copy
#   theirs          the upstream .shedosnew content
#   decisions       token stream for --stdin-decisions (one per line)
#   expected        expected merged bytes AFTER 's' token is consumed
#
# The harness builds a disposable $HOME + state tree, lays the files in the
# paths the real tool expects, runs shedos-review-configs with
# --stdin-decisions, and compares the resulting live file byte-for-byte with
# `expected`.
#
# Usage: test/review-configs/run.sh [fixture-name ...]
#        (no args = run every fixture)
#
# Exit: 0 all pass, 1 any failure.

set -uo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$here/../.." && pwd)
tool=$repo_root/packaging/shedos-system/tree/usr/bin/shedos-review-configs

if [[ ! -x $tool ]]; then
    echo "FATAL: $tool not executable" >&2
    exit 2
fi

# Which fixtures? Args, or all if none.
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

_run_one() {
    local name=$1
    local fdir=$here/fixtures/$name
    [[ -d $fdir ]] || { echo "skip $name (no such fixture)"; return; }
    [[ -f $fdir/fixture.sh ]] || { echo "skip $name (no fixture.sh)"; return; }
    [[ -f $fdir/src && -f $fdir/yours && -f $fdir/theirs ]] || {
        echo "skip $name (missing src/yours/theirs)"; return
    }
    [[ -f $fdir/decisions && -f $fdir/expected ]] || {
        echo "skip $name (missing decisions/expected)"; return
    }

    # shellcheck disable=SC1091
    local PKG="" RELPATH=""
    source "$fdir/fixture.sh"
    if [[ -z $PKG || -z $RELPATH ]]; then
        echo "FAIL $name: fixture.sh must set PKG and RELPATH"
        failures+=("$name")
        ((fail++))
        return
    fi

    local tmp
    tmp=$(mktemp -d -t shedos-review-test.XXXXXX)
    trap 'rm -rf -- "$tmp"' RETURN

    local home=$tmp/home
    local state=$tmp/state
    local defaults=$tmp/defaults
    mkdir -p "$home" "$state" "$defaults"

    # Live file + .shedosnew neighbor.
    install -Dm644 "$fdir/yours" "$home/$RELPATH"
    install -Dm644 "$fdir/theirs" "$home/$RELPATH.shedosnew"

    # Pacman-shipped src (pristine default).
    install -Dm644 "$fdir/src" "$defaults/$PKG/defaults/$RELPATH"

    # BASE manifest if this is a 3-way fixture.
    if [[ -f $fdir/base ]]; then
        local base_sha
        base_sha=$(sha256sum "$fdir/base" | awk '{print $1}')
        install -Dm600 /dev/null "$state/shedos/last-seen/$RELPATH.sha256"
        printf '%s' "$base_sha" > "$state/shedos/last-seen/$RELPATH.sha256"
        install -Dm600 "$fdir/base" "$state/shedos/last-seen-content/$RELPATH"
    fi

    # Run the tool.
    local out
    if ! out=$(
        HOME=$home \
        XDG_STATE_HOME=$state \
        SHEDOS_DEFAULTS_ROOT=$defaults \
        "$tool" --stdin-decisions < "$fdir/decisions" 2>&1
    ); then
        echo "FAIL $name: tool exited non-zero"
        echo "  output: $out"
        failures+=("$name")
        ((fail++))
        return
    fi

    # Compare live file with expected.
    if ! cmp -s "$home/$RELPATH" "$fdir/expected"; then
        echo "FAIL $name: merged content does not match expected"
        diff -u "$fdir/expected" "$home/$RELPATH" | sed 's/^/    /' | head -40
        failures+=("$name")
        ((fail++))
        return
    fi

    # .shedosnew must have been removed on successful save.
    if [[ -e $home/$RELPATH.shedosnew ]]; then
        echo "FAIL $name: .shedosnew still present after save"
        failures+=("$name")
        ((fail++))
        return
    fi

    # .shedosbak must hold the pre-merge YOURS.
    if [[ ! -f $home/$RELPATH.shedosbak ]] || \
       ! cmp -s "$home/$RELPATH.shedosbak" "$fdir/yours"; then
        echo "FAIL $name: .shedosbak missing or not equal to pre-merge YOURS"
        failures+=("$name")
        ((fail++))
        return
    fi

    # Manifest must have been advanced: sha matches sha(src), BASE content
    # file matches src byte-for-byte.
    local src_sha stored_sha
    src_sha=$(sha256sum "$fdir/src" | awk '{print $1}')
    stored_sha=$(tr -d '[:space:]' < "$state/shedos/last-seen/$RELPATH.sha256" 2>/dev/null || echo "")
    if [[ $stored_sha != "$src_sha" ]]; then
        echo "FAIL $name: manifest sha not advanced to sha(src)"
        echo "  expected: $src_sha"
        echo "  got:      $stored_sha"
        failures+=("$name")
        ((fail++))
        return
    fi
    if ! cmp -s "$state/shedos/last-seen-content/$RELPATH" "$fdir/src"; then
        echo "FAIL $name: BASE content not advanced to src"
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

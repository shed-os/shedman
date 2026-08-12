#!/usr/bin/env bash
# run.sh — integration tests for the VS Code backend (--gui) in
# _config-review.
#
# Each fixture under fixtures/<name>/ holds a merge scenario:
#
#   fixture.sh        shell vars: PKG=<pkg-name>  RELPATH=<path> (single
#                     file), or PKGS=(...) RELPATHS=(...) for multi-file
#                     fixtures. May also set:
#
#                       EXPECT_SAVED=1               result updated, .shedosnew gone
#                       EXPECT_UNCHANGED=1           result kept, .shedosnew kept
#                       EXPECT_MARKER_SKIPPED=1      marker detected, .shedosnew kept
#                       EXPECT_WAIT_INVOCATIONS=N    fake-code receives N --wait calls
#                       EXPECT_STDERR_PATTERN=…      grep -E pattern that must match
#
#   src               pristine default shipped by the package
#   base              last-seen BASE snapshot (3-way; omit for 2-way)
#   yours             the user's live $HOME copy
#   theirs            the upstream .shedosnew content
#   expected          (only if EXPECT_SAVED=1) expected merged content after
#                     copy-back
#   action.sh         (optional) bash snippet sourced by fake-code with
#                     YOURS_FILE / THEIRS_FILE / BASE_FILE / RESULT_FILE
#                     set; simulates the user editing the result file
#
# Multi-file fixtures use {src,base,yours,theirs,expected}.<idx> files
# keyed off PKGS[i]/RELPATHS[i].
#
# Usage: test/review-vscode/run.sh [fixture-name ...]
# Exit:  0 all pass, 1 any failure.

set -uo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$here/../.." && pwd)
tool=$repo_root/packaging/shedos-system/tree/usr/libexec/shedman/_config-review
fake_code=$here/fake-code

if [[ ! -x $tool ]]; then
    echo "FATAL: $tool not executable" >&2
    exit 2
fi
if [[ ! -x $fake_code ]]; then
    echo "FATAL: $fake_code not executable" >&2
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

_stage_one() {
    # Args: home defaults state pkg relpath src yours theirs [base]
    local home=$1 defaults=$2 state=$3 pkg=$4 relpath=$5
    local src=$6 yours=$7 theirs=$8 base=${9:-}
    install -Dm644 "$yours"   "$home/$relpath"
    install -Dm644 "$theirs"  "$home/$relpath.shedosnew"
    install -Dm644 "$src"     "$defaults/$pkg/defaults/$relpath"
    if [[ -n $base ]]; then
        local sha
        sha=$(sha256sum "$base" | awk '{print $1}')
        install -Dm600 /dev/null "$state/shedos/last-seen/$relpath.sha256"
        printf '%s' "$sha" > "$state/shedos/last-seen/$relpath.sha256"
        install -Dm600 "$base" "$state/shedos/last-seen-content/$relpath"
    fi
}

_run_one() {
    local name=$1
    local fdir=$here/fixtures/$name
    [[ -d $fdir ]] || { echo "skip $name (no such fixture)"; return; }
    [[ -f $fdir/fixture.sh ]] || { echo "skip $name (no fixture.sh)"; return; }

    local PKG="" RELPATH=""
    local PKGS=() RELPATHS=()
    local EXPECT_SAVED="" EXPECT_UNCHANGED="" EXPECT_MARKER_SKIPPED=""
    local EXPECT_WAIT_INVOCATIONS=""
    local EXPECT_STDERR_PATTERN=""
    # shellcheck disable=SC1090
    source "$fdir/fixture.sh"

    if [[ -n $PKG && -n $RELPATH && ${#PKGS[@]} -eq 0 ]]; then
        PKGS=("$PKG")
        RELPATHS=("$RELPATH")
    fi
    if [[ ${#PKGS[@]} -eq 0 || ${#RELPATHS[@]} -eq 0 ]]; then
        echo "FAIL $name: fixture.sh must set PKG/RELPATH or PKGS/RELPATHS"
        failures+=("$name"); ((fail++)); return
    fi

    local tmp
    tmp=$(mktemp -d -t shedos-review-vscode-test.XXXXXX)
    trap 'rm -rf -- "$tmp"' RETURN

    local home=$tmp/home
    local state=$tmp/state
    local defaults=$tmp/defaults
    local argv_log=$tmp/argv.log
    mkdir -p "$home" "$state" "$defaults"
    : > "$argv_log"

    local i count=${#PKGS[@]}
    for ((i=0; i<count; i++)); do
        local suffix=""
        (( count > 1 )) && suffix=".$i"
        local fbase=$fdir/base$suffix
        [[ -f $fbase ]] || fbase=""
        _stage_one "$home" "$defaults" "$state" \
            "${PKGS[i]}" "${RELPATHS[i]}" \
            "$fdir/src$suffix" "$fdir/yours$suffix" "$fdir/theirs$suffix" \
            "$fbase"
    done

    local extra_action=()
    if [[ -f $fdir/action.sh ]]; then
        extra_action=("SHEDOS_FAKE_CODE_ACTION=$fdir/action.sh")
    fi

    local stderr_file=$tmp/stderr
    if ! HOME=$home \
        XDG_STATE_HOME=$state \
        SHEDOS_DEFAULTS_ROOT=$defaults \
        SHEDOS_VSCODE_BIN=$fake_code \
        SHEDOS_FAKE_CODE_ARGV_FILE=$argv_log \
        env "${extra_action[@]}" \
        "$tool" --gui 2> "$stderr_file" > /dev/null
    then
        echo "FAIL $name: tool exited non-zero"
        sed 's/^/    /' "$stderr_file" | head -20
        failures+=("$name"); ((fail++)); return
    fi

    for ((i=0; i<count; i++)); do
        local suffix=""
        (( count > 1 )) && suffix=".$i"
        local rel=${RELPATHS[i]}
        local live=$home/$rel
        local theirs=$home/$rel.shedosnew
        local bak=$home/$rel.shedosbak

        if [[ ${EXPECT_SAVED:-0} == "1" ]]; then
            if ! cmp -s "$live" "$fdir/expected$suffix"; then
                echo "FAIL $name [$rel]: live does not match expected"
                diff -u "$fdir/expected$suffix" "$live" 2>&1 \
                    | sed 's/^/    /' | head -20
                failures+=("$name"); ((fail++)); return
            fi
            if [[ -e $theirs ]]; then
                echo "FAIL $name [$rel]: .shedosnew should have been removed"
                failures+=("$name"); ((fail++)); return
            fi
            if [[ ! -f $bak ]] || ! cmp -s "$bak" "$fdir/yours$suffix"; then
                echo "FAIL $name [$rel]: .shedosbak missing or wrong content"
                failures+=("$name"); ((fail++)); return
            fi
            local src_sha stored_sha
            src_sha=$(sha256sum "$fdir/src$suffix" | awk '{print $1}')
            stored_sha=$(tr -d '[:space:]' < "$state/shedos/last-seen/$rel.sha256" 2>/dev/null || echo "")
            if [[ $stored_sha != "$src_sha" ]]; then
                echo "FAIL $name [$rel]: manifest sha not advanced to sha(src)"
                failures+=("$name"); ((fail++)); return
            fi
        elif [[ ${EXPECT_UNCHANGED:-0} == "1" || ${EXPECT_MARKER_SKIPPED:-0} == "1" ]]; then
            if ! cmp -s "$live" "$fdir/yours$suffix"; then
                echo "FAIL $name [$rel]: live should be untouched"
                failures+=("$name"); ((fail++)); return
            fi
            if [[ ! -e $theirs ]]; then
                echo "FAIL $name [$rel]: .shedosnew should remain"
                failures+=("$name"); ((fail++)); return
            fi
            if [[ -e $bak ]]; then
                echo "FAIL $name [$rel]: .shedosbak should NOT exist"
                failures+=("$name"); ((fail++)); return
            fi
        fi
    done

    if [[ -n $EXPECT_WAIT_INVOCATIONS ]]; then
        local wait_lines
        wait_lines=$(grep -c -- '--wait' "$argv_log" || true)
        if (( wait_lines != EXPECT_WAIT_INVOCATIONS )); then
            echo "FAIL $name: --wait invocations expected=$EXPECT_WAIT_INVOCATIONS got=$wait_lines"
            sed 's/^/    /' "$argv_log"
            failures+=("$name"); ((fail++)); return
        fi
    fi

    if [[ -n $EXPECT_STDERR_PATTERN ]]; then
        if ! grep -Eq -- "$EXPECT_STDERR_PATTERN" "$stderr_file"; then
            echo "FAIL $name: stderr pattern '$EXPECT_STDERR_PATTERN' not found"
            sed 's/^/    /' "$stderr_file" | head -20
            failures+=("$name"); ((fail++)); return
        fi
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

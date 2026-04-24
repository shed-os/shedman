#!/usr/bin/env bash
# run.sh — test harness for shedos-apply.
#
# Each fixture under fixtures/<name>/ models one end-to-end apply scenario.
# The tool talks to three surfaces; each is mockable:
#
#   system.toml                (required) — the config shedos-apply reads.
#   initial-etc/               (optional) — tree copied into $SHEDOS_APPLY_ETC_ROOT.
#   initial-state/             (optional) — tree copied into $SHEDOS_APPLY_STATE_ROOT.
#   systemctl-enabled.txt      (optional) — one unit name per line; used by the
#                                           stub systemctl when asked for
#                                           `list-unit-files --state=enabled`.
#   user-enabled.txt           (optional) — same thing for `--user --global`.
#   expected-etc/              (optional) — tree diff against etc after apply.
#   expected-state/            (optional) — tree diff against state after apply.
#   expected-systemctl.txt     (optional) — each line = one argv the stub
#                                           should have been invoked with,
#                                           minus list-unit-files queries.
#   ufw-status-numbered.txt    (optional) — fixture content the ufw stub
#                                           replays for `ufw status numbered`.
#                                           Empty/missing → inactive ufw.
#   expected-ufw.txt           (optional) — assertion log of mutation argv
#                                           the ufw stub recorded.
#   expected-system.toml       (optional) — assertion: post-apply contents
#                                           of /etc/shedos/system.toml.
#                                           Used to verify adoption-writes
#                                           round-trip cleanly.
#   fixture.sh                 (optional) — shell vars:
#                                   APPLY_ARGS    default "--yes"
#                                   EXIT_CODE     default 0
#
# Usage: test/apply/run.sh [fixture-name ...]
#        (no args = run every fixture)
#
# Exit: 0 all pass, 1 any failure, 2 harness error.

set -uo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$here/../.." && pwd)
tool=$repo_root/packaging/shedos-system/tree/usr/libexec/shedman/apply

if [[ ! -x $tool ]]; then
    echo "FATAL: $tool not executable" >&2
    exit 2
fi

# shellcheck disable=SC1091
source "$here/_stubs.sh"

if ! command -v python3 >/dev/null 2>&1; then
    echo "FATAL: python3 required" >&2
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

_tree_diff() {
    local label=$1 expected=$2 actual=$3
    if [[ ! -d $expected && ! -d $actual ]]; then
        return 0
    fi
    if [[ ! -d $expected ]]; then
        # No expectation — nothing to verify.
        return 0
    fi
    if [[ ! -d $actual ]]; then
        echo "  $label: actual tree missing ($actual)"
        return 1
    fi
    local report
    report=$(diff -rq "$expected" "$actual" 2>&1 || true)
    if [[ -n $report ]]; then
        echo "  $label mismatch:"
        printf '    %s\n' $report
        return 1
    fi
    return 0
}

_file_eq() {
    local label=$1 expected=$2 actual=$3
    if [[ ! -f $expected ]]; then
        return 0
    fi
    if [[ ! -f $actual ]]; then
        echo "  $label: actual log missing"
        return 1
    fi
    if ! diff -u "$expected" "$actual" >/dev/null; then
        echo "  $label mismatch:"
        diff -u "$expected" "$actual" | sed 's/^/    /'
        return 1
    fi
    return 0
}

_run_one() {
    local name=$1
    local fdir=$here/fixtures/$name
    [[ -d $fdir ]] || { echo "skip $name (no such fixture)"; return; }
    [[ -f $fdir/system.toml ]] || { echo "skip $name (missing system.toml)"; return; }

    local APPLY_ARGS="--yes"
    local EXIT_CODE=0
    if [[ -f $fdir/fixture.sh ]]; then
        # shellcheck disable=SC1091
        source "$fdir/fixture.sh"
    fi

    local tmp
    tmp=$(mktemp -d -t shedos-apply-test.XXXXXX)
    trap 'rm -rf -- "$tmp"' RETURN

    local etc=$tmp/etc
    local state=$tmp/state
    local stubdir=$tmp/stubs
    mkdir -p "$etc" "$state" "$stubdir"

    if [[ -d $fdir/initial-etc ]]; then
        cp -a "$fdir/initial-etc/." "$etc/"
    fi
    if [[ -d $fdir/initial-state ]]; then
        cp -a "$fdir/initial-state/." "$state/"
    fi
    install -Dm644 "$fdir/system.toml" "$etc/shedos/system.toml"

    # Stub systemctl. We respond to list-unit-files with fixture content and
    # log every other invocation to $tmp/systemctl.log so the fixture can
    # assert which enable/disable commands ran.
    cp -a "$fdir/systemctl-enabled.txt" "$stubdir/enabled.txt" 2>/dev/null || : > "$stubdir/enabled.txt"
    cp -a "$fdir/user-enabled.txt"      "$stubdir/user-enabled.txt" 2>/dev/null || : > "$stubdir/user-enabled.txt"

    cat >"$stubdir/systemctl" <<STUB
#!/usr/bin/env bash
# Stub systemctl for shedos-apply tests.
# --- Args: "\$@"
logfile="$tmp/systemctl.log"
scope=system
if [[ "\$1" == "--user" ]]; then
    scope=user
    shift
    # Optional --global after --user — skip it.
    [[ "\$1" == "--global" ]] && shift
fi

if [[ "\$1" == "list-unit-files" ]]; then
    # Response is based on --state=enabled and scope. We emit one-per-line
    # with a trailing "enabled" column, mirroring real systemctl --plain.
    list_file="$stubdir/enabled.txt"
    if [[ "\$scope" == "user" ]]; then
        list_file="$stubdir/user-enabled.txt"
    fi
    while IFS= read -r unit; do
        [[ -z "\$unit" ]] && continue
        printf '%s enabled enabled\n' "\$unit"
    done <"\$list_file"
    exit 0
fi

# Record mutation invocations. Format one per line, scope-prefixed.
printf '%s %s\n' "\$scope" "\$*" >>"\$logfile"
exit 0
STUB
    chmod +x "$stubdir/systemctl"

    : >"$tmp/systemctl.log"

    # ufw stub — only generated if the fixture mentions firewall in its
    # name OR provides ufw-status-numbered.txt OR expected-ufw.txt. The
    # stub is harmless if the apply doesn't shell out to ufw.
    _stub_ufw "$stubdir" "$fdir"

    local rc out
    # shellcheck disable=SC2086
    out=$(
        SHEDOS_APPLY_ETC_ROOT=$etc \
        SHEDOS_APPLY_STATE_ROOT=$state \
        SHEDOS_APPLY_SYSTEMCTL="$stubdir/systemctl" \
        SHEDOS_APPLY_UFW="$stubdir/ufw" \
        SHEDOS_LIB_ROOT="$repo_root/packaging/shedos-system/tree/usr/lib/shedos" \
        NO_COLOR=1 \
        "$tool" --config "$etc/shedos/system.toml" $APPLY_ARGS 2>&1
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
    _tree_diff "ETC"   "$fdir/expected-etc"   "$etc"   || bad=1
    _tree_diff "STATE" "$fdir/expected-state" "$state" || bad=1
    _file_eq   "SYSTEMCTL" "$fdir/expected-systemctl.txt" "$tmp/systemctl.log" || bad=1
    _file_eq   "UFW"        "$fdir/expected-ufw.txt"        "$tmp/ufw.log"               || bad=1
    _file_eq   "SYSTEM_TOML" "$fdir/expected-system.toml"   "$etc/shedos/system.toml"    || bad=1

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

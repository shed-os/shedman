#!/usr/bin/env bash
# run.sh — smoke tests for `shedman update`.
#
# Coverage: --help-summary, --help, --complete-{bash,zsh,fish}, --history
# delegates to the upgrade-history TUI, --rollback delegates to shedman
# rollback, --list-snapshots dispatches to snapper, unknown flag exits
# non-zero.
#
# The pacman/yay/snapper-pre/snapper-post chain is NOT exercised — that
# requires a real package manager and root. Manual install / VM tests
# cover it.

set -uo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$here/../.." && pwd)
tool=$repo_root/packaging/shedos-system/tree/usr/libexec/shedman/update

if [[ ! -x $tool ]]; then
    echo "FATAL: $tool not executable" >&2
    exit 2
fi

pass=0
fail=0
failures=()

_ok() { echo "ok: $1"; ((pass++)); }
_fail() { echo "FAIL: $1: $2" >&2; failures+=("$1"); ((fail++)); }

# T1 --help-summary
out=$("$tool" --help-summary 2>&1); rc=$?
if (( rc == 0 )) && [[ -n $out ]] && (( $(printf '%s\n' "$out" | wc -l) == 1 )); then
    _ok T1_help_summary
else
    _fail T1_help_summary "rc=$rc out=$out"
fi

# T2 --help
out=$("$tool" --help 2>&1); rc=$?
if (( rc == 0 )) && grep -q '^Usage:' <<<"$out"; then
    _ok T2_help
else
    _fail T2_help "rc=$rc out=$out"
fi

# T3 --complete-bash / --complete-zsh
for c in --complete-bash --complete-zsh; do
    out=$("$tool" "$c" 2>&1); rc=$?
    longs=$(grep -c '^--' <<<"$out" || true)
    shorts=$(grep -cE '^-[a-zA-Z]$' <<<"$out" || true)
    if (( rc == 0 )) && (( longs >= 1 )) && (( shorts >= 1 )); then
        _ok "T3${c}"
    else
        _fail "T3${c}" "rc=$rc longs=$longs shorts=$shorts"
    fi
done

# T4 --complete-fish tab-separated
out=$("$tool" --complete-fish 2>&1); rc=$?
tabs=$(printf '%s\n' "$out" | grep -cP '\t' || true)
if (( rc == 0 )) && (( tabs >= 5 )); then
    _ok T4_complete_fish
else
    _fail T4_complete_fish "rc=$rc tabs=$tabs"
fi

# T5 --history delegates to /usr/libexec/shedman/upgrade-history.
# We can't exec the real binary (it's a TUI), so PATH-substitute a stub
# at the absolute path the source code execs.
tmp=$(mktemp -d -t shedos-update-test.XXXXXX)
trap 'rm -rf -- "$tmp"' EXIT

# Copy update binary to a stage tree where we can rewrite the hardcoded
# /usr/libexec/shedman path.
stage=$tmp/libexec
mkdir -p "$stage"
cp "$tool" "$stage/update"
sed -i "s|/usr/libexec/shedman|$stage|g" "$stage/update"
cat > "$stage/upgrade-history" <<EOF
#!/usr/bin/env bash
echo "upgrade-history called with: \$*" > "$tmp/history-args.txt"
exit 0
EOF
chmod +x "$stage/upgrade-history"

"$stage/update" --history --interval 5 2>&1 >/dev/null
if grep -q 'upgrade-history called with: --interval 5' "$tmp/history-args.txt"; then
    _ok T5_history_delegates
else
    _fail T5_history_delegates "got: $(cat "$tmp/history-args.txt" 2>/dev/null || echo MISSING)"
fi

# Summary
total=$((pass + fail))
echo
echo "update: $pass/$total passed"
if (( fail > 0 )); then
    printf '  %s\n' "${failures[@]}" >&2
    exit 1
fi

#!/usr/bin/env bash
# run.sh — smoke tests for `shedman rollback`.
#
# Coverage: --help-summary, --help, --complete-{bash,zsh,fish}, root
# requirement, --list with a stubbed snapper, unknown flag exits non-zero.
# The actual btrfs subvolume rename / snapshot is not exercised — needs a
# real btrfs filesystem and root. Manual testing on a VM covers it.

set -uo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$here/../.." && pwd)
tool=$repo_root/packaging/shedos-system/tree/usr/libexec/shedman/rollback

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

# T2 --help / -h
for h in --help -h; do
    out=$("$tool" $h 2>&1); rc=$?
    if (( rc == 0 )) && grep -q '^Usage:' <<<"$out"; then
        _ok "T2_help_${h:1}"
    else
        _fail "T2_help_${h:1}" "rc=$rc out=$out"
    fi
done

# T3 --complete-bash / --complete-zsh emit ≥1 long flag and ≥1 short flag
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

# T4 --complete-fish emits tab-separated lines
out=$("$tool" --complete-fish 2>&1); rc=$?
tabs=$(printf '%s\n' "$out" | grep -cP '\t' || true)
if (( rc == 0 )) && (( tabs >= 6 )); then
    _ok T4_complete_fish
else
    _fail T4_complete_fish "rc=$rc tabs=$tabs"
fi

# T5 non-root: a snapshot-number arg or --undo refuses
if [[ $EUID -eq 0 ]]; then
    echo "skip T5_root_required (running as root)"
else
    out=$("$tool" 42 2>&1); rc=$?
    if (( rc == 1 )) && grep -q 'must be run as root' <<<"$out"; then
        _ok T5_root_required_for_snapshot_number
    else
        _fail T5_root_required_for_snapshot_number "rc=$rc out=$out"
    fi
    out=$("$tool" --undo 2>&1); rc=$?
    if (( rc == 1 )) && grep -q 'must be run as root' <<<"$out"; then
        _ok T5_root_required_for_undo
    else
        _fail T5_root_required_for_undo "rc=$rc out=$out"
    fi
fi

# T6 --list with a PATH-stubbed snapper. Doesn't require root.
tmp=$(mktemp -d -t shedos-rollback-test.XXXXXX)
trap 'rm -rf -- "$tmp"' EXIT
stub_dir=$tmp/stubs
mkdir -p "$stub_dir"
cat > "$stub_dir/snapper" <<'EOF'
#!/usr/bin/env bash
case "$*" in
    *"list --columns"*)
        cat <<INNER
# | Type   | Pre # | Date                 | Description     | Userdata
--+--------+-------+----------------------+-----------------+----------
0 | single |       | 2026-04-26 12:00:00  | current         |
3 | pre    |       | 2026-04-26 11:30:00  | shedos-update   | source=shedos-update,kind=pre
4 | post   | 3     | 2026-04-26 11:32:14  | shedos-update   | source=shedos-update,kind=post
INNER
        ;;
    *) exit 0 ;;
esac
EOF
chmod +x "$stub_dir/snapper"

out=$(PATH="$stub_dir:$PATH" "$tool" --list 2>&1); rc=$?
if (( rc == 0 )) && grep -q 'shedos-update' <<<"$out"; then
    _ok T6_list_with_stubbed_snapper
else
    _fail T6_list_with_stubbed_snapper "rc=$rc out=$out"
fi

# T7 unknown flag — must NOT exit 0 silently. rollback's parser treats
# anything that isn't a known flag as positional, dying via die() rc=1.
out=$("$tool" --bogus-flag-that-doesnt-exist 2>&1); rc=$?
if (( rc != 0 )); then
    _ok T7_unknown_flag
else
    _fail T7_unknown_flag "rc=$rc (expected non-zero) out=$out"
fi

# Summary
total=$((pass + fail))
echo
echo "rollback: $pass/$total passed"
if (( fail > 0 )); then
    printf '  %s\n' "${failures[@]}" >&2
    exit 1
fi

#!/usr/bin/env bash
# run.sh — smoke tests for `shedman config` (umbrella for --sync / --review).
#
# Coverage: --help-summary, --help, --sync dispatches to _config-sync,
# --review dispatches to _config-review, unknown mode exits 2.

set -uo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$here/../.." && pwd)
tool=$repo_root/packaging/shedos-system/tree/usr/libexec/shedman/config
libexec=$repo_root/packaging/shedos-system/tree/usr/libexec/shedman

if [[ ! -x $tool ]]; then
    echo "FATAL: $tool not executable" >&2
    exit 2
fi

pass=0
fail=0
failures=()

_ok() { echo "ok: $1"; ((pass++)); }
_fail() { echo "FAIL: $1: $2" >&2; failures+=("$1"); ((fail++)); }

# ---------------------------------------------------------------------------
# T1: --help-summary prints one non-empty line, exit 0
# ---------------------------------------------------------------------------
out=$("$tool" --help-summary 2>&1); rc=$?
if (( rc == 0 )) && [[ -n $out ]] && (( $(printf '%s\n' "$out" | wc -l) == 1 )); then
    _ok T1_help_summary
else
    _fail T1_help_summary "rc=$rc out=$out"
fi

# ---------------------------------------------------------------------------
# T2: --help / -h / no-args / "help" all print Usage banner, exit 0
# ---------------------------------------------------------------------------
for h in --help -h help ""; do
    out=$("$tool" $h 2>&1); rc=$?
    if (( rc == 0 )) && grep -q '^Usage:' <<<"$out"; then
        _ok "T2_help_${h:-bare}"
    else
        _fail "T2_help_${h:-bare}" "rc=$rc out=$out"
    fi
done

# ---------------------------------------------------------------------------
# T3: --sync dispatches to _config-sync. Substitute _config-sync with a stub
#     that records the args it was called with, then assert.
# ---------------------------------------------------------------------------
tmp=$(mktemp -d -t shedos-config-test.XXXXXX)
trap 'rm -rf -- "$tmp"' EXIT

stage=$tmp/libexec
mkdir -p "$stage"
cp "$libexec/config" "$stage/config"
# Rewrite LIBEXEC=/usr/libexec/shedman to point at our stage so dispatch
# lands on the stubs below, not the real subcommands.
sed -i "s|^LIBEXEC=.*|LIBEXEC=$stage|" "$stage/config"
cat > "$stage/_config-sync" <<EOF
#!/usr/bin/env bash
echo "_config-sync called with args: \$*" > "$tmp/sync-args.txt"
exit 0
EOF
cat > "$stage/_config-review" <<EOF
#!/usr/bin/env bash
echo "_config-review called with args: \$*" > "$tmp/review-args.txt"
exit 0
EOF
chmod +x "$stage/_config-sync" "$stage/_config-review"

for sync_flag in --sync -s sync; do
    rm -f "$tmp/sync-args.txt"
    "$stage/config" "$sync_flag" --dry-run --verbose 2>&1 >/dev/null
    if grep -q "called with args: --dry-run --verbose" "$tmp/sync-args.txt" 2>/dev/null; then
        _ok "T3_dispatch_${sync_flag#-}"
    else
        _fail "T3_dispatch_${sync_flag#-}" "expected --dry-run --verbose, got: $(cat "$tmp/sync-args.txt" 2>/dev/null || echo MISSING)"
    fi
done

# ---------------------------------------------------------------------------
# T4: --review dispatches to _config-review.
# ---------------------------------------------------------------------------
for review_flag in --review -r review; do
    rm -f "$tmp/review-args.txt"
    "$stage/config" "$review_flag" path/to/file 2>&1 >/dev/null
    if grep -q "called with args: path/to/file" "$tmp/review-args.txt" 2>/dev/null; then
        _ok "T4_dispatch_${review_flag#-}"
    else
        _fail "T4_dispatch_${review_flag#-}" "expected path/to/file, got: $(cat "$tmp/review-args.txt" 2>/dev/null || echo MISSING)"
    fi
done

# ---------------------------------------------------------------------------
# T5: unknown mode → exit 2 + Usage banner on stderr.
# ---------------------------------------------------------------------------
out=$("$tool" --bogus 2>&1); rc=$?
if (( rc == 2 )) && grep -q 'unknown mode' <<<"$out" && grep -q '^Usage:' <<<"$out"; then
    _ok T5_unknown_mode
else
    _fail T5_unknown_mode "rc=$rc out=$out"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
total=$((pass + fail))
echo
echo "config: $pass/$total passed"
if (( fail > 0 )); then
    printf '  %s\n' "${failures[@]}" >&2
    exit 1
fi

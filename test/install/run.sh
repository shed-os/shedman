#!/usr/bin/env bash
# run.sh — smoke tests for `shedman install`.
#
# Coverage: --help-summary, --help/-h, no-args usage, root-refusal,
# --search (with/without query), and per-package source detection
# (pacman vs AUR/yay) via PATH-stubbed pacman/yay/sudo. The real
# pacman -S / yay -S install is never run — every external tool is
# stubbed and logs its argv instead of executing.

set -uo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$here/../.." && pwd)
tool=$repo_root/packaging/shedos-system/tree/usr/libexec/shedman/install

if [[ ! -x $tool ]]; then
    echo "FATAL: $tool not executable" >&2
    exit 2
fi

pass=0
fail=0
failures=()

_ok() { echo "ok: $1"; ((pass++)); }
_fail() { echo "FAIL: $1: $2" >&2; failures+=("$1"); ((fail++)); }

tmp=$(mktemp -d -t shedos-install-test.XXXXXX)
trap 'rm -rf -- "$tmp"' EXIT

# Stub pacman/yay/sudo/id on PATH. Each tool appends its argv to a
# per-tool log so the test can assert what got invoked, and exits
# according to fixture env vars so we can drive both routing branches.
#
# STUB_PACMAN_SI_RC: rc for `pacman -Si` (source detection). 0 = found.
# STUB_YAY_SI_RC:    rc for `yay -Si`.
# STUB_ID_UID:       uid that `id -u` reports.
# STUB_HIDE_YAY=1:   make `yay` vanish (command -v yay fails).
stub_dir=$tmp/stubs
mkdir -p "$stub_dir"

cat > "$stub_dir/pacman" <<STUB
#!/usr/bin/env bash
echo "\$*" >> "$tmp/pacman.log"
[[ \$1 == -Si ]] && exit "\${STUB_PACMAN_SI_RC:-0}"
exit 0
STUB

cat > "$stub_dir/yay" <<STUB
#!/usr/bin/env bash
echo "\$*" >> "$tmp/yay.log"
[[ \$1 == -Si ]] && exit "\${STUB_YAY_SI_RC:-0}"
exit 0
STUB

# sudo just drops its own argv and runs the rest through the stubs.
cat > "$stub_dir/sudo" <<STUB
#!/usr/bin/env bash
echo "\$*" >> "$tmp/sudo.log"
exec "\$@"
STUB

cat > "$stub_dir/id" <<STUB
#!/usr/bin/env bash
[[ \$1 == -u ]] && { echo "\${STUB_ID_UID:-1000}"; exit 0; }
exec /usr/bin/id "\$@"
STUB

chmod +x "$stub_dir"/{pacman,yay,sudo,id}
export PATH="$stub_dir:$PATH"

reset_logs() { rm -f "$tmp"/{pacman,yay,sudo}.log; }

# T1 --help-summary: one non-empty line, exit 0
out=$("$tool" --help-summary 2>&1); rc=$?
if (( rc == 0 )) && [[ -n $out ]] && (( $(printf '%s\n' "$out" | wc -l) == 1 )); then
    _ok T1_help_summary
else
    _fail T1_help_summary "rc=$rc out=$out"
fi

# T2 --help / -h: Usage banner, exit 0
for h in --help -h; do
    out=$("$tool" "$h" 2>&1); rc=$?
    if (( rc == 0 )) && grep -q '^Usage:' <<<"$out"; then
        _ok "T2_help_${h:1}"
    else
        _fail "T2_help_${h:1}" "rc=$rc out=$out"
    fi
done

# T3 no args: print usage, exit 1
out=$("$tool" 2>&1); rc=$?
if (( rc == 1 )) && grep -q '^Usage:' <<<"$out"; then
    _ok T3_no_args_usage_exit1
else
    _fail T3_no_args_usage_exit1 "rc=$rc out=$out"
fi

# T4 root-refusal: install (not search/help) as uid 0 exits 2
out=$(STUB_ID_UID=0 "$tool" somepkg 2>&1); rc=$?
if (( rc == 2 )) && grep -q "don't run as root" <<<"$out"; then
    _ok T4_root_refused
else
    _fail T4_root_refused "rc=$rc out=$out"
fi

# T5 --search routes the query to pacman -Ss and yay -Ss
reset_logs
out=$("$tool" --search ripgrep 2>&1); rc=$?
if (( rc == 0 )) \
   && grep -q '^-Ss ripgrep$' "$tmp/pacman.log" 2>/dev/null \
   && grep -q -- '-Ss --aur ripgrep' "$tmp/yay.log" 2>/dev/null; then
    _ok T5_search_hits_pacman_and_yay
else
    _fail T5_search_hits_pacman_and_yay "rc=$rc out=$out pacman=$(cat "$tmp/pacman.log" 2>/dev/null) yay=$(cat "$tmp/yay.log" 2>/dev/null)"
fi

# T5b -s with no query exits 1
out=$("$tool" -s 2>&1); rc=$?
if (( rc == 1 )) && grep -q 'needs a query' <<<"$out"; then
    _ok T5b_search_no_query_exit1
else
    _fail T5b_search_no_query_exit1 "rc=$rc out=$out"
fi

# T6 pacman routing: pacman -Si succeeds → installed via sudo pacman -S
reset_logs
out=$(STUB_PACMAN_SI_RC=0 "$tool" fd 2>&1); rc=$?
if (( rc == 0 )) \
   && grep -q '^-Si fd$' "$tmp/pacman.log" 2>/dev/null \
   && grep -q -- '-S --needed --noconfirm fd' "$tmp/pacman.log" 2>/dev/null \
   && [[ ! -s "$tmp/yay.log" ]]; then
    _ok T6_pacman_routing
else
    _fail T6_pacman_routing "rc=$rc out=$out pacman=$(cat "$tmp/pacman.log" 2>/dev/null) yay=$(cat "$tmp/yay.log" 2>/dev/null)"
fi

# T7 AUR routing: pacman -Si fails, yay -Si succeeds → installed via yay -S
reset_logs
out=$(STUB_PACMAN_SI_RC=1 STUB_YAY_SI_RC=0 "$tool" some-aur-bin 2>&1); rc=$?
if (( rc == 0 )) \
   && grep -q -- '-Si --aur some-aur-bin' "$tmp/yay.log" 2>/dev/null \
   && grep -q -- '-S --needed --noconfirm' "$tmp/yay.log" 2>/dev/null \
   && grep -q -- '--mflags=--skippgpcheck some-aur-bin' "$tmp/yay.log" 2>/dev/null \
   && ! grep -q -- '-S --needed' "$tmp/pacman.log" 2>/dev/null; then
    _ok T7_aur_routing
else
    _fail T7_aur_routing "rc=$rc out=$out pacman=$(cat "$tmp/pacman.log" 2>/dev/null) yay=$(cat "$tmp/yay.log" 2>/dev/null)"
fi

# T8 unknown package: neither repo has it → exit 1, nothing installed
reset_logs
out=$(STUB_PACMAN_SI_RC=1 STUB_YAY_SI_RC=1 "$tool" nope-pkg 2>&1); rc=$?
if (( rc == 1 )) \
   && grep -q 'not found in any repo: nope-pkg' <<<"$out" \
   && ! grep -q -- '-S --needed' "$tmp/pacman.log" 2>/dev/null \
   && ! grep -q -- '-S --needed' "$tmp/yay.log" 2>/dev/null; then
    _ok T8_unknown_pkg_exit1
else
    _fail T8_unknown_pkg_exit1 "rc=$rc out=$out"
fi

# T9 AUR-only pkg but yay missing: exit 1 before any install attempt.
# Drop the yay stub so command -v yay fails; pacman -Si still fails so
# the pkg can't route to pacman either → "not found in any repo".
reset_logs
no_yay=$tmp/no-yay
mkdir -p "$no_yay"
ln -sf "$stub_dir/pacman" "$no_yay/pacman"
ln -sf "$stub_dir/sudo" "$no_yay/sudo"
ln -sf "$stub_dir/id" "$no_yay/id"
out=$(PATH="$no_yay:/usr/bin:/bin" STUB_PACMAN_SI_RC=1 "$tool" aur-only 2>&1); rc=$?
if (( rc == 1 )) && grep -q 'not found in any repo' <<<"$out"; then
    _ok T9_aur_pkg_no_yay_exit1
else
    _fail T9_aur_pkg_no_yay_exit1 "rc=$rc out=$out"
fi

# Summary
total=$((pass + fail))
echo
echo "Summary: $pass passed, $fail failed"
if (( fail > 0 )); then
    printf '  %s\n' "${failures[@]}" >&2
    exit 1
fi

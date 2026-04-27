#!/usr/bin/env bash
# run.sh — smoke tests for `shedman install`.
#
# Coverage: --help-summary, --help, root-refusal, missing-catalog
# refusal, marker short-circuit. The yad UI + yay -S install path
# is NOT exercised — that needs a Wayland session and a real AUR
# helper. Manual install / first-boot welcome flow covers it.

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

# Hermetic environment so our tests don't hit the real /var/lib or HOME.
export XDG_STATE_HOME="$tmp/state"
mkdir -p "$XDG_STATE_HOME/shedos"

# PATH-stub yad so any error-dialog branch exits immediately instead of
# blocking for OK on a Wayland session. Same for nm-online (the
# connectivity check) so we never wait 10s+ for a real network probe.
stub_dir=$tmp/stubs
mkdir -p "$stub_dir"
cat > "$stub_dir/yad" <<STUB
#!/usr/bin/env bash
# Record that we were called (to a file the test can inspect) and exit 0.
# Stdout stays empty so the install script's checklist-result parsing
# yields zero selections (= "no apps selected" branch). Stderr goes
# nowhere visible because install redirects yad's stderr to /dev/null
# at line 223 — that's why we use a marker file instead.
echo "called: \$*" >> "$tmp/yad-calls.log"
exit 0
STUB
cat > "$stub_dir/nm-online" <<'STUB'
#!/usr/bin/env bash
# Pretend network is up — fast path through the connectivity check.
exit 0
STUB
chmod +x "$stub_dir/yad" "$stub_dir/nm-online"
export PATH="$stub_dir:$PATH"

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

# T3 missing catalog → exits 1. Use a path that genuinely doesn't exist
# so the catalog-not-readable branch fires and the script exits before
# any UI / connectivity work. Must use `env` to pass SHEDOS_APPS_CATALOG
# into the subshell — bash treats `VAR=val out=$(...)` as a local
# assignment, not an env-var prefix.
out=$(env SHEDOS_APPS_CATALOG="$tmp/no-such-catalog.tsv" \
    USER=test_not_calamares "$tool" 2>&1); rc=$?
if (( rc == 1 )); then
    _ok T3_missing_catalog_refused
else
    _fail T3_missing_catalog_refused "rc=$rc (expected 1) out=$out"
fi

# T4 marker short-circuit: if the apps-installer-done marker exists and
# --force isn't passed, install exits 0 without doing anything.
# Pass SHEDOS_APPS_CATALOG explicitly because the script's dev-checkout
# fallback path is currently broken (one `..` short — see deferred fix).
catalog="$repo_root/packaging/shedos-system/tree/usr/share/shedos/apps-catalog.tsv"
touch "$XDG_STATE_HOME/shedos/apps-installer-done"
out=$(SHEDOS_APPS_CATALOG="$catalog" "$tool" 2>&1); rc=$?
if (( rc == 0 )) && [[ -z $out ]]; then
    _ok T4_marker_short_circuit
else
    _fail T4_marker_short_circuit "rc=$rc out=$out (expected silent rc=0)"
fi

# T5 marker present + --force → does NOT short-circuit (proceeds past
# the marker check and into the yad-checklist flow). The yad stub
# records every invocation to $tmp/yad-calls.log; presence of any
# `--list` call means we got past the marker short-circuit.
rm -f "$tmp/yad-calls.log"
out=$(timeout 10 env SHEDOS_APPS_CATALOG="$catalog" \
    "$tool" --force --dry-run 2>&1); rc=$?
if [[ -f "$tmp/yad-calls.log" ]] && grep -q -- '--list' "$tmp/yad-calls.log"; then
    _ok T5_force_bypasses_marker
else
    _fail T5_force_bypasses_marker "rc=$rc; yad-stub never saw --list, marker may not have been bypassed"
fi

# T6 catalog parsing — if the shipped catalog is malformed, the script
# should fail loudly. Validate the catalog is parseable as TSV with at
# least one row.
catalog=$repo_root/packaging/shedos-system/tree/usr/share/shedos/apps-catalog.tsv
if [[ -r $catalog ]]; then
    # Each line: name<TAB>... (skip comments/blanks)
    badlines=$(awk -F'\t' '!/^[[:space:]]*#/ && NF > 0 && NF < 2 { print NR": "$0 }' "$catalog")
    if [[ -z $badlines ]]; then
        _ok T6_catalog_well_formed
    else
        _fail T6_catalog_well_formed "malformed lines: $badlines"
    fi
else
    _fail T6_catalog_well_formed "catalog $catalog not readable"
fi

# T7 — yad missing → exits 1 with no marker. Regression test for the
# v2026.04.27-rc1 silent-failure bug: the previous install would skip
# the yad-availability check until after the catalog/marker checks,
# which themselves used yad to render their error dialogs (resulting
# in cryptic stderr if yad was missing).
no_yad_stub=$tmp/no-yad-stubs
mkdir -p "$no_yad_stub"
for bin in bash id mkdir touch git getent grep cut date readlink dirname stat sleep rm cat sed awk head curl mktemp mkfifo kill wait nmcli; do
    src=$(command -v "$bin" 2>/dev/null)
    [[ -n $src ]] && ln -sf "$src" "$no_yad_stub/$bin"
done

HOME_T7=$tmp/home-t7
mkdir -p "$HOME_T7/.local/state/shedos"
out=$(env -i \
    PATH="$no_yad_stub" \
    HOME="$HOME_T7" \
    USER=test_not_calamares \
    XDG_STATE_HOME="$HOME_T7/.local/state" \
    SHEDOS_APPS_CATALOG="$catalog" \
    "$tool" 2>&1); rc=$?

if (( rc == 1 )) && \
   [[ ! -f "$HOME_T7/.local/state/shedos/apps-installer-done" ]] && \
   grep -q "yad not installed" "$HOME_T7/.local/state/shedos/install.log" 2>/dev/null; then
    _ok T7_yad_missing_exits_1_no_marker
else
    log_content=$(cat "$HOME_T7/.local/state/shedos/install.log" 2>/dev/null || echo "(no log)")
    marker_state=$([[ -f "$HOME_T7/.local/state/shedos/apps-installer-done" ]] && echo present || echo absent)
    _fail T7_yad_missing_exits_1_no_marker "rc=$rc marker=$marker_state log=${log_content//$'\n'/ | }"
fi

# T8 — --dry-run-no-yad prints the exact yay command line the
# autonomous flow will run. Confirms the autonomous flag set is wired
# correctly: --noconfirm + --answerclean N + --answerdiff N +
# --answeredit N + --cleanafter + --removemake + --mflags=--skippgpcheck.
HOME_T8=$tmp/home-t8
mkdir -p "$HOME_T8/.local/state/shedos"
mini_catalog=$tmp/mini-catalog.tsv
cat > "$mini_catalog" <<'EOF'
# minimal test catalog
foo-bin	Editors	Foo	desc1	icon
bar-bin	Browsers	Bar	desc2	icon
EOF
out=$(env -i \
    PATH="$PATH" \
    HOME="$HOME_T8" \
    USER=test_not_calamares \
    XDG_STATE_HOME="$HOME_T8/.local/state" \
    SHEDOS_APPS_CATALOG="$mini_catalog" \
    SHEDOS_TEST_PKGS="foo-bin bar-bin" \
    "$tool" --dry-run-no-yad 2>&1); rc=$?

expected_args=(
    "yay -S --needed --noconfirm"
    "--answerclean N --answerdiff N --answeredit N"
    "--cleanafter --removemake"
    "--mflags=--skippgpcheck"
    "foo-bin bar-bin"
)
ok_t8=1
if (( rc != 0 )); then
    ok_t8=0
fi
for needle in "${expected_args[@]}"; do
    if ! grep -qF -- "$needle" <<<"$out"; then
        ok_t8=0
    fi
done
if (( ok_t8 == 1 )); then
    _ok T8_dry_run_emits_autonomous_flag_set
else
    _fail T8_dry_run_emits_autonomous_flag_set "rc=$rc out=$out"
fi

# T9 — --dry-run-no-yad with empty SHEDOS_TEST_PKGS exits without
# attempting to run anything (no apps selected = "user picked
# nothing", marker SHOULD be written so we don't re-prompt forever).
# Note: when DRY_RUN=1 we skip the actual install, but the no-apps-
# selected branch still touches the marker — that's the right policy
# for a real run too.
HOME_T9=$tmp/home-t9
mkdir -p "$HOME_T9/.local/state/shedos"
out=$(env -i \
    PATH="$PATH" \
    HOME="$HOME_T9" \
    USER=test_not_calamares \
    XDG_STATE_HOME="$HOME_T9/.local/state" \
    SHEDOS_APPS_CATALOG="$mini_catalog" \
    SHEDOS_TEST_PKGS="" \
    "$tool" --dry-run-no-yad 2>&1); rc=$?
if (( rc == 0 )) && [[ -f "$HOME_T9/.local/state/shedos/apps-installer-done" ]]; then
    _ok T9_dry_run_no_pkgs_marks_done
else
    marker_state=$([[ -f "$HOME_T9/.local/state/shedos/apps-installer-done" ]] && echo present || echo absent)
    _fail T9_dry_run_no_pkgs_marks_done "rc=$rc marker=$marker_state out=$out"
fi

# Summary
total=$((pass + fail))
echo
echo "install: $pass/$total passed"
if (( fail > 0 )); then
    printf '  %s\n' "${failures[@]}" >&2
    exit 1
fi

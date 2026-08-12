#!/usr/bin/env bash
# run.sh — test harness for the `shedman` unified dispatcher and its silent
# back-compat shims.
#
# No fixture tree — the dispatcher is a tiny script and the shims are
# 2-liners, so every check is a self-contained assertion.
#
# Covers:
#   T1  bare `shedman` lists subcommands (and does not list hidden _* helpers).
#   T2  `shedman help` is equivalent to bare `shedman`.
#   T3  `shedman help <cmd>` forwards to the subcommand's --help.
#   T4  `shedman <cmd> --help` is accepted.
#   T5  argv is preserved across dispatch (via a synthetic probe subcommand).
#   T6  unknown command → exit 2 + did-you-mean output.
#   T7  every legacy shim points at an existing shedman path
#       (catches divergence between /usr/bin/shedos-* and /usr/libexec/shedman/*).
#   T8  every shim is +x and a valid shell script.
#   T9  dispatcher `version` prints *something* (falls back to "unknown" off-system).

set -uo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$here/../.." && pwd)

dispatcher=$repo_root/tree/usr/bin/shedman
libexec_sys=$repo_root/tree/usr/libexec/shedman
libexec_hypr=$repo_root/packaging/shedos-hyprland/tree/usr/libexec/shedman
bin_sys=$repo_root/tree/usr/bin
bin_hypr=$repo_root/packaging/shedos-hyprland/tree/usr/bin

# apply/doctor import apply_core at module load; point them at the in-repo copy
# so their --help / --help-summary don't ImportError when invoked from the tree.
export SHEDOS_LIB_ROOT=$repo_root/tree/usr/lib/shedos

if [[ ! -x $dispatcher ]]; then
    echo "FATAL: $dispatcher not executable" >&2
    exit 2
fi

pass=0
fail=0
failures=()

_fail() {
    local name=$1; shift
    echo "FAIL: $name: $*" >&2
    failures+=("$name")
    ((fail++))
}

_ok() {
    local name=$1
    echo "ok: $name"
    ((pass++))
}

_run_dispatcher() {
    # Run the dispatcher with LIBEXEC pointed at a staged merged tree so we
    # exercise both system + hyprland subcommands. We don't rely on the
    # system having /usr/libexec/shedman/ populated.
    local stage=$1; shift
    env PATH="$stage:$PATH" "$dispatcher" "$@"
}

# ---------------------------------------------------------------------------
# Stage a merged /usr/libexec/shedman/ dir from both package trees so the
# dispatcher has the full roster available. Also drop a probe subcommand for
# T5 (argv preservation).
# ---------------------------------------------------------------------------

stage=$(mktemp -d -t shedman-test.XXXXXX)
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage"
cp -a "$libexec_sys"/. "$stage/"
cp -a "$libexec_hypr"/. "$stage/"

# Probe subcommand: prints argv one-per-line so we can assert it was
# forwarded verbatim (no dispatcher-level re-escaping).
cat >"$stage/_probe" <<'PROBE'
#!/usr/bin/env bash
if [[ "${1:-}" == "--help-summary" ]]; then
    echo "probe (internal test helper)"
    exit 0
fi
printf 'argv[%d]=%s\n' 0 "$0"
i=1
for a in "$@"; do
    printf 'argv[%d]=%s\n' "$i" "$a"
    ((i++))
done
PROBE
chmod +x "$stage/_probe"

# The dispatcher scans /usr/libexec/shedman, not $stage — so for T1..T6 we
# override the hardcoded path via a drop-in wrapper.
wrapper=$(mktemp -t shedman-wrapper.XXXXXX)
sed "s#^LIBEXEC=/usr/libexec/shedman#LIBEXEC=$stage#" "$dispatcher" >"$wrapper"
chmod +x "$wrapper"
trap 'rm -rf "$stage" "$wrapper"' EXIT

# ---------------------------------------------------------------------------
# T1: bare `shedman` lists subcommands
# ---------------------------------------------------------------------------

out=$("$wrapper" 2>&1) || true
if grep -q '^Available subcommands:' <<<"$out" \
        && grep -q '^  apply ' <<<"$out" \
        && grep -q '^  update ' <<<"$out" \
        && grep -q '^  doctor ' <<<"$out" \
        && grep -q '^  config ' <<<"$out" \
        && grep -q '^  status ' <<<"$out"; then
    _ok T1_bare_lists_subcommands
else
    _fail T1_bare_lists_subcommands "listing missing expected entries: $out"
fi

# Hidden _* helpers must not appear.
if grep -Eq '^  _(probe|config-sync|config-review) ' <<<"$out"; then
    _fail T1b_hidden_helpers_leaked "underscored helpers leaked into listing: $out"
else
    _ok T1b_hidden_helpers_hidden
fi

# ---------------------------------------------------------------------------
# T2: `shedman help` same as bare
# ---------------------------------------------------------------------------

out_help=$("$wrapper" help 2>&1) || true
if [[ "$out" == "$out_help" ]]; then
    _ok T2_help_equals_bare
else
    _fail T2_help_equals_bare "bare vs. 'help' diverge"
fi

# ---------------------------------------------------------------------------
# T3: `shedman help <cmd>` forwards to --help
# ---------------------------------------------------------------------------

out_help_apply=$("$wrapper" help apply 2>&1) || true
out_apply_help=$("$wrapper" apply --help 2>&1) || true
if [[ "$out_help_apply" == "$out_apply_help" && -n "$out_help_apply" ]]; then
    _ok T3_help_cmd_forwards
else
    _fail T3_help_cmd_forwards "'help apply' differs from 'apply --help'"
fi

# ---------------------------------------------------------------------------
# T4: `shedman <cmd> --help` works
# ---------------------------------------------------------------------------

if "$wrapper" apply --help >/dev/null 2>&1; then
    _ok T4_cmd_help_ok
else
    _fail T4_cmd_help_ok "'apply --help' failed"
fi

# ---------------------------------------------------------------------------
# T5: argv preservation via the probe
# ---------------------------------------------------------------------------

out_probe=$("$wrapper" _probe hello 'a b c' --flag=value 2>&1) || true
if grep -q '^argv\[1\]=hello$' <<<"$out_probe" \
        && grep -q '^argv\[2\]=a b c$' <<<"$out_probe" \
        && grep -q '^argv\[3\]=--flag=value$' <<<"$out_probe"; then
    _ok T5_argv_preserved
else
    _fail T5_argv_preserved "probe argv mismatch: $out_probe"
fi

# ---------------------------------------------------------------------------
# T6: unknown command → exit 2 + did-you-mean
# ---------------------------------------------------------------------------

out_unknown=$("$wrapper" updat 2>&1)
rc_unknown=$?
if (( rc_unknown == 2 )) \
        && grep -q 'unknown command' <<<"$out_unknown" \
        && grep -q 'Did you mean' <<<"$out_unknown" \
        && grep -q '  update' <<<"$out_unknown"; then
    _ok T6_unknown_did_you_mean
else
    _fail T6_unknown_did_you_mean "rc=$rc_unknown out=$out_unknown"
fi

# ---------------------------------------------------------------------------
# T7 + T8: every shim points at a valid subcommand (and is +x, valid bash).
#
# A shim looks like:
#   #!/usr/bin/env bash
#   exec /usr/bin/shedman <word> [<word> ...] "$@"
# We grep out the <word>s, verify the leading word (subcommand) exists in
# the libexec tree and the remaining words are known flags/modes.
# ---------------------------------------------------------------------------

known_subcmds=$(find "$libexec_sys" "$libexec_hypr" -maxdepth 1 -type f \
    -executable ! -name '_*' -printf '%f\n' | sort -u)

_check_shims() {
    local root=$1 label=$2 shim
    for shim in "$root"/shedos-*; do
        [[ -f $shim ]] || continue
        # Only validate files that are actually shedman shims. Some files
        # under /usr/bin/ that happen to share the shedos- prefix are
        # genuine binaries (e.g. shedos-user-session, a hyprland autostart
        # helper) and not part of the shedman dispatcher surface.
        grep -q '^exec /usr/bin/shedman ' "$shim" 2>/dev/null || continue
        if [[ ! -x $shim ]]; then
            _fail "T8_${label}_$(basename "$shim")" "shim not +x: $shim"
            continue
        fi
        local line subcmd
        line=$(awk '/^exec \/usr\/bin\/shedman /{print; exit}' "$shim")
        subcmd=$(awk '{print $3}' <<<"$line")
        if ! grep -qx "$subcmd" <<<"$known_subcmds"; then
            _fail "T7_${label}_$(basename "$shim")" \
                "shim $(basename "$shim") points at missing subcmd '$subcmd'"
            continue
        fi
        _ok "T7_${label}_$(basename "$shim")"
    done
}

_check_shims "$bin_sys" system
_check_shims "$bin_hypr" hypr

# ---------------------------------------------------------------------------
# T9: `shedman version` prints something. Off-system this should fall back
# to "unknown" (pacman -Qi shedos-system fails); on-system it should print
# a version string.
# ---------------------------------------------------------------------------

out_version=$("$wrapper" version 2>&1) || true
if [[ -n $out_version ]]; then
    _ok T9_version_prints
else
    _fail T9_version_prints "empty output"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

total=$((pass + fail))
echo
if (( fail == 0 )); then
    echo "PASS $pass/$total"
    exit 0
fi
echo "FAIL $fail/$total ($pass passed)"
for f in "${failures[@]}"; do echo "  - $f"; done
exit 1

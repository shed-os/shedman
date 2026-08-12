#!/usr/bin/env bash
# run.sh — test harness for the `shedman` unified dispatcher.
#
# No fixture tree — the dispatcher is a tiny script, so every check is a
# self-contained assertion.
#
# Covers:
#   T1  bare `shedman` lists subcommands (and does not list hidden _* helpers).
#   T2  `shedman help` is equivalent to bare `shedman`.
#   T3  `shedman help <cmd>` forwards to the subcommand's --help.
#   T4  `shedman <cmd> --help` is accepted.
#   T5  argv is preserved across dispatch (via a synthetic probe subcommand).
#   T6  unknown command → exit 2 + did-you-mean output.
#   T9  dispatcher `version` prints *something* (falls back to "unknown" off-system).
#   T10 a config file that is not there leaves the compiled defaults in charge.
#   T11 the shipped config file says exactly what those defaults say.
#   T12 an executable nobody declared is not listed and still runs.
#   T13 two declarations claiming one name is reported.

set -uo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$here/../.." && pwd)

dispatcher=$repo_root/tree/usr/bin/shedman
libexec=$repo_root/tree/usr/libexec/shedman

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

# ---------------------------------------------------------------------------
# Stage a /usr/libexec/shedman/ dir from the package tree so the dispatcher has
# the roster available. Also drop a probe subcommand for T5 (argv preservation).
# ---------------------------------------------------------------------------

stage=$(mktemp -d -t shedman-test.XXXXXX)
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage"
cp -a "$libexec"/. "$stage/"

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

# Declarations for the staged tree: the repo's, plus one for the probe. An
# extra executable is left undeclared on purpose — T12 is what it is for.
verbs=$(mktemp -d -t shedman-verbs.XXXXXX)
cp "$repo_root"/tree/usr/share/shedman/verbs.d/*.toml "$verbs/"
printf 'name = "_probe"\npackage = "harness"\n' > "$verbs/_probe.toml"

cp "$stage/_probe" "$stage/stowaway"

# The dispatcher reads where its verbs live and what has been declared out of
# its config file, so the stage is named there and the dispatcher itself runs
# exactly as shipped.
conf=$(mktemp -t shedman-conf.XXXXXX)
printf 'libexec = "%s"\nverbs = "%s"\n' "$stage" "$verbs" > "$conf"
export SHEDMAN_CONFIG=$conf
trap 'rm -rf "$stage" "$verbs" "$conf"' EXIT

# ---------------------------------------------------------------------------
# T1: bare `shedman` lists subcommands
# ---------------------------------------------------------------------------

out=$("$dispatcher" 2>&1) || true
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

out_help=$("$dispatcher" help 2>&1) || true
if [[ "$out" == "$out_help" ]]; then
    _ok T2_help_equals_bare
else
    _fail T2_help_equals_bare "bare vs. 'help' diverge"
fi

# ---------------------------------------------------------------------------
# T3: `shedman help <cmd>` forwards to --help
# ---------------------------------------------------------------------------

out_help_apply=$("$dispatcher" help apply 2>&1) || true
out_apply_help=$("$dispatcher" apply --help 2>&1) || true
if [[ "$out_help_apply" == "$out_apply_help" && -n "$out_help_apply" ]]; then
    _ok T3_help_cmd_forwards
else
    _fail T3_help_cmd_forwards "'help apply' differs from 'apply --help'"
fi

# ---------------------------------------------------------------------------
# T4: `shedman <cmd> --help` works
# ---------------------------------------------------------------------------

if "$dispatcher" apply --help >/dev/null 2>&1; then
    _ok T4_cmd_help_ok
else
    _fail T4_cmd_help_ok "'apply --help' failed"
fi

# ---------------------------------------------------------------------------
# T5: argv preservation via the probe
# ---------------------------------------------------------------------------

out_probe=$("$dispatcher" _probe hello 'a b c' --flag=value 2>&1) || true
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

out_unknown=$("$dispatcher" updat 2>&1)
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
# T9: `shedman version` prints something. Off-system this should fall back
# to "unknown" (pacman -Qi shedman fails); on-system it should print
# a version string.
# ---------------------------------------------------------------------------

out_version=$("$dispatcher" version 2>&1) || true
if [[ -n $out_version ]]; then
    _ok T9_version_prints
else
    _fail T9_version_prints "empty output"
fi

# ---------------------------------------------------------------------------
# T10: no config file — the compiled defaults answer and nothing breaks.
# ---------------------------------------------------------------------------

out_nocfg=$(SHEDMAN_CONFIG=$stage/not-a-file "$dispatcher" version 2>&1)
if [[ -n $out_nocfg ]]; then
    _ok T10_missing_config_falls_back
else
    _fail T10_missing_config_falls_back "empty output"
fi

# ---------------------------------------------------------------------------
# T11: the shipped file and the compiled defaults agree, which is what makes
# the config layer behaviour-preserving.
# ---------------------------------------------------------------------------

shipped=$repo_root/tree/etc/shedman/shedman.toml
for key in libexec package; do
    want=$(sed -n "s/^$key = \"\(.*\)\"$/\1/p" "$shipped")
    got=$(sed -n "s/.*_config $key \([^)]*\))/\1/p" "$dispatcher" | head -1)
    if [[ -n $want && $want == "$got" ]]; then
        _ok "T11_default_$key"
    else
        _fail "T11_default_$key" "shipped '$want' but the dispatcher defaults to '$got'"
    fi
done

# ---------------------------------------------------------------------------
# T12: an undeclared executable stays out of the listing and out of the
# suggestions, and still dispatches — the dispatcher does not break a machine
# over metadata.
# ---------------------------------------------------------------------------

if ! grep -q 'stowaway' <<<"$out" \
        && ! grep -q 'stowaway' <<<"$("$dispatcher" stoway 2>&1)"; then
    _ok T12_undeclared_not_listed
else
    _fail T12_undeclared_not_listed "an undeclared executable was advertised"
fi

if "$dispatcher" stowaway >/dev/null 2>&1; then
    _ok T12b_undeclared_still_runs
else
    _fail T12b_undeclared_still_runs "an undeclared executable stopped working"
fi

# ---------------------------------------------------------------------------
# T13: two declarations claiming one name is an error, because no install
# order settles which package owns the verb.
# ---------------------------------------------------------------------------

sed 's/package = "shedman"/package = "other"/' "$verbs/update.toml" \
    > "$verbs/also-update.toml"
err=$("$dispatcher" 2>&1 >/dev/null)
rm -f "$verbs/also-update.toml"
if grep -q 'update is declared by both other and shedman' <<<"$err"; then
    _ok T13_collision_reported
else
    _fail T13_collision_reported "got: $err"
fi

# ---------------------------------------------------------------------------
# T14: the four channel keys say what the fence library already believes.
# The library ships with shedos-system and compiles today's channels in as
# defaults, so the shipped config file and a box with no config file have to
# render the same block — the same agreement T11 asks of the dispatcher, for
# the keys whose reader lives in another package.
# ---------------------------------------------------------------------------

fence=${SHEDOS_APPLY_PACMAN_FENCE:-/usr/lib/shedos/pacman-fence}
if [[ ! -f $fence ]]; then
    _fail T14_channel_keys_agree "$fence is not installed"
else
    for channel in stable canary; do
        if [[ "$(SHEDMAN_CONFIG=$shipped bash "$fence" render $channel)" \
              == "$(SHEDMAN_CONFIG=/nonexistent bash "$fence" render $channel)" ]]; then
            _ok "T14_channel_keys_agree_$channel"
        else
            _fail "T14_channel_keys_agree_$channel" \
                "$(diff <(SHEDMAN_CONFIG=$shipped bash "$fence" render $channel) \
                        <(SHEDMAN_CONFIG=/nonexistent bash "$fence" render $channel))"
        fi
    done
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

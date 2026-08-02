#!/usr/bin/env bash
# run.sh — test harness for shedman's bash + zsh completion files.
#
# Tests that each completion surface:
#   * enumerates subcommands at position 1 (minus `_*` internals)
#   * completes `shedman help <tab>` with subcommands
#   * delegates to `shedman <cmd> --complete-bash|zsh` when honored
#   * falls back to filename completion otherwise
#
# Every check builds a disposable libexec stage dir with a handful of
# stub subcommands, rewrites the hardcoded `libexec=/usr/libexec/shedman`
# line in the completion file to point at the stage, and sources it in
# a subshell. For bash we invoke `_shedman` with a simulated COMP_WORDS
# and read COMPREPLY. zsh uses a scripted completion driver (`compinit`
# + `compdef` + programmatic invocation) that's brittle across zsh
# versions, so we instead syntax-check the file with `zsh -n` and trust
# the runtime glob + --complete-zsh contract (covered by the bash
# harness, which exercises the same contract).
#
# Exit: 0 all pass, 1 any failure.

set -uo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$here/../.." && pwd)
bash_file=$repo_root/packaging/shedos-system/tree/usr/share/bash-completion/completions/shedman
zsh_file=$repo_root/packaging/shedos-system/tree/usr/share/zsh/site-functions/_shedman

if [[ ! -f $bash_file ]]; then
    echo "FATAL: $bash_file missing" >&2
    exit 2
fi
if [[ ! -f $zsh_file ]]; then
    echo "FATAL: $zsh_file missing" >&2
    exit 2
fi

pass=0
fail=0
failures=()

_ok() { echo "ok: $1"; ((pass++)); }
_fail() {
    local name=$1; shift
    echo "FAIL: $name: $*" >&2
    failures+=("$name")
    ((fail++))
}

# ---------------------------------------------------------------------------
# Stage a libexec dir with stub subcommands. `cmd-a` honors
# --complete-bash (returns a fixed flag list); `cmd-b` does not.
# `_hidden` must never appear in a completion result.
# ---------------------------------------------------------------------------

stage=$(mktemp -d -t shedman-completion-test.XXXXXX)
trap 'rm -rf "$stage"' EXIT

cat >"$stage/cmd-a" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
    --help-summary) echo "alpha command (summary)" ;;
    --complete-bash|--complete-zsh)
        printf '%s\n' --alpha --beta --gamma ;;
    *) echo "cmd-a invoked" ;;
esac
EOF

cat >"$stage/cmd-b" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
    --help-summary) echo "beta command (summary)" ;;
    *) echo "cmd-b invoked" ;;
esac
EOF

cat >"$stage/_hidden" <<'EOF'
#!/usr/bin/env bash
echo "_hidden invoked"
EOF

chmod +x "$stage/cmd-a" "$stage/cmd-b" "$stage/_hidden"

# Rewrite the completion file's libexec path to point at the stage.
bash_wrapper=$(mktemp -t shedman-bash-comp.XXXXXX)
sed "s#^    libexec=/usr/libexec/shedman\$#    libexec=$stage#" \
    "$bash_file" >"$bash_wrapper"

# Helper: drive the bash completion function, print COMPREPLY lines.
# The completion file is self-contained (no _init_completion /
# _filedir deps), so all we need to do is source it and set the
# COMP_* vars the function reads.
_drive_bash() {
    local -a comp_words=( "$@" )
    local cword=$((${#comp_words[@]} - 1))
    (
        # shellcheck disable=SC1090
        source "$bash_wrapper"
        COMP_WORDS=( "${comp_words[@]}" )
        COMP_CWORD=$cword
        _shedman
        printf '%s\n' "${COMPREPLY[@]}"
    )
}

# ---------------------------------------------------------------------------
# Test block
# ---------------------------------------------------------------------------

# T1: position 1 lists stub subcommands + built-ins, hides `_hidden`.
out=$(_drive_bash shedman "")
if grep -qx 'cmd-a' <<<"$out" \
        && grep -qx 'cmd-b' <<<"$out" \
        && grep -qx 'help' <<<"$out" \
        && grep -qx 'version' <<<"$out" \
        && ! grep -qx '_hidden' <<<"$out"; then
    _ok T1_bash_position1_lists_subcommands
else
    _fail T1_bash_position1_lists_subcommands "got:\n$out"
fi

# T2: `shedman help <tab>` completes with subcommand names (no builtins).
out=$(_drive_bash shedman help "")
if grep -qx 'cmd-a' <<<"$out" \
        && grep -qx 'cmd-b' <<<"$out" \
        && ! grep -qx '_hidden' <<<"$out"; then
    _ok T2_bash_help_completes_subcommands
else
    _fail T2_bash_help_completes_subcommands "got:\n$out"
fi

# T3: `shedman cmd-a <tab>` uses the stub's --complete-bash output.
out=$(_drive_bash shedman cmd-a "")
if grep -qx -- '--alpha' <<<"$out" \
        && grep -qx -- '--beta' <<<"$out" \
        && grep -qx -- '--gamma' <<<"$out"; then
    _ok T3_bash_delegates_to_stub_complete
else
    _fail T3_bash_delegates_to_stub_complete "got:\n$out"
fi

# T4: `shedman cmd-a --a<tab>` narrows to --alpha.
out=$(_drive_bash shedman cmd-a "--a")
if grep -qx -- '--alpha' <<<"$out" && ! grep -qx -- '--beta' <<<"$out"; then
    _ok T4_bash_flag_prefix_filter
else
    _fail T4_bash_flag_prefix_filter "got:\n$out"
fi

# T5: Every completion-enabled real subcommand produces a non-empty
#     flag list via --complete-bash.
# T5b: Each of those subcommands emits at least one single-letter short
#      flag, so `shedman <cmd> -<tab>` works alongside long flags.
for cmd in update apply doctor rollback; do
    real=$repo_root/packaging/shedos-system/tree/usr/libexec/shedman/$cmd
    if [[ ! -x $real ]]; then
        _fail T5_real_"$cmd" "missing binary"
        continue
    fi
    out=$("$real" --complete-bash 2>/dev/null || true)
    if [[ -z $out ]]; then
        _fail T5_real_"$cmd" "--complete-bash emitted nothing"
    else
        _ok T5_real_"$cmd"
    fi
    if grep -Eq '^-[a-zA-Z]$' <<<"$out"; then
        _ok T5b_shorts_"$cmd"
    else
        _fail T5b_shorts_"$cmd" "no single-letter short in: $out"
    fi
done

# T6: zsh completion file parses cleanly under `zsh -n`.
if command -v zsh >/dev/null 2>&1; then
    if zsh -n "$zsh_file" 2>/dev/null; then
        _ok T6_zsh_file_parses
    else
        _fail T6_zsh_file_parses "zsh -n failed"
    fi
else
    echo "skip T6_zsh_file_parses: zsh not installed"
fi

# T7: bash completion file parses cleanly under `bash -n`.
if bash -n "$bash_file" 2>/dev/null; then
    _ok T7_bash_file_parses
else
    _fail T7_bash_file_parses "bash -n failed"
fi

# T8: fish completion file parses cleanly under `fish -n`.
fish_file=$repo_root/packaging/shedos-system/tree/usr/share/fish/vendor_completions.d/shedman.fish
if [[ ! -f $fish_file ]]; then
    _fail T8_fish_file_present "fish completion file missing: $fish_file"
elif command -v fish >/dev/null 2>&1; then
    if fish -n "$fish_file" 2>/dev/null; then
        _ok T8_fish_file_parses
    else
        _fail T8_fish_file_parses "fish -n failed"
    fi
else
    echo "skip T8_fish_file_parses: fish not installed"
fi

# T9: every opt-in subcommand emits non-empty output for --complete-fish.
for cmd in update apply doctor rollback; do
    real=$repo_root/packaging/shedos-system/tree/usr/libexec/shedman/$cmd
    if [[ ! -x $real ]]; then
        _fail T9_fish_"$cmd" "missing binary"
        continue
    fi
    out=$("$real" --complete-fish 2>/dev/null || true)
    if [[ -z $out ]]; then
        _fail T9_fish_"$cmd" "--complete-fish emitted nothing"
    else
        _ok T9_fish_"$cmd"
    fi
done

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

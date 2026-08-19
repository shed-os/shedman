#!/usr/bin/env bash
# run.sh — contract smoke tests over every verb this package ships. Asserts
# the cheap invariants every subcommand must honor:
#   - --help-summary prints one nonempty line, exit 0
#   - -h/--help exits 0 and mentions usage
#   - completion contract answers exit 0 and never hang
# Deeper behavior (TUIs, GUIs, privileged paths) belongs to dedicated
# suites; this keeps the dispatcher surface from silently rotting.
#
# The roster comes off disk rather than out of a list here. A list named two
# of the nineteen verbs and `health` was not one of them, so the one verb
# whose --help said neither "usage" nor "shedman" was never asked, and it
# surfaced on a booted ISO instead.

set -uo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$here/../.." && pwd)
# datetime (themed TUI) imports shedos_palette at module load, and that module
# ships with another package, so SHEDOS_LIB_ROOT is left at its default and the
# installed copy is what answers.

pass=0 fail=0
failures=()
_ok()   { echo "ok: $1"; ((pass++)); }
_fail() { echo "FAIL: $1: $2" >&2; failures+=("$1"); ((fail++)); }

LIBEXEC=$repo_root/tree/usr/libexec/shedman
# Regular files only: a python verb run from this tree leaves a __pycache__
# directory beside itself, and the package ships neither it nor any directory.
TOOLS=()
while IFS= read -r tool; do
    TOOLS+=("$(basename "$tool")")
done < <(find "$LIBEXEC" -maxdepth 1 -type f | LC_ALL=C sort)

if (( ${#TOOLS[@]} == 0 )); then
    echo "FATAL: no verbs under $LIBEXEC" >&2
    exit 1
fi

for name in "${TOOLS[@]}"; do
    path=$LIBEXEC/$name
    if [[ ! -x $path ]]; then
        _fail "${name}_exists" "missing or not executable: $path"
        continue
    fi

    out=$(timeout 10 "$path" --help-summary 2>&1); rc=$?
    if (( rc == 0 )) && [[ -n ${out//[[:space:]]/} ]] && (( $(wc -l <<<"$out") == 1 )); then
        _ok "${name}_help_summary"
    else
        _fail "${name}_help_summary" "rc=$rc out=$out"
    fi

    out=$(timeout 10 "$path" --help 2>&1); rc=$?
    if (( rc == 0 )) && grep -qiE 'usage|shedman' <<<"$out"; then
        _ok "${name}_help"
    else
        _fail "${name}_help" "rc=$rc out=${out:0:120}"
    fi

    # An internal helper is never offered by the completers, so nothing asks
    # it what it completes with and it is free to refuse the question.
    [[ $name == _* ]] && continue

    for mode in --complete-bash --complete-zsh --complete-fish; do
        timeout 10 "$path" "$mode" >/dev/null 2>&1; rc=$?
        if (( rc == 0 )); then
            _ok "${name}${mode//-/_}"
        else
            _fail "${name}${mode//-/_}" "rc=$rc"
        fi
    done
done

echo
echo "Summary: $pass passed, $fail failed"
(( fail == 0 )) || { printf '  %s\n' "${failures[@]}"; exit 1; }
exit 0

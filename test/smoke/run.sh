#!/usr/bin/env bash
# run.sh — contract smoke tests for the shedman verbs without a dedicated
# suite (datetime, db). Asserts the cheap invariants every subcommand must
# honor:
#   - --help-summary prints one nonempty line, exit 0
#   - -h/--help exits 0 and mentions usage
#   - completion contract answers exit 0 and never hang
# Deeper behavior (TUIs, GUIs, privileged paths) belongs to dedicated
# suites; this keeps the dispatcher surface from silently rotting.

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

TOOLS=(
    tree/usr/libexec/shedman/datetime
    tree/usr/libexec/shedman/db
)

for tool in "${TOOLS[@]}"; do
    name=$(basename "$tool")
    path=$repo_root/$tool
    if [[ ! -x $path ]]; then
        _fail "${name}_exists" "missing or not executable: $tool"
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

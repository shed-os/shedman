#!/usr/bin/env bash
# run.sh — unit tests for diff_words() in _config-review.
#
# Each fixture under fixtures/<name>/ has:
#   old              left-hand string (no trailing newline appended)
#   new              right-hand string
#   expected.json    {"old": [[kind, text], ...], "new": [[kind, text], ...]}
#                    where kind is "equal" | "removed" | "added".
#
# Usage: test/word-matcher/run.sh [fixture-name ...]
# Exit:  0 all pass, 1 any failure.

set -uo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$here/../.." && pwd)
tool=$repo_root/tree/usr/libexec/shedman/_config-review

if [[ ! -x $tool ]]; then
    echo "FATAL: $tool not executable" >&2
    exit 2
fi

if (( $# > 0 )); then
    fixtures=("$@")
else
    fixtures=()
    while IFS= read -r -d '' d; do
        fixtures+=("$(basename "$d")")
    done < <(find "$here/fixtures" -mindepth 1 -maxdepth 1 -type d -print0 | sort -z)
fi

pass=0
fail=0
failures=()

_run_one() {
    local name=$1
    local fdir=$here/fixtures/$name
    [[ -d $fdir ]] || { echo "skip $name (no such fixture)"; return; }
    [[ -f $fdir/old && -f $fdir/new && -f $fdir/expected.json ]] || {
        echo "skip $name (missing old/new/expected.json)"; return
    }

    local actual
    actual=$(python3 -c "
import json, sys, runpy
ns = runpy.run_path('$tool', run_name='__diff_words_test__')
old = open('$fdir/old').read()
new = open('$fdir/new').read()
o, n = ns['diff_words'](old, new)
print(json.dumps({'old': [[k, t] for k, t in o], 'new': [[k, t] for k, t in n]}, ensure_ascii=False))
" 2>&1)
    local rc=$?
    if (( rc != 0 )); then
        echo "FAIL $name: diff_words raised"
        printf '    %s\n' "$actual"
        failures+=("$name")
        ((fail++))
        return
    fi

    if ! diff -u <(python3 -c "import json,sys; print(json.dumps(json.load(open('$fdir/expected.json')), ensure_ascii=False))") \
                 <(echo "$actual") > /dev/null 2>&1; then
        echo "FAIL $name: output mismatch"
        echo "  expected: $(cat $fdir/expected.json)"
        echo "  got:      $actual"
        failures+=("$name")
        ((fail++))
        return
    fi

    echo "PASS $name"
    ((pass++))
}

for f in "${fixtures[@]}"; do
    _run_one "$f"
done

echo
echo "Summary: $pass passed, $fail failed"
if (( fail > 0 )); then
    printf '  %s\n' "${failures[@]}"
    exit 1
fi
exit 0

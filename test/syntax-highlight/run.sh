#!/usr/bin/env bash
# run.sh — tests for _lexer_for() + syntax_spans().
#
# Two test forms:
#
# 1) Lexer-resolution test (fixtures/<name>/lexer/):
#      relpath          one-line filename or path to resolve
#      expected         expected lexer class name (e.g. "BashLexer",
#                       "ShedConfLexer", or "None")
#
# 2) Span test (fixtures/<name>/span/):
#      relpath          file path (drives lexer choice)
#      input            text to tokenise
#      expected.json    list of [palette_key, text] pairs, in order
#
# Usage: test/syntax-highlight/run.sh [fixture-name ...]

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

_run_lexer() {
    local name=$1 fdir=$2
    local relpath expected actual
    relpath=$(< "$fdir/relpath")
    expected=$(< "$fdir/expected")
    actual=$(python3 -c "
import runpy
ns = runpy.run_path('$tool', run_name='__syntax_test__')
lx = ns['_lexer_for']('$relpath'.strip())
print(type(lx).__name__ if lx is not None else 'None')
" 2>&1)
    if [[ "$(echo "$actual" | tr -d '[:space:]')" != "$(echo "$expected" | tr -d '[:space:]')" ]]; then
        echo "FAIL $name: lexer mismatch"
        echo "  relpath:  $relpath"
        echo "  expected: $expected"
        echo "  got:      $actual"
        failures+=("$name"); ((fail++)); return
    fi
    echo "PASS $name"; ((pass++))
}

_run_span() {
    local name=$1 fdir=$2
    local relpath actual
    relpath=$(< "$fdir/relpath")
    actual=$(python3 -c "
import json, runpy
ns = runpy.run_path('$tool', run_name='__syntax_test__')
lx = ns['_lexer_for']('$relpath'.strip())
if lx is None:
    print(json.dumps([['_no_lexer', '']], ensure_ascii=False))
else:
    text = open('$fdir/input').read()
    spans = ns['syntax_spans'](text, lx)
    print(json.dumps([[k, t] for k, t in spans], ensure_ascii=False))
" 2>&1)
    local rc=$?
    if (( rc != 0 )); then
        echo "FAIL $name: syntax_spans raised"
        printf '    %s\n' "$actual"
        failures+=("$name"); ((fail++)); return
    fi
    # Normalize both via json round-trip for stable comparison.
    if ! diff -u <(python3 -c "import json; print(json.dumps(json.load(open('$fdir/expected.json')), ensure_ascii=False))") \
                 <(echo "$actual") > /dev/null 2>&1; then
        echo "FAIL $name: output mismatch"
        echo "  expected: $(cat $fdir/expected.json)"
        echo "  got:      $actual"
        failures+=("$name"); ((fail++)); return
    fi
    echo "PASS $name"; ((pass++))
}

_run_one() {
    local name=$1
    local fdir=$here/fixtures/$name
    [[ -d $fdir ]] || { echo "skip $name (no such fixture)"; return; }
    if [[ -d $fdir/lexer ]]; then
        _run_lexer "$name" "$fdir/lexer"
    elif [[ -d $fdir/span ]]; then
        _run_span "$name" "$fdir/span"
    else
        echo "skip $name (no lexer/ or span/ subdir)"
    fi
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

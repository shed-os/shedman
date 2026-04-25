#!/usr/bin/env bash
# run.sh — sanity tests for the shedman man pages.
#
# Each page must:
#   * exist on disk
#   * render under `man -l` without error
#   * contain the canonical `NAME`, `SYNOPSIS`, and `DESCRIPTION`
#     section headers (in groff: .SH NAME / .SH SYNOPSIS / .SH
#     DESCRIPTION)
#
# Each subcommand page additionally must:
#   * cross-reference shedman(1) in its `SEE ALSO` section.

set -uo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$here/../.." && pwd)
mandir=$repo_root/packaging/shedos-system/tree/usr/share/man/man1

PAGES=(
    shedman.1
    shedman-update.1
    shedman-apply.1
    shedman-doctor.1
    shedman-rollback.1
    shedman-config.1
    shedman-status.1
)

pass=0
fail=0
failures=()

_ok()   { echo "ok: $1"; ((pass++)); }
_fail() { echo "FAIL: $1: $2" >&2; failures+=("$1"); ((fail++)); }

for p in "${PAGES[@]}"; do
    src=$mandir/$p
    if [[ ! -f $src ]]; then
        _fail "exists_$p" "missing: $src"
        continue
    fi
    _ok "exists_$p"

    # Section headers.
    for sec in NAME SYNOPSIS DESCRIPTION; do
        if grep -q "^\.SH $sec" "$src"; then
            _ok "section_${sec}_$p"
        else
            _fail "section_${sec}_$p" "no .SH $sec in $src"
        fi
    done

    # Render via `man -l`. Exits 0 on success; warnings written to
    # stderr don't fail the page (man is forgiving).
    if command -v man >/dev/null 2>&1; then
        if man -P cat -l "$src" >/dev/null 2>&1; then
            _ok "renders_$p"
        else
            _fail "renders_$p" "man -l failed"
        fi
    else
        echo "skip renders_$p: man not installed"
    fi

    # Subcommand pages cross-reference shedman(1).
    if [[ $p != shedman.1 ]]; then
        if grep -q 'shedman (1)\|shedman(1)' "$src"; then
            _ok "xref_$p"
        else
            _fail "xref_$p" "no shedman(1) cross-reference"
        fi
    fi
done

echo
total=$((pass + fail))
if (( fail == 0 )); then
    echo "PASS $pass/$total"
    exit 0
fi
echo "FAIL $fail/$total ($pass passed)"
for f in "${failures[@]}"; do echo "  - $f"; done
exit 1

#!/usr/bin/env bash
# run.sh — sanity tests for the shedman man pages.
#
# Source of truth lives at packaging/shedos-system/man/*.scd.
# At build time PKGBUILD's prepare() renders these via scdoc into
# /usr/share/man/man1/*.1. This harness mirrors the same render
# step at test time, then validates that each rendered page:
#
#   * was emitted by scdoc without error
#   * renders under `man -l` without error
#   * contains the canonical NAME, SYNOPSIS, and DESCRIPTION
#     section headers (.SH NAME / .SH SYNOPSIS / .SH DESCRIPTION)
#
# Each subcommand page additionally must:
#   * cross-reference shedman(1) in its SEE ALSO section.

set -uo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$here/../.." && pwd)
srcdir=$repo_root/packaging/shedos-system/man

PAGES=(
    shedman.1
    shedman-update.1
    shedman-apply.1
    shedman-doctor.1
    shedman-rollback.1
    shedman-config.1
    shedman-status.1
    shedman-secureboot.1
    shedman-tpm2.1
    shedman-key.1
)

# Skip gracefully if scdoc isn't installed (mirrors the T6 zsh -n
# pattern in test/completions/run.sh). Hard rule: never silently
# pretend the test ran.
if ! command -v scdoc >/dev/null 2>&1; then
    echo "skip: scdoc not installed; cannot render .scd man-page sources"
    exit 0
fi

mandir=$(mktemp -d -t shedos-man.XXXXXX)
trap 'rm -rf -- "$mandir"' EXIT

pass=0
fail=0
failures=()

_ok()   { echo "ok: $1"; ((pass++)); }
_fail() { echo "FAIL: $1: $2" >&2; failures+=("$1"); ((fail++)); }

for p in "${PAGES[@]}"; do
    src=$srcdir/$p.scd
    rendered=$mandir/$p

    if [[ ! -f $src ]]; then
        _fail "source_$p" "scdoc source missing: $src"
        continue
    fi
    _ok "source_$p"

    # Render. scdoc emits to stdout; capture into the rendered path.
    if scdoc < "$src" > "$rendered" 2>/dev/null; then
        _ok "renders_scdoc_$p"
    else
        _fail "renders_scdoc_$p" "scdoc failed to render $src"
        continue
    fi

    # Section headers (post-render groff).
    for sec in NAME SYNOPSIS DESCRIPTION; do
        if grep -q "^\.SH $sec" "$rendered"; then
            _ok "section_${sec}_$p"
        else
            _fail "section_${sec}_$p" "no .SH $sec in rendered $p"
        fi
    done

    # Render via `man -l`. Exits 0 on success; warnings written to
    # stderr don't fail the page (man is forgiving).
    if command -v man >/dev/null 2>&1; then
        if man -P cat -l "$rendered" >/dev/null 2>&1; then
            _ok "renders_man_$p"
        else
            _fail "renders_man_$p" "man -l failed on rendered $p"
        fi
    else
        echo "skip renders_man_$p: man not installed"
    fi

    # Subcommand pages cross-reference shedman(1) in SEE ALSO. The
    # cross-reference lives in the .scd source (rendered as bold via
    # scdoc); grep the source rather than the rendered output to
    # avoid groff escape sequence noise.
    if [[ $p != shedman.1 ]]; then
        if grep -q '\*shedman\*(1)' "$src"; then
            _ok "xref_$p"
        else
            _fail "xref_$p" "no shedman(1) cross-reference in $src"
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

# _stubs.sh — shared PATH-stub helpers for shedos-apply fixtures.
#
# Each `_stub_<binary> <stubdir> [<fixture-dir>]` writes an executable
# at $stubdir/<binary>. The stub:
#   * Responds to query subcommands by emitting fixture content (so a
#     test can pin "ufw status numbered" output).
#   * Logs every mutation invocation, one argv per line, to
#     $stubdir/../<binary>.log so the fixture can assert which calls
#     happened.
#
# Buckets that grow new stubs (pacman-key, useradd, etc.) add a new
# `_stub_<binary>` here so the call sites stay one-liners.

# ---------------------------------------------------------------------------
# _stub_ufw
#
# Reads $FIXDIR/ufw-status-numbered.txt (if present) for `ufw status
# numbered`. Treats `--force enable`/`disable`/`reset` as state
# mutations and logs them. `default <policy> <chain>`, `delete <N>`,
# and `<action> <args...>` (allow/deny/reject/limit) are also logged.
# ---------------------------------------------------------------------------
_stub_ufw() {
    local stubdir=$1
    local fixdir=${2:-}
    local logfile="$stubdir/../ufw.log"
    cat >"$stubdir/ufw" <<STUB
#!/usr/bin/env bash
logfile=$logfile
numbered_file=${fixdir:+$fixdir/ufw-status-numbered.txt}
verbose_file=${fixdir:+$fixdir/ufw-status-verbose.txt}

args=("\$@")
if [[ "\${args[0]:-}" == "--force" ]]; then
    args=("\${args[@]:1}")
fi

case "\${args[0]:-}" in
    status)
        sub="\${args[1]:-}"
        case "\$sub" in
            numbered)
                if [[ -n "\$numbered_file" && -f "\$numbered_file" ]]; then
                    cat "\$numbered_file"
                else
                    printf 'Status: inactive\n'
                fi
                ;;
            verbose)
                if [[ -n "\$verbose_file" && -f "\$verbose_file" ]]; then
                    cat "\$verbose_file"
                elif [[ -n "\$numbered_file" && -f "\$numbered_file" ]]; then
                    # If only the numbered fixture is given, derive a
                    # minimal verbose response from its first line.
                    first=\$(head -n1 "\$numbered_file")
                    if [[ "\$first" =~ active ]]; then
                        printf 'Status: active\nLogging: on (low)\nDefault: deny (incoming), allow (outgoing), disabled (routed)\n'
                    else
                        printf 'Status: inactive\n'
                    fi
                else
                    printf 'Status: inactive\n'
                fi
                ;;
            *)
                if [[ -n "\$numbered_file" && -f "\$numbered_file" ]]; then
                    cat "\$numbered_file"
                else
                    printf 'Status: inactive\n'
                fi
                ;;
        esac
        exit 0
        ;;
esac

# Mutation — record one argv per line, exit 0.
printf '%s\n' "\${args[*]}" >>"\$logfile"
exit 0
STUB
    chmod +x "$stubdir/ufw"
    : >"$logfile"
}

# ---------------------------------------------------------------------------
# _stub_pacman_key
#
# Reads $FIXDIR/pacman-key-list-keys.txt for `--list-keys --with-colons`
# (full GPG colon format expected). `--lsign-key <fp>` is logged as a
# mutation. A non-zero exit can be requested for a specific fingerprint
# via $FIXDIR/pacman-key-lsign-fail.txt (one fp per line).
# ---------------------------------------------------------------------------
_stub_pacman_key() {
    local stubdir=$1
    local fixdir=${2:-}
    local logfile="$stubdir/../pacman-key.log"
    cat >"$stubdir/pacman-key" <<STUB
#!/usr/bin/env bash
logfile=$logfile
list_file=${fixdir:+$fixdir/pacman-key-list-keys.txt}
fail_file=${fixdir:+$fixdir/pacman-key-lsign-fail.txt}

case "\${1:-}" in
    --list-keys)
        # Emit fixture content (or empty for "no keys trusted").
        if [[ -n "\$list_file" && -f "\$list_file" ]]; then
            cat "\$list_file"
        fi
        exit 0
        ;;
    --lsign-key)
        fp="\${2:-}"
        printf '%s\n' "--lsign-key \$fp" >>"\$logfile"
        if [[ -n "\$fail_file" && -f "\$fail_file" ]]; then
            if grep -qx "\$fp" "\$fail_file"; then
                printf 'gpg: %s: signing failed: No public key\n' "\$fp" >&2
                exit 2
            fi
        fi
        exit 0
        ;;
esac

printf '%s\n' "\$*" >>"\$logfile"
exit 0
STUB
    chmod +x "$stubdir/pacman-key"
    : >"$logfile"
}

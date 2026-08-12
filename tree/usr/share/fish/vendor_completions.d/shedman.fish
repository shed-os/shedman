# fish completion for the shedman unified CLI.
#
# Subcommand list is read at completion time from the verb declarations
# /etc/shedman/shedman.toml points at; same pattern as the bash + zsh
# completers. Names prefixed with `_` (internal helpers) are hidden.
#
# Per-subcommand flag completion defers to `shedman <cmd>
# --complete-fish` if the subcommand honors it. Other subcommands fall
# back to filename completion.
#
# This file is structurally simple; fish's completion API is
# designed for one-line `complete -c` directives, so we don't need
# a function dispatch like bash/zsh.

# ---------------------------------------------------------------------------
# Subcommand discovery (position 1).
# ---------------------------------------------------------------------------

function __shedman_config
    # One `key = "value"` line out of shedman's config file, or the default
    # when the file is absent, unreadable or silent about the key.
    set -l conf $SHEDMAN_CONFIG
    test -n "$conf"; or set conf /etc/shedman/shedman.toml
    set -l value (sed -n "s/^[ \t]*$argv[1][ \t]*=[ \t]*\"\\([^\"]*\\)\".*/\\1/p" \
        "$conf" 2>/dev/null | head -1)
    test -n "$value"; and echo $value; or echo $argv[2]
end

function __shedman_verbs
    # find rather than a glob: fish reports a wildcard that matches nothing
    # instead of expanding to nothing, and a box with no declarations yet is
    # not an error worth printing at every tab press.
    set -l dir (__shedman_config verbs /usr/share/shedman/verbs.d)
    find $dir -maxdepth 1 -name '*.toml' -exec \
        sed -n 's/^[ \t]*name[ \t]*=[ \t]*"\([^_"][^"]*\)".*/\1/p' {} + 2>/dev/null \
        | env LC_ALL=C sort -u
end

function __shedman_subcommands
    __shedman_verbs
    echo help
    echo version
end

complete -c shedman -n '__fish_use_subcommand' \
    -xa '(__shedman_subcommands)'

# ---------------------------------------------------------------------------
# `shedman help <tab>` completes with subcommand names.
# ---------------------------------------------------------------------------

complete -c shedman -n '__fish_seen_subcommand_from help' \
    -xa '(__shedman_subcommands)'

# ---------------------------------------------------------------------------
# Per-subcommand flag completion. The subcommand emits its own flag
# vocabulary via --complete-fish (one flag per stdout line, longs and
# shorts mixed). Fall back to plain filename completion when a
# subcommand doesn't opt in.
# ---------------------------------------------------------------------------

function __shedman_subcmd_flags
    set tokens (commandline -opc)
    if test (count $tokens) -ge 2
        set sub $tokens[2]
        set bin (__shedman_config libexec /usr/libexec/shedman)/$sub
        if test -x "$bin"
            "$bin" --complete-fish 2>/dev/null
        end
    end
end

# Bind per-subcommand to the dynamic flag emitter. The declarations are the
# roster, so a verb another package ships binds itself.
for sub in (__shedman_verbs)
    complete -c shedman -n "__fish_seen_subcommand_from $sub" \
        -a '(__shedman_subcmd_flags)'
end

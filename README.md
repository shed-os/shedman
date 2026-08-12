# shedman

The package manager and system CLI for [ShedOS](https://shedos.org). `shedman
<verb>` installs and removes packages, drives the interactive upgrade,
reconciles the declared system state, reports health and status, browses logs
and rolls the machine back.

**Build-out in progress.** Production lives at
[Theshedman/shedos](https://github.com/Theshedman/shedos) until the multi-repo
cutover completes; this repo publishes to a staging channel.

## The verb contract

The dispatcher runs anything under `/usr/libexec/shedman/`, and any package may
put a verb there. What it may not do is put one there unannounced: a verb ships
a declaration at `/usr/share/shedman/verbs.d/<name>.toml` naming itself, the
package that owns it, its man page and a one-line description.

```toml
name = "update"
package = "shedman"
man = "shedman-update.1"
description = "interactive ShedOS upgrade (pacman + AUR + config sync)"
requires = ["migrate"]
```

A verb with no flags of its own says `completes = false` and answers the
completion contract with nothing. Silence has to be declared: otherwise a verb
that never read the flag reads exactly like one that has nothing to offer.

The dispatcher reads the declarations at startup. Two packages claiming one
name is an error rather than a race the last install wins; an executable with
no declaration is ignored and `shedman doctor` says whose it is. `shedman help`
and the three shell completions list what is declared.

A package shipping a verb declares `depends=(shedman)` and answers
`--help-summary` and `--complete-bash|zsh|fish`. The shared pipeline holds the
whole contract to a build-time check: every shipped executable declared, every
declaration backed by an executable and a man page, every visible verb
answering the completion contract.

## Configuration

`/etc/shedman/shedman.toml` holds what shedman would otherwise have hardcoded
about the OS it manages — where the verbs live and which package it is. It
ships with the values ShedOS uses, and the defaults compiled into the
dispatcher are the same ones, so a missing or unreadable file changes nothing.

## Tests

Every suite runs from the checkout with no root and no network:

```
for s in test/*/run.sh; do bash "$s"; done
```

[shed-os/shedos-ci](https://github.com/shed-os/shedos-ci) builds and tests this
repo and requests publication;
[shed-os/shedos-release](https://github.com/shed-os/shedos-release) signs and
publishes.

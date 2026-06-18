shedman(1) "ShedOS" "User Commands"

# NAME

shedman - unified ShedOS utility CLI

# SYNOPSIS

*shedman* [_command_] [_args ..._]++
*shedman* *help* [_command_]++
*shedman* *version*

# DESCRIPTION

*shedman* is the unified dispatcher for every utility shipped by
ShedOS. It discovers subcommands at runtime by globbing every
executable under /usr/libexec/shedman/ — each becomes
*shedman <name>*. Filenames prefixed with an underscore are internal
helpers, hidden from *shedman help* but still reachable.

The dispatcher itself only does argv preservation, name resolution,
and "did you mean" suggestions on unknown commands. All substantive
logic lives in the per-subcommand binaries.

# SUBCOMMANDS

The headline set:

*shedman update*
	Interactive system upgrade (pacman + yay + config sync), bracketed
	by a snapper pre/post snapshot pair. See *shedman-update*(1).

*shedman apply*
	Reconcile /etc/shedos/system.toml against live state. See
	*shedman-apply*(1).

*shedman doctor*
	Detect drift between /etc/shedos/system.toml and live state.
	Read-only. See *shedman-doctor*(1).

*shedman rollback*
	Restore the root subvolume from a snapper snapshot. See
	*shedman-rollback*(1).

*shedman config*
	Synchronise dotfiles and review merge conflicts. See
	*shedman-config*(1).

*shedman status*
	Aggregated dashboard across the four waybar signals (updates,
	conflicts, health, doctor). See *shedman-status*(1).

*shedman updates* / *conflicts* / *health*
	The waybar-backed signal binaries. Each prints a JSON payload
	with *--waybar* and a human-readable summary by default.

*shedman logs*
	Textual journalctl browser TUI.

*shedman install*
	Install one or more packages, routing pacman vs yay per-package
	by source.

*shedman uninstall*
	Remove installed packages and their now-unused dependency
	closure, routing pacman vs yay by installed source. See
	*shedman-uninstall*(1).

*shedman dock*
	Manage the ShedOS application dock — position, pinned apps,
	toggle visibility. See *shedman-dock*(1).

*shedman fingerprint*
	Manage user fingerprint enrollments and characterise the
	matcher's reliability. See *shedman-fingerprint*(1).

*shedman migrate*
	One-shot retrofit for installs dated before the April 2026
	packaging cut-over. With *--from-arch*, instead adopts an
	existing vanilla Arch install into ShedOS (Manjaro is
	refused). Idempotent — runs on a packaged install are a fast
	no-op.

*shedman snapshot*
	Manual btrfs checkpoint before risky changes; restore through
	*shedman rollback*. See *shedman-snapshot*(1).

*shedman db*
	Per-project PostgreSQL databases (create/drop/list) on the
	per-user setup. See *shedman-db*(1).

*shedman tour*
	Replay the first-run welcome tour overlay. See
	*shedos-tour*(1).

# BUILT-INS

*shedman* | *help* | *--help* | *-h*
	List installed subcommands with one-line summaries.

*shedman help <cmd>*
	Forward to *shedman <cmd> --help*.

*shedman version*
	Print the installed _shedos-system_ version.

# FILES

/etc/shedos/system.toml
	Declarative source of truth (applied via *shedman apply*).

/usr/libexec/shedman/
	Subcommand binaries. The dispatcher discovers them at runtime by
	listing this directory.

/usr/bin/shedos-\*
	Silent back-compat shims for legacy shedos-\* names.

/var/lib/shedos/
	Per-section last-applied + baseline state files.

# ENVIRONMENT

Many subcommands honour environment variables for testing — see
the per-command man pages.

# SEE ALSO

*shedman-update*(1), *shedman-apply*(1), *shedman-doctor*(1),
*shedman-rollback*(1), *shedman-snapshot*(1), *shedman-config*(1),
*shedman-status*(1), *shedman-fingerprint*(1), *shedman-uninstall*(1),
*shedman-install*(1), *shedman-services*(1), *shedman-health*(1),
*shedman-updates*(1), *shedman-conflicts*(1), *shedman-kernel*(1),
*shedman-logs*(1), *shedman-upgrade-history*(1), *shedman-db*(1),
*shedman-theme*(1), *shedman-lock*(1), *shedman-login*(1),
*shedman-dock*(1),
*pacman*(8), *yay*(8).

The web reference at https://shedos.org/docs/commands covers every
subcommand and flag with examples.

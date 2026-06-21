shedman-apply(1) "ShedOS" "User Commands"

# NAME

shedman-apply - reconcile /etc/shedos/system.toml against live state

# SYNOPSIS

*shedman apply* [*-c*|*--config* _PATH_] [*-y*|*--yes*] [*-n*|*--dry-run*] [*--validate*]

# DESCRIPTION

*shedman apply* parses /etc/shedos/system.toml and reconciles every
declared section against the live system. Reconcilers in scope:
*[systemd]* (unit enable/disable), *[drop-ins]* (/etc files),
*[snapper]* (timeline + cleanup), *[pacman.repos]* (/etc/pacman.conf
fence), *[services.postgresql]* (auto-init + per-user-db),
*[network.firewall]* (ufw), *[security.keyring]* (pacman-key trust),
*[fs.mounts]* (/etc/fstab fence), *[kernel.cmdline]* (kernel
command-line append tokens, baked into the signed boot image),
*[[users]] / [[groups]]* (additive only).

Each section can be edited via either the TOML file or the underlying
tool (e.g. *ufw allow*, *pacman-key --lsign-key*). Hand-made changes
via the raw tool are _adopted_ into the TOML on the next apply. The
TOML is the canonical persisted form; raw tool calls are a
convenience UI.

Install-time state (Calamares-created users, shedos-keyring's trust
chain, pre-existing fstab entries) is snapshotted to per-section
baseline files on first apply and permanently invisible to the
reconciler thereafter.

# OPTIONS

*-c* _PATH_, *--config* _PATH_
	Read declarative state from _PATH_ instead of
	/etc/shedos/system.toml.

*-y*, *--yes*
	Apply without the interactive y/N prompt.

*-n*, *--dry-run*
	Print the plan and exit. No mutations, no TOML adoption-write.

*--validate*
	Parse + schema-check the TOML; exit 0 if valid, 2 on any error.
	No plan built, no live access.

# FILES

/etc/shedos/system.toml
	Source of truth.

/usr/share/shedos/system.toml.example
	Fully-commented template covering every supported section.

/var/lib/shedos/
	Per-section state files (.state.json + .baseline.json) used by
	the bidirectional adoption merge.

# ENVIRONMENT

*SHEDOS_APPLY_ETC_ROOT*
	Override /etc.

*SHEDOS_APPLY_STATE_ROOT*
	Override /var/lib/shedos.

*SHEDOS_KERNEL_CMDLINE_FILE*
	Override /etc/kernel/cmdline (the *[kernel.cmdline]* surface; its
	tokens are baked into the signed boot image from there).

*SHEDOS_APPLY_BOOT_ROOT*
	Override /boot; also suppresses the boot-image re-sign under tests.

*SHEDOS_APPLY_UFW*, *SHEDOS_APPLY_PACMAN_KEY*, *SHEDOS_APPLY_SYSTEMCTL*, *SHEDOS_APPLY_FSTAB_PATH*
	Override the corresponding tool / file path for fixture-driven
	testing.

# EXIT STATUS

*0*
	Plan applied (or empty plan).

*1*
	Apply failed and was rolled back, or user aborted at the prompt.

*2*
	Schema or environment-shape error (TOML invalid, fstab malformed).

# SEE ALSO

*shedman*(1), *shedman-doctor*(1).

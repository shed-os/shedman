shedman-doctor(1) "ShedOS" "User Commands"

# NAME

shedman-doctor - read-only drift detector for /etc/shedos/system.toml

# SYNOPSIS

*shedman doctor* [*-c*|*--config* _PATH_]++
*shedman doctor* [*-j*|*--json*] | [*-d*|*--diff*] | [*-f*|*--fix*] | *--fix-mounts* [*-y*|*--yes*] | *--waybar* | *--tick* | *--notify-if-new* | *--reset-notify-state*

# DESCRIPTION

*shedman doctor* runs the same plan engine as *shedman-apply*(1)
but never mutates anything. It returns a Plan describing the
differences between /etc/shedos/system.toml and the live system.
Outputs vary by mode.

It runs fine as a regular user: planning writes no state, and
sections whose live side is root-only — the snapper config, ufw —
degrade to a *?* note instead of failing. Run as root for full
coverage. Notify hysteresis is per-invoker: root keeps it under
/var/lib/shedos, users under _$XDG_STATE_HOME/shedos_.

Alongside the drift report, *doctor* runs two boot-safety checks: it
flags non-root /etc/fstab mounts that lack *nofail* and could drop the
system to emergency mode if their disk is absent (fix with
*--fix-mounts*), and it surfaces any auto mount that silently failed to
mount on the last boot. See _docs/boot-safety.md_.

# OPTIONS

*-c* _PATH_, *--config* _PATH_
	Read declarative state from _PATH_ instead of
	/etc/shedos/system.toml.

default (no flag)
	Print a compact, colorized summary of every drift item.

*-j*, *--json*
	Emit a machine-readable JSON document suitable for the
	*custom/doctor* waybar module. Includes a stable _plan_hash_
	fingerprint and _mount_safety_ / _mount_missed_ arrays for the
	two boot-safety checks.

*-d*, *--diff*
	Render unified diffs for every drift entry. Useful before
	running *shedman apply*.

*-f*, *--fix*
	Delegate to *shedman apply --yes* and exit with whatever code
	apply returns. Safe shorthand for "I looked at the diff, just
	do it".

*--waybar*
	JSON shape consumed by waybar's *custom/doctor* module. Always
	exits 0 even on drift.

*--tick*
	Timer-driven entrypoint. Notify-if-new + refresh waybar; always
	exits 0.

*--notify-if-new*
	Fire a mako notification iff the drift fingerprint has changed
	since the last invocation.

*--reset-notify-state*
	Clear the hysteresis file. Use this if a notification got stuck.

*--fix-mounts*
	Add *nofail* (and a short device-timeout) to any non-root
	/etc/fstab mount that lacks it — the hand-added entries that can
	wedge boot. Prompts for confirmation and writes a timestamped
	_fstab.shedos-bak_ backup first. This repairs lines *outside* the
	managed fence, which *--fix* does not touch.

*-y*, *--yes*
	Assume yes for *--fix-mounts* (non-interactive).

# FILES

/etc/shedos/system.toml
	Source of truth (read-only).

/var/lib/shedos/doctor.state.json
	Notify hysteresis (last-notified drift and missed-disk fingerprints).

/var/lib/shedos/mount-missed.json
	Breadcrumb from *shedos-mount-report.service*: auto fstab mounts
	that did not mount on the last boot. Read-only input to doctor.

# ENVIRONMENT

Same overrides as *shedman-apply*(1): SHEDOS_APPLY_ETC_ROOT,
_STATE_ROOT, _SYSTEMCTL, _UFW, _PACMAN_KEY, _FSTAB_PATH,
_BOOT_ROOT.

# EXIT STATUS

*0*
	Aligned and boot-safe (default/diff/json); waybar, tick and
	notify modes always exit 0.

*1*
	Drift, an unsafe fstab mount, or a disk that did not mount this
	boot (default/diff/json); or *--fix* delegated to apply and it
	returned non-zero.

*2*
	Schema or environment-shape error.

# SEE ALSO

*shedman*(1), *shedman-apply*(1), *shedman-secureboot*(1).

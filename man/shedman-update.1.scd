shedman-update(1) "ShedOS" "User Commands"

# NAME

shedman-update - interactive ShedOS upgrade flow

# SYNOPSIS

*shedman update* [*-y*|*--yes*] [*-h*|*--help*]++
*shedman update* {*-l*|*--list-snapshots*}++
*shedman update* {*-r*|*--rollback*} [_N_]++
*shedman update* *--history*

# DESCRIPTION

*shedman update* is the primary upgrade command on ShedOS. It wraps
three steps in one interactive flow, bracketed by a snapper pre/post
btrfs snapshot pair:

. *pacman -Syu* — official repo updates.
. *yay -Sua* — AUR updates (if *yay* is installed).
. *shedman config --sync* — 3-way merge of dotfiles in $HOME against
  packaged defaults.

Every step is gated by an explicit y/N prompt; nothing destructive
runs without confirmation. The snapper snapshots make every upgrade
reversible via *shedman rollback* or *shedman update --rollback*.

# OPTIONS

*-y*, *--yes*
	Unattended mode. Pass *--noconfirm* to pacman/yay and *--yes* to
	*shedman config --sync*. Conflicts still drop _.shedosnew_ files
	instead of overwriting your edits — there is no silent data
	loss path.

*-l*, *--list-snapshots*
	Print the recent snapper snapshot list and exit. Use this to
	find a pre-upgrade snapshot number for *--rollback*.

*-r* _N_, *--rollback* _N_
	Roll back the root subvolume to snapshot _N_. If _N_ is omitted,
	you'll be prompted from the recent list. Reboot is required
	after the call returns.

*--history*
	Open the upgrade-history TUI (delegates to *shedman-upgrade-history*(1)
	— an internal helper).

*-h*, *--help*
	Print usage and exit.

# FILES

/etc/shedos/system.toml
	Read by *shedman config --sync* during the post-upgrade dotfile
	pass.

/var/lib/shedos/
	Last-seen + baseline state for the dotfile sync.

# SEE ALSO

*shedman*(1), *shedman-config*(1), *shedman-rollback*(1),
*pacman*(8), *yay*(8).

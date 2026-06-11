shedman-rollback(1) "ShedOS" "User Commands"

# NAME

shedman-rollback - restore root subvolume from a snapper snapshot

# SYNOPSIS

*sudo shedman rollback* [*-y*|*--yes*] _N_++
*sudo shedman rollback* {*-l*|*--list*}++
*sudo shedman rollback* {*-u*|*--undo*}

# DESCRIPTION

*shedman rollback* restores the *@* btrfs subvolume from a snapper
snapshot. Requires root. Reboot is required for the rollback to
take effect.

The model:

. The current *@* is renamed to @.rollback-_<timestamp>_ (safety
  net; nothing is deleted).
. A new *@* is snapshotted from the chosen snapper snapshot.
. On reboot, the new *@* is mounted at /.

*@home*, *@log*, *@cache*, *@pkg*, and the other data-carrying
subvolumes are intentionally *not* rolled back. This is a
system-state rollback only.

# OPTIONS

_N_
	Snapper snapshot number to roll back to. Required unless
	*--list* or *--undo* is given.

*-l*, *--list*
	Show recent snapper snapshots and exit. Use this to find the
	right _N_.

*-u*, *--undo*
	Promote the most recent @.rollback-_<ts>_ backup back to *@*.
	Reboot to apply.

*-y*, *--yes*
	Skip the "are you sure" prompt.

# FILES

/
	The *@* subvolume.

/.snapshots/
	Where snapper keeps its snapshots.

# SEE ALSO

*shedman*(1), *shedman-update*(1), *snapper*(8).

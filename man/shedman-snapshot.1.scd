shedman-snapshot(1)

# NAME

shedman-snapshot - create a manual btrfs checkpoint

# SYNOPSIS

*shedman snapshot* _label_++
*shedman snapshot* {*-l*|*--list*}

# DESCRIPTION

Creates a snapper snapshot named _label_ — the "before I break
something" checkpoint. Checkpoints use the same conventions as
*shedman update*'s automatic snapshots: they appear in
*shedman-upgrade-history*(1) and restore through
*shedman-rollback*(1).

# OPTIONS

*-l*, *--list*
	List manual checkpoints with their numbers.

# SEE ALSO

*shedman*(1), *shedman-rollback*(1), *shedman-upgrade-history*(1)

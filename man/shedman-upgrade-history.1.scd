shedman-upgrade-history(1)

# NAME

shedman-upgrade-history - browse upgrade history and roll back

# SYNOPSIS

*shedman upgrade-history* [*--list*]

# DESCRIPTION

Lists past *shedman update* runs with their pre/post snapper
snapshot pairs and the package changes each one made. From the TUI a
run can be rolled back (delegating to *shedman rollback* with the
matching snapshot).

Checkpoints created with *shedman snapshot* list in a separate
manual-snapshots section. They have no pre/post pair, so the in-TUI
diff and rollback actions apply to upgrade runs; restore a
checkpoint with *shedman rollback* and its snapshot number.

# OPTIONS

*--list*
	Plain-text run list, no TUI (for scripting).

# SEE ALSO

*shedman-update*(1), *shedman-rollback*(1), *shedman-snapshot*(1)

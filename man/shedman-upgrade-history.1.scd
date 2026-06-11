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

# OPTIONS

*--list*
	Plain-text run list, no TUI (for scripting).

# SEE ALSO

*shedman-update*(1), *shedman-rollback*(1)

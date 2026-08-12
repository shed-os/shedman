shedman-services(1)

# NAME

shedman-services - verify expected services are enabled and running

# SYNOPSIS

*shedman services* [*-f*|*--fix*]

# DESCRIPTION

Prints a table of unit / enabled-state / active-state for the
platform invariants (NetworkManager, bluetooth, greetd, …) plus
whatever */etc/shedos/system.toml* declares — the table can never
disagree with *shedman apply*. Exits non-zero when something needs
repair.

# OPTIONS

*-f*, *--fix*
	Enable and start anything missing (needs root). Also runs
	*initdb* when the TOML expects PostgreSQL and the cluster is
	absent.

# SEE ALSO

*shedman*(1), *shedman-apply*(1), *shedman-doctor*(1)

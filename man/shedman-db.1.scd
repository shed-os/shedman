shedman-db(1)

# NAME

shedman-db - per-project databases and service bootstrap

# SYNOPSIS

*shedman db* {*create*|*drop*} _name_++
*shedman db* *list*++
*sudo shedman db* *--pg-bootstrap*

# DESCRIPTION

PostgreSQL helpers on top of the per-user setup ShedOS ships: every
regular user has a LOGIN+CREATEDB role and a same-named database,
so project databases need no superuser and no pg_hba edits.

# COMMANDS

*create* _name_
	Create a project database owned by you. Names are lowercase
	letters, digits, underscore and hyphen.

*drop* _name_
	Drop one of your databases after confirmation. Your personal
	default database (named after your user) is refused.

*list*
	Your databases with on-disk sizes.

*--pg-bootstrap*
	Create the role+database pair for every regular user (UID
	1000–59999). Idempotent; also fired on first boot by
	*shedos-pg-user-bootstrap.service*. Needs root.

# SEE ALSO

*shedman*(1), *shedman-services*(1)

shedman-db(1)

# NAME

shedman-db - database-service bootstrap and admin helpers

# SYNOPSIS

*sudo shedman db* *--pg-bootstrap*

# DESCRIPTION

PostgreSQL helpers for the per-user development database ShedOS
ships. *--pg-bootstrap* creates a role and same-named database for
every regular user (UID 1000–59999) with LOGIN + CREATEDB, skipping
names that aren't POSIX-safe. Idempotent; also fired on first boot
by *shedos-pg-user-bootstrap.service*.

# SEE ALSO

*shedman-services*(1)

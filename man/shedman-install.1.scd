shedman-install(1)

# NAME

shedman-install - install packages from any source with one command

# SYNOPSIS

*shedman install* _pkg_...++
*shedman install* {*-s*|*--search*} _query_

# DESCRIPTION

Installs one or more packages, auto-detecting the source per
package: official repos and *[shedos]* via pacman first, then the
AUR via yay. AUR installs are deliberately interactive — yay's
review prompts run and makepkg verifies declared PGP signatures;
nothing is installed sight-unseen.

# OPTIONS

*-s*, *--search* _query_
	Search pacman repos and the AUR; prints both result sets.

# SEE ALSO

*shedman-uninstall*(1), *shedman-update*(1)

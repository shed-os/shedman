shedman-uninstall(1) "ShedOS" "User Commands"

# NAME

shedman-uninstall - remove installed packages, routing pacman vs yay by source

# SYNOPSIS

*shedman uninstall* _pkg_ [_pkg ..._]++
*shedman uninstall* [*-v*|*--verbose*] _pkg_ [_pkg ..._]++
*shedman uninstall* {*-s*|*--search*} _query_++
*shedman uninstall* [*-h*|*--help*]

# DESCRIPTION

*shedman uninstall* removes one or more installed packages together
with their now-unused dependency closure (equivalent to
*pacman -Rns* / *yay -Rns*). It is the inverse of *shedman install*
and mirrors that command's UX.

Per-package source detection runs on the _installed_ state, not on
repo availability. *pacman -Q* decides whether the package is
installed at all; *pacman -Qm* decides whether it was installed
from a foreign source (AUR build with no backing sync repo), in
which case removal routes through *yay -Rns* for parity with how
*shedman install* invokes yay. Packages tracked by any sync DB
(official or *[shedos]*) are removed via *sudo pacman -Rns*.

The script refuses to run as root: sudo is invoked only for the
pacman path; yay drops privileges itself. *--noconfirm* is passed
through to match *shedman install*'s house style, with the sudo
password prompt acting as the authorisation gate.

# OPTIONS

*-v*, *--verbose*
	Print *via pacman:* / *via yay:* and the full resolved command
	before exec, so you can see exactly which tool runs.

*-s*, *--search* _query_
	Search _installed_ packages whose name or description matches
	_query_ (same regex syntax as *pacman -Qs*). Output groups
	matches into *=== installed (official + [shedos]) ===* and
	*=== installed (AUR) ===* by partitioning the *pacman -Qs*
	hit set against *pacman -Qm*. Useful as a "what could I
	uninstall?" probe before running the destructive command.

*-h*, *--help*
	Print usage and exit.

# EXIT STATUS

*0*
	All requested packages removed.

*1*
	One or more arguments was not installed, *--search* was given
	with no query, or AUR removal was needed but *yay* is missing.

*2*
	Invoked as root.

Errors from pacman / yay propagate verbatim (repo locks, dep
cycles, etc.).

# EXAMPLES

Remove a single official package:

```
shedman uninstall tree
```

Preview the command before running:

```
shedman uninstall -v tree
```

Find every installed package that mentions "fish":

```
shedman uninstall --search fish
```

Remove an AUR-built tool (routes through yay):

```
shedman uninstall google-chrome
```

# SEE ALSO

*shedman*(1), *shedman-install*(1), *pacman*(8), *yay*(8).

shedman-config(1) "ShedOS" "User Commands"

# NAME

shedman-config - sync packaged dotfile defaults and review merge conflicts

# SYNOPSIS

*shedman config* {*-s*|*--sync*} [_options_]++
*shedman config* {*-r*|*--review*} [_file_]

# DESCRIPTION

*shedman config* is an umbrella for the two dotfile-related
operations on ShedOS: *--sync* seeds packaged defaults into $HOME
and runs a per-file 3-way merge after every upgrade; *--review*
opens the IntelliJ-style merge TUI for unresolved _.shedosnew_
conflicts.

# SUBMODES

*-s*, *--sync*
	Run the 3-way merge between pristine defaults (under
	/usr/share/shedos/<pkg>/defaults/), your live $HOME files, and
	the manifest at $XDG_STATE_HOME/shedos/last-seen/. Files you've
	edited are never overwritten — when both you and upstream
	changed a file, the upstream version lands as _<file>.shedosnew_.

	Common flags (forwarded to the underlying tool):
	*--dry-run* print plan, change nothing;
	*--yes* skip prompts;
	*--rebuild-manifest* re-hash current $HOME against current
	defaults (used after manually-resolving a conflict).

*-r*, *--review*
	Open the merge TUI for every _.shedosnew_ sidecar in $HOME.
	Per-hunk decisions: y (yours), t (theirs), b (both YT), B (both
	TY), x (skip), u (undo). Whole-file shortcuts: A (all yours), T
	(all theirs). Save with *s* — atomic; the previous live file is
	backed up to _<file>.shedosbak_.

# FILES

/usr/share/shedos/<pkg>/defaults/
	Pristine defaults shipped by each package.

$HOME/<file>.shedosnew
	Upstream version of a file you've also edited locally.

$HOME/<file>.shedosbak
	Backup of the pre-merge YOURS file (lossless).

$XDG_STATE_HOME/shedos/last-seen/
	Manifest tracking the per-file hash of the last default version
	seen.

$HOME/.config/shedos/sync-exclude
	Optional glob list of files to opt out of declarative management.

# SEE ALSO

*shedman*(1), *shedman-update*(1).

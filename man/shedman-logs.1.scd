shedman-logs(1)

# NAME

shedman-logs - browse systemd journal logs in a TUI

# SYNOPSIS

*shedman logs* [*--list-units*]

# DESCRIPTION

A two-pane Textual TUI: unit list left, journal messages right.
Filters: regex (*/*, passed to *journalctl -g*), severity floor
(*f*), time window (*t*: 1h / 6h / 24h / boot), live streaming
(*s*, *journalctl -f*). *j*/*k* or arrows move the unit cursor;
*enter* or *space* loads messages; *c* copies the newest visible
line; *r* refreshes units; *?* help; *q* quits.

# OPTIONS

*--list-units*
	Print the parsed unit list and exit (smoke-testing aid).

# SEE ALSO

*shedman-upgrade-history*(1)

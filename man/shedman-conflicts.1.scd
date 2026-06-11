shedman-conflicts(1)

# NAME

shedman-conflicts - report unresolved .shedosnew dotfile conflicts

# SYNOPSIS

*shedman conflicts* [*--waybar*] [*--refresh-waybar*] [*--notify-if-new*] [*--tick*] [*--reset-notify-state*]

# DESCRIPTION

Counts *.shedosnew* sidecar files under *$HOME* — the conflicts
*shedman config --sync* produces when both the packaged default and
the local copy changed. With no flag, prints a human summary.
Resolve conflicts with *shedman config --review*.

# OPTIONS

*--waybar*
	JSON payload for the waybar *custom/conflicts* pill.

*--refresh-waybar*
	Signal waybar (SIGRTMIN+9) to re-poll the pill.

*--notify-if-new*
	Desktop notification iff the count rose since last notified.

*--tick*
	Count + notify-if-new + waybar refresh in one shot.

*--reset-notify-state*
	Forget the last-notified count.

# SEE ALSO

*shedman-config*(1), *shedman-status*(1)

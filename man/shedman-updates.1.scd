shedman-updates(1)

# NAME

shedman-updates - report pending pacman + AUR updates

# SYNOPSIS

*shedman updates* [*--waybar*] [*--refresh-waybar*] [*--notify-if-new*] [*--tick*] [*--reset-notify-state*]

# DESCRIPTION

Counts pending official-repo updates (via *checkupdates*) and AUR
updates (via *yay -Qua* or *paru*). With no flag, prints a human
summary. Runs unprivileged; checkupdates uses an isolated temp
database, so it never touches pacman's real sync state.

# OPTIONS

*--waybar*
	JSON payload for the waybar *custom/updates* pill.

*--refresh-waybar*
	Signal waybar (SIGRTMIN+8) to re-poll the pill. No counting.

*--notify-if-new*
	Send a desktop notification iff the count rose since the last
	notification. Silent at zero or unchanged counts.

*--tick*
	Timer entry point: count, notify-if-new, kick waybar. Fired
	hourly by *shedos-update-check.timer*.

*--reset-notify-state*
	Forget the last-notified count. *shedman update* calls this
	after a successful upgrade.

# SEE ALSO

*shedman-update*(1), *shedman-status*(1)

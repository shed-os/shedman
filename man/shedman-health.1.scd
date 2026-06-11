shedman-health(1)

# NAME

shedman-health - aggregate hardware health signals

# SYNOPSIS

*shedman health* [*--waybar*] [*--refresh-waybar*] [*--notify-if-new*] [*--tick*] [*--reset-notify-state*]

# DESCRIPTION

Summarizes disk usage, memory pressure, battery state, and CPU
temperature into one ok/warning/critical verdict. With no flag,
prints the per-metric table.

# OPTIONS

*--waybar*
	JSON payload for the waybar *custom/health* pill (hidden while
	everything is ok).

*--refresh-waybar*
	Signal waybar (SIGRTMIN+10) to re-poll the pill.

*--notify-if-new*
	Desktop notification on ok→warning or any→critical
	transitions, with 10-minute hysteresis against flapping.

*--tick*
	Timer entry point: refresh, notify, kick waybar.

*--reset-notify-state*
	Forget the last-notified class.

# ENVIRONMENT

*SHEDOS_HEALTH_SENSORS_CMD*, *SHEDOS_HEALTH_DF_TARGETS*
	Override the sensors command and the disk-usage targets
	(defaults: *sensors -u*; */ $HOME*).

# SEE ALSO

*shedman-status*(1)

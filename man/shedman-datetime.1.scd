shedman-datetime(1)

# NAME

shedman-datetime - set timezone, network time, and the system clock

# SYNOPSIS

*shedman datetime*

# DESCRIPTION

Opens a single-screen TUI for adjusting the system clock. Changes
are local until *Apply*; *Cancel* (or *Esc*) discards.

*Network time (NTP)*
	Toggle *systemd-timesyncd*. When on, the clock is corrected
	against the configured NTP pool; when off, the manual *Date*
	and *Time* fields become editable and a user-supplied time
	takes effect on apply.

*Timezone*
	Searchable filter over the full *tzdata* set. Type to narrow
	the list, arrow keys to highlight, Enter to confirm. Pre-filled
	with the current zone.

*Date*, *Time*
	Manual time-of-day entry. Disabled while NTP is on. Format is
	*YYYY-MM-DD* and *HH:MM:SS*.

# AUTHORIZATION

Mutations route through the *org.freedesktop.timedate1* D-Bus
interface. Users in the *wheel* group are authorized directly by
*/usr/share/polkit-1/rules.d/49-shedos-timedate.rules* — no password
prompt, matching GNOME's default for date/time on single-user desktops.
The rule is scoped to the four *set-{timezone,time,local-rtc,ntp}*
actions only.

# SEE ALSO

*timedatectl*(1), *systemd-timesyncd*(8).

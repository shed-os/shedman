shedman-status(1) "ShedOS" "User Commands"

# NAME

shedman-status - aggregated dashboard across the four waybar signals

# SYNOPSIS

*shedman status*++
*shedman status* {*-j*|*--json*}++
*shedman status* {*-w*|*--watch*} [*--interval* _N_]

# DESCRIPTION

*shedman status* runs all four waybar-backed signal binaries
(*shedman updates*, *shedman conflicts*, *shedman health*,
*shedman doctor*) in *--waybar* mode and prints a single-screen
summary plus a verdict line. Each signal is run with a 2-second
timeout; failures degrade gracefully to _unavailable_(_error_) on
that row only.

# OPTIONS

default (no flag)
	Render a one-line-per-signal text dashboard plus a Summary
	line (OK / Attention needed / N unavailable).

*-j*, *--json*
	Emit an aggregated JSON document (schema:1) that passes each
	underlying *--waybar* payload through verbatim under its signal
	key, plus a top-level summary section. Suitable for monitoring
	scripts.

*-w*, *--watch*
	Spawn a Textual TUI that re-runs the four probes on a timer and
	re-renders. Press *q* to quit.

*--interval* _N_
	Refresh interval in seconds for *--watch*. Default 5; minimum 1.
	Also overridable via *SHEDOS_STATUS_INTERVAL*.

# ENVIRONMENT

*SHEDOS_STATUS_LIBEXEC*
	Override /usr/libexec/shedman. Used by tests; rarely set by end
	users.

*SHEDOS_STATUS_INTERVAL*
	Default refresh interval for *--watch*.

# EXIT STATUS

*0*
	Always (the verdict is in the output, not the exit code).

*2*
	/usr/libexec/shedman isn't a directory — the env override is
	wrong, or shedos-system is broken.

# SEE ALSO

*shedman*(1).

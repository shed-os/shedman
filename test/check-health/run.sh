#!/usr/bin/env bash
# run.sh — test harness for shedos-check-health.
#
# Drives the script with synthetic inputs via the documented test hooks
# (SHEDOS_HEALTH_PSI_FILE, SHEDOS_HEALTH_POWER_SUPPLY, SHEDOS_HEALTH_SENSORS_CMD,
# SHEDOS_HEALTH_DF_TARGETS) so we can assert each classifier without needing
# a rigged kernel. Tests run in a disposable $HOME + $XDG_STATE_HOME.
#
# Fixture layout under fixtures/<name>/:
#   fixture.sh            sources to set:
#                           EXPECT_OVERALL  — ok|warning|critical
#                           EXPECT_WAYBAR_CLASS  — same
#                           (optional EXPECT_TEXT_SUBSTRING — substring assertion)
#   psi                   (optional) contents of /proc/pressure/memory
#   power-supply/BAT0/*   (optional) energy_full, energy_full_design, etc.
#   sensors.out           (optional) lines `cat` will emit for sensors stand-in
#
# If a fixture omits an input, that metric resolves to "unavailable" (which
# is excluded from overall class aggregation — so "no-battery" stays ok).
#
# Exit: 0 all pass, 1 any failure.

set -uo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$here/../.." && pwd)
tool=$repo_root/tree/usr/libexec/shedman/health

if [[ ! -x $tool ]]; then
    echo "FATAL: $tool not executable" >&2
    exit 2
fi

if ! command -v jq >/dev/null 2>&1; then
    echo "FATAL: jq required (tests parse --waybar JSON)" >&2
    exit 2
fi

if (( $# > 0 )); then
    fixtures=("$@")
else
    fixtures=()
    while IFS= read -r -d '' d; do
        fixtures+=("$(basename "$d")")
    done < <(find "$here/fixtures" -mindepth 1 -maxdepth 1 -type d -print0 | sort -z)
fi

pass=0
fail=0
failures=()

_run_one() {
    local name=$1
    local fdir=$here/fixtures/$name
    [[ -d $fdir ]] || { echo "skip $name (no such fixture)"; return; }
    [[ -f $fdir/fixture.sh ]] || { echo "skip $name (no fixture.sh)"; return; }

    local EXPECT_OVERALL="" EXPECT_WAYBAR_CLASS="" EXPECT_TEXT_SUBSTRING=""
    # shellcheck disable=SC1091
    source "$fdir/fixture.sh"
    if [[ -z $EXPECT_OVERALL || -z $EXPECT_WAYBAR_CLASS ]]; then
        echo "FAIL $name: fixture.sh must set EXPECT_OVERALL + EXPECT_WAYBAR_CLASS"
        failures+=("$name")
        ((fail++))
        return
    fi

    local tmp
    tmp=$(mktemp -d -t shedos-health-test.XXXXXX)
    # shellcheck disable=SC2064
    trap "rm -rf -- '$tmp'" RETURN

    # Wire up the test hooks. Anything the fixture omits is steered at
    # a path that doesn't exist, which the script treats as "unavailable".
    local env_args=(
        "HOME=$tmp/home"
        "XDG_STATE_HOME=$tmp/state"
        "DBUS_SESSION_BUS_ADDRESS="   # suppress real notify-send
    )

    if [[ -f $fdir/psi ]]; then
        env_args+=("SHEDOS_HEALTH_PSI_FILE=$fdir/psi")
    else
        env_args+=("SHEDOS_HEALTH_PSI_FILE=$tmp/no-psi")
    fi

    if [[ -d $fdir/power-supply ]]; then
        env_args+=("SHEDOS_HEALTH_POWER_SUPPLY=$fdir/power-supply")
    else
        env_args+=("SHEDOS_HEALTH_POWER_SUPPLY=$tmp/no-power")
    fi

    if [[ -f $fdir/sensors.out ]]; then
        env_args+=("SHEDOS_HEALTH_SENSORS_CMD=cat $fdir/sensors.out")
    else
        # cat on a nonexistent path → "unavailable" via empty reading.
        env_args+=("SHEDOS_HEALTH_SENSORS_CMD=cat $tmp/no-sensors")
    fi

    # SMART + btrfs scrub read host state (a cache file and `btrfs scrub
    # status /`); steer both at controlled inputs so a real btrfs root or
    # smart cache on the harness machine can't leak into the verdict.
    if [[ -f $fdir/smart.json ]]; then
        env_args+=("SHEDOS_SMART_CACHE=$fdir/smart.json")
    else
        env_args+=("SHEDOS_SMART_CACHE=$tmp/no-smart")
    fi
    if [[ -f $fdir/scrub.out ]]; then
        env_args+=("SHEDOS_HEALTH_SCRUB_CMD=cat $fdir/scrub.out")
    else
        env_args+=("SHEDOS_HEALTH_SCRUB_CMD=cat $tmp/no-scrub")
    fi

    # Keep disk out of the equation — harness machines have varying free
    # space, so a disk-specific fixture lives in its own test (pointed at a
    # mount we control). Default: steer at a bogus path → "unavailable".
    env_args+=("SHEDOS_HEALTH_DF_TARGETS=${SHEDOS_HEALTH_DF_TARGETS:-$tmp/no-mount}")

    mkdir -p "$tmp/home" "$tmp/state"

    # --- Text mode: grab "Overall:" line.
    local text_out text_overall
    if ! text_out=$(env "${env_args[@]}" "$tool" 2>&1); then
        echo "FAIL $name: text-mode exit non-zero"
        echo "  output: $text_out"
        failures+=("$name")
        ((fail++))
        return
    fi
    text_overall=$(echo "$text_out" | awk '/^Overall:/ {print $2}')
    if [[ $text_overall != "$EXPECT_OVERALL" ]]; then
        echo "FAIL $name: text Overall=$text_overall, expected $EXPECT_OVERALL"
        echo "$text_out" | sed 's/^/    /'
        failures+=("$name")
        ((fail++))
        return
    fi

    if [[ -n $EXPECT_TEXT_SUBSTRING ]]; then
        if ! echo "$text_out" | grep -qF -- "$EXPECT_TEXT_SUBSTRING"; then
            echo "FAIL $name: text output missing expected substring"
            echo "  expected: $EXPECT_TEXT_SUBSTRING"
            echo "$text_out" | sed 's/^/    /'
            failures+=("$name")
            ((fail++))
            return
        fi
    fi

    # --- Waybar mode: parse JSON, check class.
    local waybar_out waybar_class
    if ! waybar_out=$(env "${env_args[@]}" "$tool" --waybar 2>&1); then
        echo "FAIL $name: --waybar exit non-zero"
        echo "  output: $waybar_out"
        failures+=("$name")
        ((fail++))
        return
    fi
    if ! waybar_class=$(echo "$waybar_out" | jq -er '.class' 2>/dev/null); then
        echo "FAIL $name: --waybar output not valid JSON or missing .class"
        echo "  output: $waybar_out"
        failures+=("$name")
        ((fail++))
        return
    fi
    if [[ $waybar_class != "$EXPECT_WAYBAR_CLASS" ]]; then
        echo "FAIL $name: waybar class=$waybar_class, expected $EXPECT_WAYBAR_CLASS"
        echo "  output: $waybar_out"
        failures+=("$name")
        ((fail++))
        return
    fi

    # --- refresh-waybar: must exit 0 regardless of waybar availability.
    if ! env "${env_args[@]}" "$tool" --refresh-waybar >/dev/null 2>&1; then
        echo "FAIL $name: --refresh-waybar exit non-zero"
        failures+=("$name")
        ((fail++))
        return
    fi

    # --- reset-notify-state: must write "ok" to the state file.
    if ! env "${env_args[@]}" "$tool" --reset-notify-state >/dev/null 2>&1; then
        echo "FAIL $name: --reset-notify-state exit non-zero"
        failures+=("$name")
        ((fail++))
        return
    fi
    if [[ ! -f $tmp/state/shedos/last-notified-health ]] || \
       [[ "$(<"$tmp/state/shedos/last-notified-health")" != ok ]]; then
        echo "FAIL $name: --reset-notify-state did not write 'ok'"
        failures+=("$name")
        ((fail++))
        return
    fi

    echo "PASS $name"
    ((pass++))
}

for f in "${fixtures[@]}"; do
    _run_one "$f"
done

echo
echo "Summary: $pass passed, $fail failed"
if (( fail > 0 )); then
    printf '  %s\n' "${failures[@]}"
    exit 1
fi
exit 0

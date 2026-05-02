#!/usr/bin/env bash
# Post-install integration check: confirm the four proprietary apps
# installed by shedos_optional_apps during Calamares are present.
# Run inside the freshly-installed system after Calamares finishes.

set -uo pipefail

if [[ -f /run/archiso/bootmnt ]] || grep -qi 'archiso' /proc/cmdline 2>/dev/null; then
    echo "ERROR: this script verifies an installed system; you appear to" >&2
    echo "       be running it on the live ISO. Reboot first." >&2
    exit 2
fi

required=(
    google-chrome
    postman-bin
    claude-code-bin
    jetbrains-toolbox
)

missing=()
for pkg in "${required[@]}"; do
    if pacman -Q "$pkg" >/dev/null 2>&1; then
        installed_ver=$(pacman -Q "$pkg" | awk '{print $2}')
        printf "  [OK]      %-22s %s\n" "$pkg" "$installed_ver"
    else
        printf "  [MISSING] %s\n" "$pkg"
        missing+=("$pkg")
    fi
done

echo ""
if (( ${#missing[@]} == 0 )); then
    echo "All ${#required[@]} default proprietary apps installed."
    exit 0
fi

echo "Missing: ${missing[*]}" >&2
echo "" >&2
echo "Likely causes:" >&2
echo "  1. yay-as-user could not sudo without a password — see" >&2
echo "     /etc/sudoers.d/wheel and Calamares' user-creation log." >&2
echo "  2. No internet during install; yay couldn't reach AUR." >&2
echo "  3. Upstream PKGBUILD broke (vendor moved a download URL)." >&2
echo "  4. Re-run: yay -S ${missing[*]}" >&2
exit 1

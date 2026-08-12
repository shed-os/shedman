#!/usr/bin/env python3
"""Unit tests for apply_core's mount boot-safety helpers.

Imports apply_core directly from the package tree and exercises the three
pure functions behind the nofail boot-safety work:
  _ensure_boot_safe_options, audit_fstab_mount_safety, add_nofail_to_fstab
"""
import sys
import pathlib

_TREE = pathlib.Path(__file__).resolve().parents[2] / \
    "tree/usr/lib/shedos"
sys.path.insert(0, str(_TREE))
import apply_core as ac  # noqa: E402

fails = []


def check(name, got, want):
    if got != want:
        fails.append(f"{name}: got {got!r} want {want!r}")


# --- _ensure_boot_safe_options ------------------------------------------
check("adds_nofail",
      ac._ensure_boot_safe_options("/run/media/u/disk", "defaults,noatime", False),
      "defaults,noatime,nofail,x-systemd.device-timeout=5s")
check("idempotent",
      ac._ensure_boot_safe_options("/data", "noatime,nofail,x-systemd.device-timeout=5s", False),
      "noatime,nofail,x-systemd.device-timeout=5s")
check("nofail_no_timeout",
      ac._ensure_boot_safe_options("/data", "nofail", False),
      "nofail,x-systemd.device-timeout=5s")
check("keeps_user_timeout",
      ac._ensure_boot_safe_options("/data", "nofail,x-systemd.device-timeout=30s", False),
      "nofail,x-systemd.device-timeout=30s")
check("root_untouched",
      ac._ensure_boot_safe_options("/", "defaults", False), "defaults")
check("required_optout",
      ac._ensure_boot_safe_options("/data", "defaults", True), "defaults")

# --- audit_fstab_mount_safety (derives root from the / entry) -----------
_FSTAB = (
    "UUID=ROOT / btrfs subvol=/@,defaults 0 0\n"
    "UUID=ROOT /home btrfs subvol=/@home,defaults 0 0\n"        # same fs as root -> safe
    "UUID=BKP /mnt/backup btrfs subvol=@backup,defaults,noatime 0 0\n"  # separate, no nofail -> RISK
    "UUID=SAFE /mnt/ext ext4 nofail,noatime 0 0\n"             # separate but nofail -> safe
    "tmpfs /tmp tmpfs defaults 0 0\n"                           # pseudo -> safe
)
findings = ac.audit_fstab_mount_safety(_FSTAB)
check("audit_flags_only_risky", sorted(f.target for f in findings), ["/mnt/backup"])
if findings:
    check("finding_device", findings[0].device, "UUID=BKP")

# --- add_nofail_to_fstab (preserves the rest of the line) ---------------
fixed = ac.add_nofail_to_fstab(_FSTAB, ["/mnt/backup"])
check("fix_adds_nofail",
      "UUID=BKP /mnt/backup btrfs subvol=@backup,defaults,noatime,nofail,x-systemd.device-timeout=5s 0 0" in fixed,
      True)
check("fix_leaves_root", "UUID=ROOT / btrfs subvol=/@,defaults 0 0" in fixed, True)
check("fix_is_complete", ac.audit_fstab_mount_safety(fixed), [])

# --- column spacing is preserved on aligned fstabs ----------------------
_ALIGNED = "UUID=BKP   /mnt/backup   btrfs   subvol=@backup,defaults   0 0\n"
check("fix_preserves_spacing",
      ac.add_nofail_to_fstab(_ALIGNED, ["/mnt/backup"]),
      "UUID=BKP   /mnt/backup   btrfs   subvol=@backup,defaults,nofail,x-systemd.device-timeout=5s   0 0\n")

if fails:
    print("FAIL\n  " + "\n  ".join(fails))
    sys.exit(1)
print("unit_mounts: all cases passed")

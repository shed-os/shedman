"""apply_core — shared plan engine for shedos-apply and shedos-doctor.

Both CLIs speak the same language:

  parse TOML  →  validate schema  →  build plan  →  (apply | report)

apply_core holds everything up to "build plan". The ``shedos-apply`` CLI
adds the prompt-and-apply loop; ``shedos-doctor`` adds drift reporting
and the waybar integration.

Import path: shedos-apply and shedos-doctor both prepend
``SHEDOS_LIB_ROOT`` (default ``/usr/lib/shedos``) to ``sys.path`` so
``import apply_core`` resolves without packaging gymnastics.

Env vars (all shared with shedos-apply):

    SHEDOS_APPLY_ETC_ROOT       replaces /etc
    SHEDOS_APPLY_STATE_ROOT     replaces /var/lib/shedos
    SHEDOS_APPLY_BOOT_ROOT      replaces /boot
    SHEDOS_APPLY_SYSTEMCTL      replaces "systemctl" (shlex-split)
    SHEDOS_APPLY_UFW            replaces "ufw" (shlex-split)
    SHEDOS_APPLY_PACMAN_KEY     replaces "pacman-key" (shlex-split)
    SHEDOS_APPLY_FSTAB_PATH     replaces /etc/fstab
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Paths — env-overridable so tests can steer at a disposable tree.
# ---------------------------------------------------------------------------


def etc_root() -> Path:
    return Path(os.environ.get("SHEDOS_APPLY_ETC_ROOT", "/etc"))


def state_root() -> Path:
    return Path(os.environ.get("SHEDOS_APPLY_STATE_ROOT", "/var/lib/shedos"))


def systemctl_cmd() -> list[str]:
    raw = os.environ.get("SHEDOS_APPLY_SYSTEMCTL", "systemctl")
    return shlex.split(raw)


def boot_root() -> Path:
    return Path(os.environ.get("SHEDOS_APPLY_BOOT_ROOT", "/boot"))


def ufw_cmd() -> list[str]:
    raw = os.environ.get("SHEDOS_APPLY_UFW", "ufw")
    return shlex.split(raw)


def pacman_key_cmd() -> list[str]:
    raw = os.environ.get("SHEDOS_APPLY_PACMAN_KEY", "pacman-key")
    return shlex.split(raw)


def fstab_path() -> Path:
    raw = os.environ.get("SHEDOS_APPLY_FSTAB_PATH")
    if raw:
        return Path(raw)
    return etc_root() / "fstab"


def config_path() -> Path:
    return etc_root() / "shedos" / "system.toml"


def manifest_path() -> Path:
    return state_root() / "apply.state.json"


def baseline_path(section: str) -> Path:
    """Per-section baseline file. Snapshotted on first apply, never
    rewritten — represents the install-time state Phase 6A reconcilers
    are invisible to."""
    return state_root() / f"{section}.baseline.json"


def state_path(section: str) -> Path:
    """Per-section last-applied state file. Updated every successful
    apply; drives the three-way diff that distinguishes 'remove via
    TOML' from 'adopt via raw tool'."""
    return state_root() / f"{section}.state.json"


# ---------------------------------------------------------------------------
# Colorized output — opt-out via NO_COLOR (XDG convention).
# ---------------------------------------------------------------------------


def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


_ANSI = {
    "green":  "\033[32m",
    "red":    "\033[31m",
    "yellow": "\033[33m",
    "blue":   "\033[34m",
    "mauve":  "\033[35m",
    "dim":    "\033[2m",
    "bold":   "\033[1m",
    "reset":  "\033[0m",
}


def colorize(text: str, color: str) -> str:
    if not _supports_color():
        return text
    return f"{_ANSI.get(color, '')}{text}{_ANSI['reset']}"


# ---------------------------------------------------------------------------
# Change + Plan value types. Reconcilers emit these and never mutate state
# during planning; the runner is what calls ``change.apply()``.
# ---------------------------------------------------------------------------


@dataclass
class Change:
    kind: str           # "+" (add), "~" (update), "-" (remove)
    section: str        # "systemd.system", "drop-ins", etc — display-only
    summary: str        # one-line label for the plan view
    diff: Optional[str] = None  # optional unified-diff block for `diff` prompt
    # The actual mutation — a zero-arg callable returning None. Allowed to
    # raise; the runner catches and rolls back.
    apply_fn: Optional[Callable[[], None]] = None
    # Optional undo function — called in LIFO order if a later change raises.
    undo_fn: Optional[Callable[[], None]] = None


@dataclass
class Plan:
    changes: list[Change] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.changes

    def render(self) -> str:
        if self.is_empty():
            return "(no changes — system matches system.toml)"
        out = []
        width = max(len(c.section) for c in self.changes)
        for c in self.changes:
            sym = {"+": colorize("+", "green"),
                   "~": colorize("~", "yellow"),
                   "-": colorize("-", "red")}.get(c.kind, c.kind)
            out.append(f"  {sym} [{c.section:<{width}}]  {c.summary}")
        return "\n".join(out)

    def render_diffs(self) -> str:
        out = []
        for c in self.changes:
            if not c.diff:
                continue
            out.append(colorize(f"=== [{c.section}] {c.summary}", "bold"))
            out.append(c.diff)
            out.append("")
        return "\n".join(out) or "(no diffable changes)"

    def to_json(self) -> dict[str, Any]:
        return {
            "aligned": self.is_empty(),
            "drift_count": len(self.changes),
            "changes": [
                {"kind": c.kind, "section": c.section, "summary": c.summary}
                for c in self.changes
            ],
        }


# ---------------------------------------------------------------------------
# Schema validation — hand-rolled because the schema is small and we want
# stdlib-only. ``validate_doc`` returns a normalized dict or raises
# ``SchemaError`` with a pointed message.
# ---------------------------------------------------------------------------


class SchemaError(ValueError):
    pass


_ALLOWED_TOP = {"schema", "systemd", "drop-ins", "snapper",
                "pacman", "services", "network", "security", "fs"}
_ALLOWED_SYSTEMD = {"system", "user"}
_ALLOWED_SYSTEMD_SUB = {"enable", "disable"}
_ALLOWED_SNAPPER = {"timeline", "cleanup"}
_ALLOWED_SNAPPER_TIMELINE = {"enabled", "hourly", "daily", "weekly",
                             "monthly", "yearly"}
_ALLOWED_SNAPPER_CLEANUP = {"number"}
_ALLOWED_SNAPPER_CLEANUP_NUMBER = {"limit"}
_ALLOWED_PACMAN = {"repos"}
_ALLOWED_PACMAN_REPO_KEYS = {"server", "siglevel"}
_ALLOWED_SERVICES = {"postgresql"}
_ALLOWED_SERVICES_POSTGRES = {"auto-init", "per-user-db"}
_ALLOWED_NETWORK = {"firewall"}
_ALLOWED_FIREWALL_TOP = {"enabled", "incoming", "outgoing", "routed", "rules"}
_ALLOWED_FIREWALL_RULE_KEYS = {
    "action", "direction", "interface", "log",
    "from", "to", "from-port", "to-port", "port", "proto", "app",
    "comment",
}
_ALLOWED_FIREWALL_DEFAULT_POLICIES = {"allow", "deny", "reject"}
_ALLOWED_FIREWALL_RULE_ACTIONS = {"allow", "deny", "reject", "limit"}
_ALLOWED_FIREWALL_DIRECTIONS = {"in", "out"}
_ALLOWED_FIREWALL_LOG = {"log", "log-all"}
# Loose proto allowlist mirroring `man ufw`'s "PROTOCOLS" section. Anything
# not on this list (e.g. typos like `tpc`) raises a schema error.
_ALLOWED_FIREWALL_PROTOS = {"tcp", "udp", "ah", "esp", "ipv6", "igmp", "gre"}
_ALLOWED_SECURITY = {"keyring"}
_ALLOWED_SECURITY_KEYRING = {"trusted"}
_ALLOWED_FS = {"mounts"}
_ALLOWED_MOUNT_KEYS = {"name", "device", "target", "fstype",
                      "options", "dump", "pass"}
MOUNT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

GPG_FINGERPRINT_RE = re.compile(r"^[0-9A-F]{40}$")

UNIT_NAME_RE = re.compile(r"^[A-Za-z0-9@:_.\-\\x]+\.(service|timer|socket|target|mount|path|slice)$")
DROPIN_KEY_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-/]*\.(conf|json|rules|nsswitch|hosts|cfg)$")
REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")


def _check_keys(section: str, got: dict, allowed: set[str]) -> None:
    extras = set(got) - allowed
    if extras:
        raise SchemaError(
            f"[{section}] has unknown key(s): {sorted(extras)}. "
            f"Allowed: {sorted(allowed)}"
        )


def _check_str_list(section: str, val: Any, item_re: re.Pattern) -> list[str]:
    if not isinstance(val, list):
        raise SchemaError(f"[{section}] must be an array of strings")
    out = []
    for i, item in enumerate(val):
        if not isinstance(item, str):
            raise SchemaError(
                f"[{section}][{i}] must be a string, got {type(item).__name__}"
            )
        if not item_re.match(item):
            raise SchemaError(
                f"[{section}][{i}] {item!r} is not a valid unit name"
            )
        out.append(item)
    return out


def _check_int(section: str, val: Any, *, minimum: int = 0) -> int:
    if isinstance(val, bool) or not isinstance(val, int):
        raise SchemaError(f"[{section}] must be an integer, got {val!r}")
    if val < minimum:
        raise SchemaError(f"[{section}] must be >= {minimum}, got {val}")
    return val


def _check_bool(section: str, val: Any) -> bool:
    if not isinstance(val, bool):
        raise SchemaError(f"[{section}] must be true/false, got {val!r}")
    return val


@dataclass
class SystemdSection:
    system_enable: list[str] = field(default_factory=list)
    system_disable: list[str] = field(default_factory=list)
    user_enable: list[str] = field(default_factory=list)
    user_disable: list[str] = field(default_factory=list)


@dataclass
class SnapperSection:
    timeline_enabled: Optional[bool] = None
    timeline_hourly: Optional[int] = None
    timeline_daily: Optional[int] = None
    timeline_weekly: Optional[int] = None
    timeline_monthly: Optional[int] = None
    timeline_yearly: Optional[int] = None
    cleanup_number_limit: Optional[int] = None

    def is_empty(self) -> bool:
        return all(v is None for v in (
            self.timeline_enabled, self.timeline_hourly,
            self.timeline_daily, self.timeline_weekly,
            self.timeline_monthly, self.timeline_yearly,
            self.cleanup_number_limit,
        ))


@dataclass
class PacmanRepo:
    name: str
    server: str
    siglevel: str = "Required DatabaseRequired"


@dataclass
class PacmanSection:
    repos: dict[str, PacmanRepo] = field(default_factory=dict)
    managed: bool = False  # True once [pacman.repos] appears at all

    def is_empty(self) -> bool:
        return not self.managed


@dataclass
class PostgresqlServices:
    auto_init: Optional[bool] = None
    per_user_db: Optional[bool] = None

    def is_empty(self) -> bool:
        return self.auto_init is None and self.per_user_db is None


@dataclass
class ServicesSection:
    postgresql: PostgresqlServices = field(default_factory=PostgresqlServices)

    def is_empty(self) -> bool:
        return self.postgresql.is_empty()


@dataclass
class FirewallRule:
    """A single ufw rule row, normalized to the union of UFW's grammar.
    `None` = "key not specified". `to_tuple()` produces the identity
    used for the three-way merge — comments are excluded since they're
    informational, not part of identity."""
    action: str            # required: allow | deny | reject | limit
    direction: Optional[str] = None
    interface: Optional[str] = None
    log: Optional[str] = None
    from_: Optional[str] = None       # `from` is a Python keyword
    to: Optional[str] = None
    from_port: Optional[int] = None
    to_port: Optional[int] = None
    port: Optional[int] = None
    proto: Optional[str] = None
    app: Optional[str] = None
    comment: Optional[str] = None

    def to_tuple(self) -> tuple:
        return (
            self.action, self.direction, self.interface, self.log,
            self.from_, self.to, self.from_port, self.to_port,
            self.port, self.proto, self.app,
        )


@dataclass
class FirewallSection:
    """Parsed [network.firewall] block. `present` = "the section
    exists in TOML at all" — distinct from `enabled = false`, which
    is "section is in TOML but ufw should be off"."""
    present: bool = False
    enabled: Optional[bool] = None
    incoming: Optional[str] = None
    outgoing: Optional[str] = None
    routed: Optional[str] = None
    rules: list[FirewallRule] = field(default_factory=list)


@dataclass
class NetworkSection:
    firewall: FirewallSection = field(default_factory=FirewallSection)

    def is_empty(self) -> bool:
        return not self.firewall.present


@dataclass
class KeyringSection:
    """Parsed [security.keyring]. `trusted` is a list of upper-case
    40-char hex GPG fingerprints we want locally signed."""
    present: bool = False
    trusted: list[str] = field(default_factory=list)


@dataclass
class SecuritySection:
    keyring: KeyringSection = field(default_factory=KeyringSection)

    def is_empty(self) -> bool:
        return not self.keyring.present


@dataclass
class MountEntry:
    """One [[fs.mounts]] table → one fstab line. `name` is the user-
    friendly handle for the entry; the (device, target) pair is the
    identity tuple for the three-way merge."""
    name: str
    device: str
    target: str
    fstype: str
    options: str = "defaults"
    dump: int = 0
    pass_: int = 0

    def to_fstab_line(self) -> str:
        # Tab-separated, mirroring the format Calamares emits.
        return (f"{self.device}\t{self.target}\t{self.fstype}\t"
                f"{self.options}\t{self.dump}\t{self.pass_}")

    def identity(self) -> tuple:
        return (self.device, self.target)


@dataclass
class FsSection:
    present: bool = False
    mounts: list[MountEntry] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.present


@dataclass
class ValidatedConfig:
    schema_version: int = SCHEMA_VERSION
    systemd: SystemdSection = field(default_factory=SystemdSection)
    dropins: dict[str, str] = field(default_factory=dict)
    snapper: SnapperSection = field(default_factory=SnapperSection)
    pacman: PacmanSection = field(default_factory=PacmanSection)
    services: ServicesSection = field(default_factory=ServicesSection)
    network: NetworkSection = field(default_factory=NetworkSection)
    security: SecuritySection = field(default_factory=SecuritySection)
    fs: FsSection = field(default_factory=FsSection)


def validate_doc(doc: dict) -> ValidatedConfig:
    _check_keys("<root>", doc, _ALLOWED_TOP)

    version = doc.get("schema", SCHEMA_VERSION)
    if not isinstance(version, int) or version != SCHEMA_VERSION:
        raise SchemaError(
            f"schema = {version} is not supported; this shedos-apply "
            f"speaks schema {SCHEMA_VERSION}. "
            f"Check `man shedos-apply` or the CHANGELOG before downgrading."
        )

    cfg = ValidatedConfig(schema_version=version)

    sysd_raw = doc.get("systemd", {})
    if not isinstance(sysd_raw, dict):
        raise SchemaError("[systemd] must be a table")
    _check_keys("systemd", sysd_raw, _ALLOWED_SYSTEMD)
    for scope in ("system", "user"):
        sub = sysd_raw.get(scope, {})
        if not isinstance(sub, dict):
            raise SchemaError(f"[systemd.{scope}] must be a table")
        _check_keys(f"systemd.{scope}", sub, _ALLOWED_SYSTEMD_SUB)
        en = _check_str_list(f"systemd.{scope}.enable",
                             sub.get("enable", []), UNIT_NAME_RE)
        di = _check_str_list(f"systemd.{scope}.disable",
                             sub.get("disable", []), UNIT_NAME_RE)
        overlap = set(en) & set(di)
        if overlap:
            raise SchemaError(
                f"[systemd.{scope}] unit(s) appear in both enable and "
                f"disable: {sorted(overlap)}"
            )
        if scope == "system":
            cfg.systemd.system_enable, cfg.systemd.system_disable = en, di
        else:
            cfg.systemd.user_enable, cfg.systemd.user_disable = en, di

    dropins_raw = doc.get("drop-ins", {})
    if not isinstance(dropins_raw, dict):
        raise SchemaError("[drop-ins] must be a table")
    for key, value in dropins_raw.items():
        if not isinstance(key, str):
            raise SchemaError(f"[drop-ins] key must be a string, got {key!r}")
        if "/" not in key:
            raise SchemaError(
                f"[drop-ins] key {key!r} must include a directory — "
                f"e.g. 'sddm.conf.d/theme.conf', not a bare filename"
            )
        if key.startswith("/") or ".." in key.split("/"):
            raise SchemaError(
                f"[drop-ins] key {key!r} must be relative to /etc with no '..'"
            )
        if not DROPIN_KEY_RE.match(key):
            raise SchemaError(
                f"[drop-ins] key {key!r} does not look like a config file "
                f"(expected a /-segment ending in .conf/.rules/.json/…)"
            )
        if not isinstance(value, str):
            raise SchemaError(
                f"[drop-ins][{key}] must be a string, got {type(value).__name__}"
            )
        cfg.dropins[key] = value

    snap_raw = doc.get("snapper", {})
    if not isinstance(snap_raw, dict):
        raise SchemaError("[snapper] must be a table")
    _check_keys("snapper", snap_raw, _ALLOWED_SNAPPER)
    tl = snap_raw.get("timeline", {})
    if tl:
        if not isinstance(tl, dict):
            raise SchemaError("[snapper.timeline] must be a table")
        _check_keys("snapper.timeline", tl, _ALLOWED_SNAPPER_TIMELINE)
        if "enabled" in tl:
            cfg.snapper.timeline_enabled = _check_bool("snapper.timeline.enabled", tl["enabled"])
        for k in ("hourly", "daily", "weekly", "monthly", "yearly"):
            if k in tl:
                setattr(cfg.snapper, f"timeline_{k}",
                        _check_int(f"snapper.timeline.{k}", tl[k]))
    cu = snap_raw.get("cleanup", {})
    if cu:
        if not isinstance(cu, dict):
            raise SchemaError("[snapper.cleanup] must be a table")
        _check_keys("snapper.cleanup", cu, _ALLOWED_SNAPPER_CLEANUP)
        num = cu.get("number", {})
        if num:
            if not isinstance(num, dict):
                raise SchemaError("[snapper.cleanup.number] must be a table")
            _check_keys("snapper.cleanup.number", num,
                        _ALLOWED_SNAPPER_CLEANUP_NUMBER)
            if "limit" in num:
                cfg.snapper.cleanup_number_limit = _check_int(
                    "snapper.cleanup.number.limit", num["limit"])

    pac_raw = doc.get("pacman", {})
    if pac_raw:
        if not isinstance(pac_raw, dict):
            raise SchemaError("[pacman] must be a table")
        _check_keys("pacman", pac_raw, _ALLOWED_PACMAN)
        repos_raw = pac_raw.get("repos", {})
        if not isinstance(repos_raw, dict):
            raise SchemaError("[pacman.repos] must be a table")
        cfg.pacman.managed = True
        for name, stanza in repos_raw.items():
            if not REPO_NAME_RE.match(name):
                raise SchemaError(
                    f"[pacman.repos.{name}] is not a valid pacman repo name "
                    f"(alphanumerics/dot/dash/underscore only)"
                )
            if not isinstance(stanza, dict):
                raise SchemaError(
                    f"[pacman.repos.{name}] must be a table of key=value pairs"
                )
            _check_keys(f"pacman.repos.{name}", stanza,
                        _ALLOWED_PACMAN_REPO_KEYS)
            server = stanza.get("server")
            if not isinstance(server, str) or not server.strip():
                raise SchemaError(
                    f"[pacman.repos.{name}].server is required and must be "
                    f"a non-empty string"
                )
            siglevel = stanza.get("siglevel", "Required DatabaseRequired")
            if not isinstance(siglevel, str):
                raise SchemaError(
                    f"[pacman.repos.{name}].siglevel must be a string"
                )
            cfg.pacman.repos[name] = PacmanRepo(
                name=name, server=server.strip(), siglevel=siglevel.strip(),
            )

    svc_raw = doc.get("services", {})
    if svc_raw:
        if not isinstance(svc_raw, dict):
            raise SchemaError("[services] must be a table")
        _check_keys("services", svc_raw, _ALLOWED_SERVICES)
        pg_raw = svc_raw.get("postgresql", {})
        if pg_raw:
            if not isinstance(pg_raw, dict):
                raise SchemaError("[services.postgresql] must be a table")
            _check_keys("services.postgresql", pg_raw,
                        _ALLOWED_SERVICES_POSTGRES)
            if "auto-init" in pg_raw:
                cfg.services.postgresql.auto_init = _check_bool(
                    "services.postgresql.auto-init", pg_raw["auto-init"])
            if "per-user-db" in pg_raw:
                cfg.services.postgresql.per_user_db = _check_bool(
                    "services.postgresql.per-user-db", pg_raw["per-user-db"])

    net_raw = doc.get("network", {})
    if net_raw:
        if not isinstance(net_raw, dict):
            raise SchemaError("[network] must be a table")
        _check_keys("network", net_raw, _ALLOWED_NETWORK)
        fw_raw = net_raw.get("firewall", {})
        if fw_raw is not None:
            if not isinstance(fw_raw, dict):
                raise SchemaError("[network.firewall] must be a table")
            _check_keys("network.firewall", fw_raw, _ALLOWED_FIREWALL_TOP)
            cfg.network.firewall.present = True

            if "enabled" in fw_raw:
                cfg.network.firewall.enabled = _check_bool(
                    "network.firewall.enabled", fw_raw["enabled"])

            for chain in ("incoming", "outgoing", "routed"):
                if chain in fw_raw:
                    val = fw_raw[chain]
                    if not isinstance(val, str) or val not in _ALLOWED_FIREWALL_DEFAULT_POLICIES:
                        raise SchemaError(
                            f"[network.firewall].{chain} must be one of "
                            f"{sorted(_ALLOWED_FIREWALL_DEFAULT_POLICIES)}, "
                            f"got {val!r}"
                        )
                    setattr(cfg.network.firewall, chain, val)

            rules_raw = fw_raw.get("rules", [])
            if not isinstance(rules_raw, list):
                raise SchemaError("[network.firewall].rules must be an array")
            for i, item in enumerate(rules_raw):
                if not isinstance(item, dict):
                    raise SchemaError(
                        f"[network.firewall.rules][{i}] must be a table"
                    )
                _check_keys(f"network.firewall.rules[{i}]", item,
                            _ALLOWED_FIREWALL_RULE_KEYS)
                cfg.network.firewall.rules.append(
                    _validate_firewall_rule(i, item)
                )

    sec_raw = doc.get("security", {})
    if sec_raw:
        if not isinstance(sec_raw, dict):
            raise SchemaError("[security] must be a table")
        _check_keys("security", sec_raw, _ALLOWED_SECURITY)
        kr_raw = sec_raw.get("keyring", {})
        if kr_raw is not None:
            if not isinstance(kr_raw, dict):
                raise SchemaError("[security.keyring] must be a table")
            _check_keys("security.keyring", kr_raw,
                        _ALLOWED_SECURITY_KEYRING)
            cfg.security.keyring.present = True
            trusted_raw = kr_raw.get("trusted", [])
            if not isinstance(trusted_raw, list):
                raise SchemaError(
                    "[security.keyring].trusted must be an array of "
                    "40-char hex fingerprint strings"
                )
            for i, fp in enumerate(trusted_raw):
                if not isinstance(fp, str):
                    raise SchemaError(
                        f"[security.keyring.trusted][{i}] must be a string"
                    )
                fp_norm = fp.replace(" ", "").upper()
                if not GPG_FINGERPRINT_RE.match(fp_norm):
                    raise SchemaError(
                        f"[security.keyring.trusted][{i}] {fp!r} is not a "
                        f"valid 40-char hex GPG fingerprint"
                    )
                cfg.security.keyring.trusted.append(fp_norm)

    fs_raw = doc.get("fs", {})
    if fs_raw:
        if not isinstance(fs_raw, dict):
            raise SchemaError("[fs] must be a table")
        _check_keys("fs", fs_raw, _ALLOWED_FS)
        mounts_raw = fs_raw.get("mounts", [])
        if mounts_raw is not None:
            if not isinstance(mounts_raw, list):
                raise SchemaError("[[fs.mounts]] must be an array of tables")
            cfg.fs.present = True
            seen_names: set[str] = set()
            seen_targets: set[str] = set()
            for i, item in enumerate(mounts_raw):
                if not isinstance(item, dict):
                    raise SchemaError(
                        f"[[fs.mounts]][{i}] must be a table"
                    )
                _check_keys(f"fs.mounts[{i}]", item, _ALLOWED_MOUNT_KEYS)
                cfg.fs.mounts.append(_validate_mount_entry(i, item, seen_names,
                                                           seen_targets))

    return cfg


def _validate_mount_entry(idx: int, item: dict, seen_names: set[str],
                          seen_targets: set[str]) -> "MountEntry":
    pfx = f"fs.mounts[{idx}]"
    name = item.get("name")
    if not isinstance(name, str) or not MOUNT_NAME_RE.match(name):
        raise SchemaError(
            f"[{pfx}].name is required and must match {MOUNT_NAME_RE.pattern}"
        )
    if name in seen_names:
        raise SchemaError(f"[{pfx}].name {name!r} duplicated")
    seen_names.add(name)

    device = item.get("device")
    if not isinstance(device, str) or not device.strip():
        raise SchemaError(f"[{pfx}].device is required (non-empty string)")
    target = item.get("target")
    if not isinstance(target, str) or not target.startswith("/"):
        raise SchemaError(f"[{pfx}].target is required and must be absolute")
    if target in seen_targets:
        raise SchemaError(f"[{pfx}].target {target!r} duplicated")
    seen_targets.add(target)
    fstype = item.get("fstype")
    if not isinstance(fstype, str) or not fstype.strip():
        raise SchemaError(f"[{pfx}].fstype is required (non-empty string)")
    options = item.get("options", "defaults")
    if not isinstance(options, str):
        raise SchemaError(f"[{pfx}].options must be a string")
    dump = item.get("dump", 0)
    if isinstance(dump, bool) or not isinstance(dump, int):
        raise SchemaError(f"[{pfx}].dump must be an integer")
    pass_ = item.get("pass", 0)
    if isinstance(pass_, bool) or not isinstance(pass_, int):
        raise SchemaError(f"[{pfx}].pass must be an integer")

    return MountEntry(
        name=name, device=device.strip(), target=target,
        fstype=fstype.strip(), options=options.strip() or "defaults",
        dump=dump, pass_=pass_,
    )


def _validate_firewall_rule(idx: int, item: dict) -> "FirewallRule":
    """Schema-validate one [[network.firewall.rules]] table and return
    a FirewallRule. Catches mutually-exclusive key combinations
    (e.g. `app` paired with `port` or `proto`) and dependent keys
    (`from-port` without `from`)."""
    pfx = f"network.firewall.rules[{idx}]"
    action = item.get("action")
    if not isinstance(action, str) or action not in _ALLOWED_FIREWALL_RULE_ACTIONS:
        raise SchemaError(
            f"[{pfx}].action is required and must be one of "
            f"{sorted(_ALLOWED_FIREWALL_RULE_ACTIONS)}, got {action!r}"
        )
    direction = item.get("direction")
    if direction is not None and (not isinstance(direction, str) or
                                  direction not in _ALLOWED_FIREWALL_DIRECTIONS):
        raise SchemaError(
            f"[{pfx}].direction must be one of "
            f"{sorted(_ALLOWED_FIREWALL_DIRECTIONS)}, got {direction!r}"
        )
    # UFW's default direction is `in`. Normalize so a TOML rule that
    # omits direction matches a parsed-ufw rule on the identity tuple.
    if direction is None:
        direction = "in"
    log = item.get("log")
    if log is not None and (not isinstance(log, str) or
                            log not in _ALLOWED_FIREWALL_LOG):
        raise SchemaError(
            f"[{pfx}].log must be one of "
            f"{sorted(_ALLOWED_FIREWALL_LOG)}, got {log!r}"
        )
    proto = item.get("proto")
    if proto is not None and (not isinstance(proto, str) or
                              proto not in _ALLOWED_FIREWALL_PROTOS):
        raise SchemaError(
            f"[{pfx}].proto must be one of "
            f"{sorted(_ALLOWED_FIREWALL_PROTOS)}, got {proto!r}"
        )
    interface = item.get("interface")
    if interface is not None and not isinstance(interface, str):
        raise SchemaError(f"[{pfx}].interface must be a string")
    from_ = item.get("from")
    if from_ is not None and not isinstance(from_, str):
        raise SchemaError(f"[{pfx}].from must be a string")
    to = item.get("to")
    if to is not None and not isinstance(to, str):
        raise SchemaError(f"[{pfx}].to must be a string")
    from_port = item.get("from-port")
    if from_port is not None:
        from_port = _check_int(f"{pfx}.from-port", from_port, minimum=1)
    to_port = item.get("to-port")
    if to_port is not None:
        to_port = _check_int(f"{pfx}.to-port", to_port, minimum=1)
    port = item.get("port")
    if port is not None:
        port = _check_int(f"{pfx}.port", port, minimum=1)
    app = item.get("app")
    if app is not None and not isinstance(app, str):
        raise SchemaError(f"[{pfx}].app must be a string")
    comment = item.get("comment")
    if comment is not None and not isinstance(comment, str):
        raise SchemaError(f"[{pfx}].comment must be a string")

    # Mutually-exclusive constraints.
    if app is not None and (port is not None or proto is not None
                            or to_port is not None or from_port is not None):
        raise SchemaError(
            f"[{pfx}] `app` is mutually exclusive with port/proto/from-port/to-port"
        )
    if from_port is not None and from_ is None:
        raise SchemaError(
            f"[{pfx}] `from-port` requires `from` to be set"
        )
    if to_port is not None and to is None and port is None:
        # to-port without `to` is meaningless; user probably wanted `port`.
        raise SchemaError(
            f"[{pfx}] `to-port` requires `to` (or use `port` shorthand)"
        )

    return FirewallRule(
        action=action, direction=direction, interface=interface, log=log,
        from_=from_, to=to, from_port=from_port, to_port=to_port,
        port=port, proto=proto, app=app, comment=comment,
    )


def load_config(path: Optional[Path] = None) -> ValidatedConfig:
    p = path or config_path()
    if not p.exists():
        raise FileNotFoundError(f"{p} not found — no declarative state to apply")
    try:
        raw = p.read_bytes()
    except OSError as e:
        raise FileNotFoundError(f"cannot read {p}: {e}") from e
    try:
        doc = tomllib.loads(raw.decode("utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise SchemaError(f"{p}: {e}") from e
    return validate_doc(doc)


# ---------------------------------------------------------------------------
# Manifest — records which drop-ins shedos-apply currently owns. Drives the
# "remove when dropped from TOML" behavior. File lives under /var/lib/shedos
# so /etc stays pacman-clean.
# ---------------------------------------------------------------------------


@dataclass
class Manifest:
    dropins: dict[str, str] = field(default_factory=dict)  # relpath → sha256

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Manifest":
        p = path or manifest_path()
        if not p.exists():
            return cls()
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        dropins = doc.get("dropins", {}) if isinstance(doc, dict) else {}
        if not isinstance(dropins, dict):
            dropins = {}
        return cls(dropins={str(k): str(v) for k, v in dropins.items()})

    def save(self, path: Optional[Path] = None) -> None:
        p = path or manifest_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        doc = {"schema": SCHEMA_VERSION, "dropins": self.dropins}
        atomic_write_text(p, json.dumps(doc, indent=2, sort_keys=True) + "\n",
                          mode=0o644)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Atomic writes — tmp + fsync + rename. Reused across every reconciler so a
# crash mid-apply never leaves a half-written file at the live path.
# ---------------------------------------------------------------------------


def atomic_write_text(path: Path, text: str, *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise


# ---------------------------------------------------------------------------
# Cross-cutting Phase 6A scaffolding: three-way merge, baseline + state
# files, format-preserving system.toml writes via tomlkit.
#
# Every Phase 6A reconciler runs the same algorithm with different
# identity tuples:
#
#   declared    — set of items in /etc/shedos/system.toml
#   live        — set of items observed on the system right now
#   last_applied— set we wrote on the previous apply
#   baseline    — set captured on first apply; protected forever
#
# Items in `baseline` are invisible to the reconciler — never adopted,
# never removed when omitted from TOML. Items elsewhere flow through
# `to_add` / `to_remove` / `to_adopt` per the merge rules below.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MergeResult:
    """Output of `threeway_merge`. Each field is a set of identity
    tuples. Reconcilers translate these into `Change` objects."""
    to_add: frozenset[tuple]      # in declared, not in live
    to_remove: frozenset[tuple]   # in last_applied, not in declared (and not baseline)
    to_adopt: frozenset[tuple]    # in live, not in baseline/last_applied/declared
    aligned: frozenset[tuple]     # in declared, in live (noop)


def threeway_merge(
    declared: set[tuple],
    live: set[tuple],
    last_applied: set[tuple],
    baseline: set[tuple],
) -> MergeResult:
    """Compute add/remove/adopt sets for the bidirectional reconciler.

    Item-by-item rules (matching the table in the Phase 6A plan):

      * baseline rows               → invisible; never in any output set
      * declared & live             → aligned (noop)
      * declared & not live         → to_add (push to live)
      * not declared & last_applied → to_remove (user removed from TOML)
      * not declared & live & not last_applied & not baseline
                                    → to_adopt (user added via raw tool)
    """
    # baseline trumps everything — strip it from the inputs first.
    declared = declared - baseline
    live = live - baseline
    last_applied = last_applied - baseline

    aligned = declared & live
    to_add = declared - live
    to_remove = (last_applied - declared) & live  # only delete what's actually there
    to_adopt = (live - declared) - last_applied
    return MergeResult(
        to_add=frozenset(to_add),
        to_remove=frozenset(to_remove),
        to_adopt=frozenset(to_adopt),
        aligned=frozenset(aligned),
    )


def load_state_set(path: Path) -> set[tuple]:
    """Load a section's last-applied or baseline state file. Each entry
    is stored as a list (JSON has no tuple type) and rehydrated to a
    tuple for set membership. Missing/unreadable file → empty set."""
    if not path.exists():
        return set()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    items = doc.get("items", []) if isinstance(doc, dict) else []
    out: set[tuple] = set()
    for item in items:
        if isinstance(item, list):
            out.add(tuple(item))
    return out


def save_state_set(path: Path, items: set[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": SCHEMA_VERSION,
        "items": sorted([list(t) for t in items]),
    }
    atomic_write_text(path, json.dumps(payload, indent=2) + "\n", mode=0o644)


def seed_baseline_if_missing(section: str, live: set[tuple]) -> set[tuple]:
    """First-apply seed: snapshot the current live state as the
    permanent baseline. On subsequent applies, return the existing
    file's contents unchanged."""
    p = baseline_path(section)
    if p.exists():
        return load_state_set(p)
    save_state_set(p, live)
    return live


def atomic_write_system_toml(
    path: Path,
    new_doc_text: str,
) -> None:
    """Replace /etc/shedos/system.toml atomically and re-validate it
    end-to-end with stdlib `tomllib` + `validate_doc()`. If the new
    text doesn't parse or doesn't validate, the live file is left
    untouched and the caller gets a SchemaError with a pointed message.

    Reconcilers should use `tomlkit` to *edit* a parsed document, then
    serialize and pass through here for the safety net.
    """
    try:
        doc = tomllib.loads(new_doc_text)
    except tomllib.TOMLDecodeError as e:
        raise SchemaError(
            f"adoption-write produced invalid TOML and was discarded: {e}"
        ) from e
    try:
        validate_doc(doc)
    except SchemaError as e:
        raise SchemaError(
            f"adoption-write produced TOML that fails schema validation "
            f"and was discarded: {e}"
        ) from e
    atomic_write_text(path, new_doc_text, mode=0o644)


# ---------------------------------------------------------------------------
# Reconciler: systemd
# ---------------------------------------------------------------------------


def _systemctl_run(args: list[str], *, check: bool = True) -> tuple[int, str]:
    cmd = systemctl_cmd() + args
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                             check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        if check:
            raise RuntimeError(f"{cmd}: {e}") from e
        return 127, str(e)
    if out.returncode != 0 and check:
        raise RuntimeError(
            f"{cmd} exited {out.returncode}: "
            f"{(out.stderr or out.stdout).strip()[:200]}"
        )
    return out.returncode, out.stdout


def _enabled_units(scope: str) -> set[str]:
    """Return the set of enabled units at the given scope ("system"|"user")."""
    args = ["list-unit-files", "--no-legend", "--no-pager", "--plain",
            "--state=enabled"]
    if scope == "user":
        args = ["--user", "--global"] + args
    rc, out = _systemctl_run(args, check=False)
    if rc != 0:
        return set()
    names: set[str] = set()
    for raw in out.splitlines():
        line = raw.strip()
        if not line:
            continue
        name = line.split(None, 1)[0]
        names.add(name)
    return names


def _systemd_change(scope: str, unit: str, action: str) -> Change:
    """Build a Change that enables/disables a single unit."""
    kind = "+" if action == "enable" else "-"
    scope_args = ["--user", "--global"] if scope == "user" else []
    summary = f"{action} {unit}"

    def apply_fn() -> None:
        _systemctl_run(scope_args + [action, unit])

    def undo_fn() -> None:
        inverse = "disable" if action == "enable" else "enable"
        _systemctl_run(scope_args + [inverse, unit], check=False)

    return Change(kind=kind, section=f"systemd.{scope}", summary=summary,
                  apply_fn=apply_fn, undo_fn=undo_fn)


def plan_systemd(cfg: ValidatedConfig) -> list[Change]:
    out: list[Change] = []
    for scope in ("system", "user"):
        enable = getattr(cfg.systemd, f"{scope}_enable")
        disable = getattr(cfg.systemd, f"{scope}_disable")
        current = _enabled_units(scope)
        for unit in sorted(enable):
            if unit not in current:
                out.append(_systemd_change(scope, unit, "enable"))
        for unit in sorted(disable):
            if unit in current:
                out.append(_systemd_change(scope, unit, "disable"))
    return out


# ---------------------------------------------------------------------------
# Reconciler: drop-ins
# ---------------------------------------------------------------------------


def _dropin_diff(relpath: str, current: Optional[str], desired: Optional[str]) -> str:
    a = (current or "").splitlines(keepends=True)
    b = (desired or "").splitlines(keepends=True)
    if a == b:
        return ""
    tag_a = f"{relpath} (current)" if current is not None else f"{relpath} (absent)"
    tag_b = f"{relpath} (declared)" if desired is not None else f"{relpath} (to remove)"
    diff = difflib.unified_diff(a, b, fromfile=tag_a, tofile=tag_b, lineterm="")
    return "".join(l if l.endswith("\n") else l + "\n" for l in diff)


def plan_dropins(cfg: ValidatedConfig,
                 manifest: Manifest) -> tuple[list[Change], Manifest]:
    """Returns the list of changes plus the NEW manifest that should be
    persisted after a successful apply. Callers should only save the new
    manifest after the whole plan succeeds; on rollback, keep the old."""
    changes: list[Change] = []
    new_dropins: dict[str, str] = {}
    etc = etc_root()

    for relpath, content in cfg.dropins.items():
        target = etc / relpath
        current = target.read_text(encoding="utf-8") if target.exists() else None
        desired_hash = sha256_text(content)
        new_dropins[relpath] = desired_hash
        if current is None:
            changes.append(Change(
                kind="+", section="drop-ins",
                summary=f"create /etc/{relpath} ({len(content)} bytes)",
                diff=_dropin_diff(relpath, None, content),
                apply_fn=_make_write_dropin(target, content),
                undo_fn=_make_remove_dropin(target, backup=None),
            ))
        elif current != content:
            changes.append(Change(
                kind="~", section="drop-ins",
                summary=f"update /etc/{relpath}",
                diff=_dropin_diff(relpath, current, content),
                apply_fn=_make_write_dropin(target, content),
                undo_fn=_make_write_dropin(target, current),
            ))

    for relpath in manifest.dropins:
        if relpath in cfg.dropins:
            continue
        target = etc / relpath
        if not target.exists():
            continue
        current = target.read_text(encoding="utf-8")
        changes.append(Change(
            kind="-", section="drop-ins",
            summary=f"remove /etc/{relpath}",
            diff=_dropin_diff(relpath, current, None),
            apply_fn=_make_remove_dropin(target, backup=current),
            undo_fn=_make_write_dropin(target, current),
        ))

    return changes, Manifest(dropins=new_dropins)


def _make_write_dropin(target: Path, content: str) -> Callable[[], None]:
    def fn() -> None:
        atomic_write_text(target, content, mode=0o644)
    return fn


def _make_remove_dropin(target: Path, *, backup: Optional[str]) -> Callable[[], None]:
    def fn() -> None:
        try:
            target.unlink()
        except FileNotFoundError:
            pass
    return fn


# ---------------------------------------------------------------------------
# Reconciler: snapper root config
# ---------------------------------------------------------------------------


SNAPPER_KEY_MAP = {
    "timeline_enabled":       ("TIMELINE_CREATE",     lambda v: "yes" if v else "no"),
    "timeline_hourly":        ("TIMELINE_LIMIT_HOURLY",  str),
    "timeline_daily":         ("TIMELINE_LIMIT_DAILY",   str),
    "timeline_weekly":        ("TIMELINE_LIMIT_WEEKLY",  str),
    "timeline_monthly":       ("TIMELINE_LIMIT_MONTHLY", str),
    "timeline_yearly":        ("TIMELINE_LIMIT_YEARLY",  str),
    "cleanup_number_limit":   ("NUMBER_LIMIT",           str),
}


def _snapper_config_path() -> Path:
    return etc_root() / "snapper" / "configs" / "root"


def _parse_snapper_config(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        out[key] = value
    return out


def _rewrite_snapper_line(text: str, key: str, value: str) -> str:
    pat = re.compile(rf'^{re.escape(key)}\s*=.*$', re.MULTILINE)
    replacement = f'{key}="{value}"'
    if pat.search(text):
        return pat.sub(replacement, text, count=1)
    if not text.endswith("\n"):
        text = text + "\n"
    return text + replacement + "\n"


def plan_snapper(cfg: ValidatedConfig) -> list[Change]:
    if cfg.snapper.is_empty():
        return []
    cfg_path = _snapper_config_path()
    if not cfg_path.exists():
        return [Change(
            kind="~", section="snapper",
            summary=f"SKIP: {cfg_path} not found (run `snapper create-config /` first)",
            apply_fn=lambda: None,
        )]
    current_text = cfg_path.read_text(encoding="utf-8")
    current = _parse_snapper_config(current_text)
    changes: list[Change] = []

    desired_pairs: list[tuple[str, str, Any]] = []
    for attr, (snapper_key, serialize) in SNAPPER_KEY_MAP.items():
        val = getattr(cfg.snapper, attr)
        if val is None:
            continue
        desired_pairs.append((snapper_key, serialize(val), val))

    new_text = current_text
    diff_lines: list[str] = []
    for snapper_key, desired_value, _raw in desired_pairs:
        cur_val = current.get(snapper_key)
        if cur_val == desired_value:
            continue
        diff_lines.append(
            f"  {snapper_key}: "
            f"{cur_val!r} → {desired_value!r}"
        )
        new_text = _rewrite_snapper_line(new_text, snapper_key, desired_value)

    if not diff_lines:
        return []

    summary = f"reconcile {len(diff_lines)} snapper key(s) in {cfg_path}"
    diff_block = "\n".join(diff_lines)

    def apply_fn() -> None:
        atomic_write_text(cfg_path, new_text, mode=0o640)

    def undo_fn() -> None:
        atomic_write_text(cfg_path, current_text, mode=0o640)

    changes.append(Change(
        kind="~", section="snapper", summary=summary,
        diff=diff_block, apply_fn=apply_fn, undo_fn=undo_fn,
    ))
    return changes


# ---------------------------------------------------------------------------
# Reconciler: pacman repos — fence-block rewrite of /etc/pacman.conf
#
# The fence markers below match those emitted by shedos-system.install so
# existing installs upgrade smoothly: the install hook writes an initial
# fence on first install, and shedos-apply takes over subsequent edits.
# ---------------------------------------------------------------------------


PACMAN_FENCE_OPEN = "# >>> shedos <<<"
PACMAN_FENCE_CLOSE = "# <<< shedos >>>"
PACMAN_FENCE_PREAMBLE = (
    "# Managed by shedos-apply — do not edit between these markers.\n"
    "# Declarative source: /etc/shedos/system.toml ([pacman.repos]).\n"
)


def _pacman_conf_path() -> Path:
    return etc_root() / "pacman.conf"


def _render_pacman_fence(repos: dict[str, PacmanRepo]) -> str:
    """Generate the exact text (including open/close fence lines) that the
    managed block should contain, given the declared repos."""
    out = [PACMAN_FENCE_OPEN, PACMAN_FENCE_PREAMBLE.rstrip()]
    for name in sorted(repos):
        repo = repos[name]
        out.append("")
        out.append(f"[{name}]")
        out.append(f"SigLevel = {repo.siglevel}")
        out.append(f"Server = {repo.server}")
    out.append(PACMAN_FENCE_CLOSE)
    return "\n".join(out) + "\n"


def _rewrite_pacman_conf(text: str, new_fence: str) -> str:
    """Replace the existing fenced block in ``text`` (if any) with
    ``new_fence``. If no fence exists, append one after a blank line."""
    lines = text.splitlines(keepends=True)
    start = end = None
    for i, line in enumerate(lines):
        if line.strip() == PACMAN_FENCE_OPEN and start is None:
            start = i
        elif line.strip() == PACMAN_FENCE_CLOSE and start is not None:
            end = i
            break
    if start is not None and end is not None:
        before = "".join(lines[:start])
        after = "".join(lines[end + 1:])
        return before + new_fence + after
    base = text if text.endswith("\n") or not text else text + "\n"
    sep = "\n" if base and not base.endswith("\n\n") else ""
    return base + sep + new_fence


def plan_pacman(cfg: ValidatedConfig) -> list[Change]:
    if cfg.pacman.is_empty():
        return []
    conf = _pacman_conf_path()
    current_text = conf.read_text(encoding="utf-8") if conf.exists() else ""
    desired_fence = _render_pacman_fence(cfg.pacman.repos)
    desired_text = _rewrite_pacman_conf(current_text, desired_fence)
    if current_text == desired_text:
        return []
    repo_names = sorted(cfg.pacman.repos) or ["(none)"]
    summary = (f"reconcile pacman fence block ({len(cfg.pacman.repos)} "
               f"repo(s): {', '.join(repo_names)})")
    diff_iter = difflib.unified_diff(
        current_text.splitlines(keepends=True),
        desired_text.splitlines(keepends=True),
        fromfile="pacman.conf (current)",
        tofile="pacman.conf (declared)",
        lineterm="",
    )
    diff = "".join(l if l.endswith("\n") else l + "\n" for l in diff_iter)

    def apply_fn() -> None:
        atomic_write_text(conf, desired_text, mode=0o644)

    def undo_fn() -> None:
        atomic_write_text(conf, current_text, mode=0o644)

    return [Change(
        kind="~", section="pacman", summary=summary,
        diff=diff, apply_fn=apply_fn, undo_fn=undo_fn,
    )]


# ---------------------------------------------------------------------------
# Reconciler: services.postgresql — declarative wrapper over the two
# existing bootstrap units. auto-init and per-user-db are user-facing knobs
# that desugar to ordinary systemctl enable/disable calls, so the actual
# mutation piggy-backs on _systemd_change.
# ---------------------------------------------------------------------------


POSTGRES_AUTO_INIT_UNIT = "shedos-pg-initdb.service"
POSTGRES_PER_USER_DB_UNIT = "shedos-pg-user-bootstrap.service"


def plan_services(cfg: ValidatedConfig) -> list[Change]:
    if cfg.services.is_empty():
        return []
    current = _enabled_units("system")
    out: list[Change] = []
    flags = [
        (cfg.services.postgresql.auto_init, POSTGRES_AUTO_INIT_UNIT,
         "services.postgresql.auto-init"),
        (cfg.services.postgresql.per_user_db, POSTGRES_PER_USER_DB_UNIT,
         "services.postgresql.per-user-db"),
    ]
    for desired, unit, label in flags:
        if desired is None:
            continue
        if desired and unit not in current:
            c = _systemd_change("system", unit, "enable")
            c.section = label
            out.append(c)
        elif not desired and unit in current:
            c = _systemd_change("system", unit, "disable")
            c.section = label
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# Reconciler: network.firewall — declarative ufw with bidirectional adoption.
#
# Three-way merge against (declared, live, last_applied, baseline). Tool-
# added rules ("ufw allow 9090") get adopted into system.toml; TOML
# removals propagate to ufw. Baseline (the rule set on first apply) is
# protected forever; on a fresh ShedOS install ufw is empty so baseline
# is the empty set, but a system that ran `ufw allow` before adopting the
# section will have those baseline rules invisible to the reconciler.
#
# Wire-format: every rule we ADD goes through `ufw <action> <args>
# comment '<text>'`. We never tag rules with a `shedos:` prefix —
# ownership is tracked via the state file, not via the comment.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FirewallLiveState:
    active: bool
    incoming: Optional[str]   # current default (deny | allow | reject)
    outgoing: Optional[str]
    routed: Optional[str]
    rules: list[FirewallRule]


def _firewall_status(check: bool = True) -> FirewallLiveState:
    """Return the live ufw state. UFW splits its disclosure across
    `status numbered` (rule numbers) and `status verbose` (default
    policies header), so we make two calls.
    """
    rc, num_out = _ufw_run_capture(["status", "numbered"], check=check)
    if rc != 0 and not check:
        return FirewallLiveState(False, None, None, None, [])
    active, rules = _parse_ufw_status_numbered(num_out)

    rc2, verb_out = _ufw_run_capture(["status", "verbose"], check=check)
    if rc2 != 0:
        # Best-effort: rules are good, defaults left as None.
        return FirewallLiveState(active, None, None, None, rules)
    incoming, outgoing, routed = _parse_ufw_defaults(verb_out)
    return FirewallLiveState(active, incoming, outgoing, routed, rules)


def _ufw_run_capture(extra_args: list[str], *, check: bool) -> tuple[int, str]:
    cmd = ufw_cmd() + extra_args
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        if check:
            raise RuntimeError(f"{cmd}: {e}") from e
        return 127, ""
    if proc.returncode != 0 and check:
        raise RuntimeError(
            f"{cmd} exited {proc.returncode}: "
            f"{(proc.stderr or proc.stdout).strip()[:200]}"
        )
    return proc.returncode, proc.stdout


_UFW_DEFAULT_RE = re.compile(
    r"Default:\s*"
    r"(?P<incoming>\w+)\s*\(incoming\),?\s*"
    r"(?P<outgoing>\w+)\s*\(outgoing\),?\s*"
    r"(?P<routed>\w+)\s*\(routed\)",
    re.IGNORECASE,
)


def _parse_ufw_defaults(text: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Pull the `Default: deny (incoming), allow (outgoing), disabled (routed)`
    line out of `ufw status verbose` output. UFW reports `disabled` for
    routed when forwarding is off; we map that to None so it never
    triggers a `ufw default disabled routed` call (not a real policy)."""
    for raw in text.splitlines():
        m = _UFW_DEFAULT_RE.search(raw)
        if not m:
            continue
        def _norm(p: str) -> Optional[str]:
            p = p.lower()
            return p if p in _ALLOWED_FIREWALL_DEFAULT_POLICIES else None
        return _norm(m.group("incoming")), _norm(m.group("outgoing")), _norm(m.group("routed"))
    return None, None, None


_UFW_RULE_RE = re.compile(
    r"^\[\s*(?P<num>\d+)\]\s+(?P<body>.+?)\s*$"
)


def _parse_ufw_status_numbered(text: str) -> tuple[bool, list[FirewallRule]]:
    """Parse `ufw status numbered` output into (active, rules).

    UFW's output isn't formal — we treat each numbered line as a
    rule, split off the comment, and decode the action + direction +
    pieces from the remaining tokens. We deduplicate v4/v6 pairs by
    rule number (UFW emits them as separate lines but they share the
    same identity tuple in our schema).
    """
    active = False
    seen: dict[int, FirewallRule] = {}
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("Status:"):
            # Match the first word after "Status:" — guard against
            # "Status: inactive" matching the substring "active".
            tail = line.split(":", 1)[1].strip().lower().split()
            active = bool(tail) and tail[0] == "active"
            continue
        m = _UFW_RULE_RE.match(line)
        if not m:
            continue
        num = int(m.group("num"))
        body = m.group("body")
        rule = _decode_ufw_rule_body(body)
        if rule is None:
            raise SchemaError(
                f"could not parse ufw rule line {num!r}: {body!r}. "
                f"Refusing to apply — aborts before any mutation."
            )
        # First occurrence wins (v4 line before v6 (v6) line).
        seen.setdefault(num, rule)
    return active, [seen[k] for k in sorted(seen)]


def _decode_ufw_rule_body(body: str) -> Optional[FirewallRule]:
    """Decode a single ufw rule line minus the `[N]` prefix into a
    FirewallRule. Returns None on unrecognized shapes (caller raises
    SchemaError to abort the apply rather than silently miscount)."""
    # Strip `(v6)` and trailing comment.
    comment: Optional[str] = None
    if "#" in body:
        body, _, comment_raw = body.partition("#")
        body = body.rstrip()
        comment = comment_raw.strip() or None
    body = body.replace("(v6)", "").rstrip()

    # Find ALLOW/DENY/REJECT/LIMIT IN/OUT/FWD splitter.
    action_re = re.compile(
        r"^(?P<dst>.+?)\s+(?P<action>ALLOW|DENY|REJECT|LIMIT)"
        r"\s+(?P<dir>IN|OUT|FWD)\s+(?P<src>.*?)\s*$",
        re.IGNORECASE,
    )
    m = action_re.match(body)
    if not m:
        return None
    dst_part = m.group("dst").strip()
    src_part = m.group("src").strip()
    action = m.group("action").lower()
    direction = {"in": "in", "out": "out", "fwd": "in"}[m.group("dir").lower()]

    # Decode dst — `<port>/<proto>` | `<addr>` | `<addr> <port>` |
    # `App profile`.
    port: Optional[int] = None
    to_port: Optional[int] = None
    proto: Optional[str] = None
    to_addr: Optional[str] = None
    app: Optional[str] = None
    # App profiles in ufw status look like "OpenSSH" with no slash/digit.
    if dst_part and not any(ch.isdigit() for ch in dst_part) and "/" not in dst_part:
        app = dst_part
    elif "/" in dst_part:
        port_str, _, proto_str = dst_part.partition("/")
        if port_str.isdigit():
            port = int(port_str)
            proto = proto_str.lower() or None
        else:
            return None
    elif dst_part.isdigit():
        port = int(dst_part)
    else:
        # Could be `<addr> <port>` or just `<addr>`.
        parts = dst_part.split()
        if len(parts) == 2 and parts[1].isdigit():
            to_addr = parts[0]
            to_port = int(parts[1])
        elif len(parts) == 1:
            to_addr = parts[0]
        else:
            return None

    from_addr: Optional[str] = None
    if src_part and src_part.lower() != "anywhere":
        from_addr = src_part

    return FirewallRule(
        action=action, direction=direction,
        interface=None,  # ufw status doesn't surface this distinctly enough
        log=None,
        from_=from_addr, to=to_addr,
        from_port=None, to_port=to_port, port=port,
        proto=proto, app=app, comment=comment,
    )


def _ufw_args_for_rule(rule: FirewallRule) -> list[str]:
    """Build the argv for `ufw <action> ...` from a FirewallRule.
    Mirrors `man ufw` rule syntax. Caller prepends the ufw binary +
    `--force`."""
    args = [rule.action]
    if rule.direction == "out":
        args.append("out")
    if rule.interface:
        args += ["on", rule.interface]
    if rule.log == "log":
        args.append("log")
    elif rule.log == "log-all":
        args.append("log-all")
    # App-profile rule shape.
    if rule.app:
        if rule.from_:
            args += ["from", rule.from_]
        args += ["to", "any", "app", rule.app]
    else:
        if rule.from_:
            args += ["from", rule.from_]
            if rule.from_port is not None:
                args += ["port", str(rule.from_port)]
        if rule.to:
            args += ["to", rule.to]
            if rule.to_port is not None:
                args += ["port", str(rule.to_port)]
        if rule.port is not None and not rule.to and not rule.from_:
            args.append(str(rule.port))
            if rule.proto:
                args[-1] = f"{rule.port}/{rule.proto}"
        elif rule.proto and (rule.from_ or rule.to):
            args += ["proto", rule.proto]
    if rule.comment:
        args += ["comment", rule.comment]
    return args


def plan_firewall(cfg: ValidatedConfig) -> list[Change]:
    """Build the firewall plan via three-way merge.

    Returns either an empty list (aligned), one Change covering all
    mutations, or two Changes (a `~` for adoption-write that touches
    system.toml + a `~` for live ufw mutations) so doctor can surface
    them distinctly. The undo function for the live Change snapshots
    the pre-apply rule set to firewall.undo.json.
    """
    fw = cfg.network.firewall
    if not fw.present:
        return []

    section_name = "firewall"
    state_p = state_path(section_name)
    baseline_p = baseline_path(section_name)
    config_p = config_path()

    try:
        live = _firewall_status(check=True)
    except RuntimeError as e:
        raise RuntimeError(f"plan_firewall: {e}") from e
    live_active = live.active
    live_rules = live.rules

    declared_set: set[tuple] = {r.to_tuple() for r in fw.rules}
    live_set: set[tuple] = {r.to_tuple() for r in live_rules}
    last_applied: set[tuple] = load_state_set(state_p)

    # First apply seeds the baseline as the empty set — bypassing the
    # baseline-protection path because the firewall ship state is "off,
    # no rules". This matches the user-approved behavior: any rules
    # already present at first apply get adopted into TOML.
    if baseline_p.exists():
        baseline = load_state_set(baseline_p)
    else:
        baseline = set()
        save_state_set(baseline_p, baseline)

    merge = threeway_merge(declared_set, live_set, last_applied, baseline)

    # Rule lookup tables for translating tuples back to FirewallRule
    # objects when we need to render argv / write TOML.
    by_tuple_declared = {r.to_tuple(): r for r in fw.rules}
    by_tuple_live = {r.to_tuple(): r for r in live_rules}
    rule_num_by_tuple: dict[tuple, int] = {}
    for i, r in enumerate(live_rules, start=1):
        rule_num_by_tuple.setdefault(r.to_tuple(), i)

    # Adoption-write needed?
    adoption_changes: list[Change] = []
    if merge.to_adopt:
        adopted_rules = [by_tuple_live[t] for t in merge.to_adopt]
        new_rules_for_toml = list(fw.rules) + adopted_rules
        new_doc_text = _render_system_toml_with_firewall(
            config_p, fw, new_rules_for_toml,
        )
        old_doc_text = config_p.read_text(encoding="utf-8") if config_p.exists() else ""
        diff_iter = difflib.unified_diff(
            old_doc_text.splitlines(keepends=True),
            new_doc_text.splitlines(keepends=True),
            fromfile="system.toml (current)",
            tofile="system.toml (after adoption)",
            lineterm="",
        )
        diff = "".join(l if l.endswith("\n") else l + "\n" for l in diff_iter)
        adopt_summary = (
            f"adopt {len(merge.to_adopt)} ufw-CLI rule(s) into "
            f"[network.firewall]"
        )

        def apply_adopt() -> None:
            atomic_write_system_toml(config_p, new_doc_text)

        def undo_adopt() -> None:
            atomic_write_text(config_p, old_doc_text, mode=0o644)

        adoption_changes.append(Change(
            kind="~", section="network.firewall (toml)",
            summary=adopt_summary, diff=diff,
            apply_fn=apply_adopt, undo_fn=undo_adopt,
        ))

    # Live mutations: defaults, service state, deletes, adds.
    # Only emit a default Change if declared differs from observed.
    default_changes: list[tuple[str, str]] = []  # (chain, policy)
    for chain in ("incoming", "outgoing", "routed"):
        declared_pol = getattr(fw, chain)
        observed_pol = getattr(live, chain)
        if declared_pol is not None and declared_pol != observed_pol:
            default_changes.append((chain, declared_pol))

    desired_active = fw.enabled
    service_action: Optional[str] = None
    if desired_active is True and not live_active:
        service_action = "enable"
    elif desired_active is False and live_active:
        service_action = "disable"

    rules_to_remove: list[tuple[int, FirewallRule]] = []
    for t in merge.to_remove:
        n = rule_num_by_tuple.get(t)
        rule_obj = by_tuple_live.get(t)
        if n is not None and rule_obj is not None:
            rules_to_remove.append((n, rule_obj))
    rules_to_remove.sort(key=lambda x: x[0], reverse=True)

    rules_to_add: list[FirewallRule] = [
        by_tuple_declared[t] for t in merge.to_add
    ]

    needs_live = (default_changes or service_action
                  or rules_to_remove or rules_to_add)
    if not needs_live and not adoption_changes:
        # Aligned. Still record the merged set so a future remove
        # propagates correctly.
        save_state_set(state_p, declared_set | merge.to_adopt)
        return []

    if not needs_live:
        # Adoption-only — write state and return.
        def apply_state_only() -> None:
            save_state_set(state_p, declared_set | merge.to_adopt)
        # Splice the state save into the adoption Change's apply_fn.
        original_apply = adoption_changes[0].apply_fn

        def combined() -> None:
            assert original_apply is not None
            original_apply()
            apply_state_only()
        adoption_changes[0].apply_fn = combined
        return adoption_changes

    summary_parts: list[str] = []
    if rules_to_add:
        summary_parts.append(f"add {len(rules_to_add)} rule(s)")
    if rules_to_remove:
        summary_parts.append(f"remove {len(rules_to_remove)} rule(s)")
    if default_changes:
        summary_parts.append(f"set {len(default_changes)} default policy(ies)")
    if service_action:
        summary_parts.append(f"{service_action} ufw")
    live_summary = "; ".join(summary_parts) or "noop"

    def apply_live() -> None:
        # Remove first (descending), then add, then defaults, then
        # service flip.
        for num, _rule in rules_to_remove:
            _ufw_run(["--force", "delete", str(num)])
        for r in rules_to_add:
            _ufw_run(["--force"] + _ufw_args_for_rule(r))
        for chain, policy in default_changes:
            _ufw_run(["--force", "default", policy, chain])
        if service_action == "enable":
            _systemctl_run(["enable", "ufw.service"])
            _ufw_run(["--force", "enable"])
        elif service_action == "disable":
            _ufw_run(["--force", "disable"])
            _systemctl_run(["disable", "ufw.service"], check=False)
        save_state_set(state_p, declared_set | merge.to_adopt)

    def undo_live() -> None:
        # Best-effort: replay the pre-apply rule set. A flock on
        # firewall.lock during apply protects against concurrent
        # mutation; on undo we just trust the snapshot.
        _ufw_run(["--force", "reset"], check=False)
        for r in live_rules:
            _ufw_run(["--force"] + _ufw_args_for_rule(r), check=False)

    live_change = Change(
        kind="~", section="network.firewall",
        summary=live_summary, diff=_firewall_diff(live_rules, rules_to_add,
                                                  rules_to_remove,
                                                  default_changes,
                                                  service_action),
        apply_fn=apply_live, undo_fn=undo_live,
    )
    return adoption_changes + [live_change]


def _ufw_run(extra_args: list[str], *, check: bool = True) -> tuple[int, str]:
    cmd = ufw_cmd() + extra_args
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                             check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        if check:
            raise RuntimeError(f"{cmd}: {e}") from e
        return 127, str(e)
    if out.returncode != 0 and check:
        raise RuntimeError(
            f"{cmd} exited {out.returncode}: "
            f"{(out.stderr or out.stdout).strip()[:200]}"
        )
    return out.returncode, out.stdout


def _firewall_diff(
    pre_rules: list[FirewallRule],
    rules_to_add: list[FirewallRule],
    rules_to_remove: list[tuple[int, FirewallRule]],
    default_changes: list[tuple[str, str]],
    service_action: Optional[str],
) -> str:
    """Render a human-readable diff for `shedman doctor --diff`."""
    lines: list[str] = []
    if service_action:
        lines.append(f"  service: {service_action}")
    for chain, policy in default_changes:
        lines.append(f"  default {chain:<8} → {policy}")
    for num, r in rules_to_remove:
        lines.append(f"  - [{num}] {_render_rule_for_diff(r)}")
    for r in rules_to_add:
        lines.append(f"  + {_render_rule_for_diff(r)}")
    return "\n".join(lines) + ("\n" if lines else "")


def _render_rule_for_diff(r: FirewallRule) -> str:
    parts = [r.action]
    if r.direction == "out":
        parts.append("out")
    if r.app:
        parts.append(f"app:{r.app}")
    if r.port is not None:
        parts.append(f"{r.port}/{r.proto}" if r.proto else str(r.port))
    if r.from_:
        parts.append(f"from {r.from_}")
    if r.to:
        parts.append(f"to {r.to}")
    if r.comment:
        parts.append(f"# {r.comment}")
    return " ".join(parts)


def _render_system_toml_with_firewall(
    path: Path,
    fw: FirewallSection,
    new_rules: list[FirewallRule],
) -> str:
    """Use tomlkit to round-trip /etc/shedos/system.toml, replacing only
    the `[network.firewall].rules` array with the post-merge rule list.
    Top-level + non-firewall sections + comments are byte-preserved
    (modulo tomlkit's normal serialization).

    Imported lazily so the rest of apply_core.py keeps stdlib-only.
    """
    import tomlkit  # noqa: PLC0415

    text = path.read_text(encoding="utf-8") if path.exists() else ""
    doc = tomlkit.parse(text)

    network = doc.get("network")
    if network is None:
        network = tomlkit.table()
        doc["network"] = network
    firewall = network.get("firewall")
    if firewall is None:
        firewall = tomlkit.table()
        network["firewall"] = firewall

    # Preserve scalar keys already in the file (enabled, incoming, ...).
    rules_aot = tomlkit.aot()
    for rule in new_rules:
        t = tomlkit.table()
        if rule.action is not None:
            t.add("action", rule.action)
        # `direction = "in"` is the UFW default; omit it from TOML to
        # keep adoption-writes minimal. Only emit if explicitly "out".
        if rule.direction is not None and rule.direction != "in":
            t.add("direction", rule.direction)
        if rule.interface is not None:
            t.add("interface", rule.interface)
        if rule.log is not None:
            t.add("log", rule.log)
        if rule.from_ is not None:
            t.add("from", rule.from_)
        if rule.to is not None:
            t.add("to", rule.to)
        if rule.from_port is not None:
            t.add("from-port", rule.from_port)
        if rule.to_port is not None:
            t.add("to-port", rule.to_port)
        if rule.port is not None:
            t.add("port", rule.port)
        if rule.proto is not None:
            t.add("proto", rule.proto)
        if rule.app is not None:
            t.add("app", rule.app)
        if rule.comment is not None:
            t.add("comment", rule.comment)
        rules_aot.append(t)
    firewall["rules"] = rules_aot

    return tomlkit.dumps(doc)


# ---------------------------------------------------------------------------
# Reconciler: security.keyring — declarative pacman-key trust with
# bidirectional adoption.
#
# Posture: warn-don't-remove. TOML omission of a previously-managed
# fingerprint emits a `⚠` Change that logs but never runs
# `pacman-key --delete`. Users must remove trust by hand.
#
# Identity = 40-char upper-hex GPG fingerprint string (no spaces).
#
# Live-state source: `pacman-key --list-keys --with-colons`. We treat
# every `fpr:` record as locally-trusted on this system.
# ---------------------------------------------------------------------------


_PACMAN_KEY_FPR_RE = re.compile(r"^fpr:::::::::([0-9A-F]{40}):", re.MULTILINE)


def _list_pacman_key_fingerprints(check: bool = True) -> set[str]:
    cmd = pacman_key_cmd() + ["--list-keys", "--with-colons"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        if check:
            raise RuntimeError(f"{cmd}: {e}") from e
        return set()
    if proc.returncode != 0:
        if check:
            raise RuntimeError(
                f"{cmd} exited {proc.returncode}: "
                f"{(proc.stderr or proc.stdout).strip()[:200]}"
            )
        return set()
    return {m.group(1) for m in _PACMAN_KEY_FPR_RE.finditer(proc.stdout)}


def plan_keyring(cfg: ValidatedConfig) -> list[Change]:
    if not cfg.security.keyring.present:
        return []

    section_name = "keyring"
    state_p = state_path(section_name)
    baseline_p = baseline_path(section_name)
    config_p = config_path()

    declared: set[tuple] = {(fp,) for fp in cfg.security.keyring.trusted}
    live: set[tuple] = {(fp,) for fp in _list_pacman_key_fingerprints(check=True)}
    last_applied: set[tuple] = load_state_set(state_p)

    if baseline_p.exists():
        baseline = load_state_set(baseline_p)
    else:
        # First apply: snapshot the entire live keyring as baseline.
        # The shedos install-time fingerprints become invisible forever.
        baseline = set(live)
        save_state_set(baseline_p, baseline)

    merge = threeway_merge(declared, live, last_applied, baseline)

    changes: list[Change] = []

    # Adoption-write: live - baseline - last_applied - declared.
    if merge.to_adopt:
        adopted_fps = sorted(t[0] for t in merge.to_adopt)
        new_trusted = list(cfg.security.keyring.trusted) + adopted_fps
        new_doc_text = _render_system_toml_with_keyring(config_p, new_trusted)
        old_doc_text = config_p.read_text(encoding="utf-8") if config_p.exists() else ""
        diff_iter = difflib.unified_diff(
            old_doc_text.splitlines(keepends=True),
            new_doc_text.splitlines(keepends=True),
            fromfile="system.toml (current)",
            tofile="system.toml (after adoption)",
            lineterm="",
        )
        diff = "".join(l if l.endswith("\n") else l + "\n" for l in diff_iter)
        summary = (f"adopt {len(adopted_fps)} pacman-key trusted "
                   f"fingerprint(s) into [security.keyring]")

        def apply_adopt() -> None:
            atomic_write_system_toml(config_p, new_doc_text)

        def undo_adopt() -> None:
            atomic_write_text(config_p, old_doc_text, mode=0o644)

        changes.append(Change(
            kind="~", section="security.keyring (toml)",
            summary=summary, diff=diff,
            apply_fn=apply_adopt, undo_fn=undo_adopt,
        ))

    # `live` here is "fingerprints that are locally-signed already".
    # Imported-but-not-signed keys aren't visible to us; we'll discover
    # they're missing only when `pacman-key --lsign-key` fails. That's
    # surfaced as a RuntimeError → exit 1 with a pointed message.

    # Warn-don't-remove: items in last_applied but not declared, that
    # are still live (i.e. not the user already cleaning up by hand).
    warn_fps = sorted(t[0] for t in merge.to_remove)
    if warn_fps:
        for fp in warn_fps:
            changes.append(Change(
                kind="~", section="security.keyring",
                summary=(f"⚠ {fp[:8]}…{fp[-4:]} dropped from TOML but stays "
                         f"locally-signed (run `pacman-key --delete {fp}` to "
                         f"actually revoke)"),
                apply_fn=lambda: None,
                undo_fn=None,
            ))

    # We also need to update last_applied even when there are no
    # mutations — to record the merged "post-apply" set so future
    # removals work. The state-save needs to happen on adoption-only
    # paths too.
    if not changes and not merge.to_add:
        save_state_set(state_p, declared | merge.to_adopt)
        return []

    # Live additions: lsign each declared fingerprint not yet trusted.
    add_fps = sorted(t[0] for t in merge.to_add)

    if add_fps:
        def apply_lsign() -> None:
            for fp in add_fps:
                _pacman_key_run(["--lsign-key", fp])
            save_state_set(state_p, declared | merge.to_adopt)

        # No undo for lsign — it's idempotent; rollback is no-op.
        changes.append(Change(
            kind="+", section="security.keyring",
            summary=f"locally-sign {len(add_fps)} fingerprint(s)",
            diff="\n".join(f"  + {fp}" for fp in add_fps) + "\n",
            apply_fn=apply_lsign,
        ))
    else:
        # Adoption-only or warn-only: still save the merged state.
        # Splice into the first Change's apply_fn.
        prev_apply = changes[0].apply_fn

        def combined() -> None:
            if prev_apply is not None:
                prev_apply()
            save_state_set(state_p, declared | merge.to_adopt)
        changes[0].apply_fn = combined

    return changes


def _pacman_key_run(extra_args: list[str], *, check: bool = True) -> tuple[int, str]:
    cmd = pacman_key_cmd() + extra_args
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                             check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        if check:
            raise RuntimeError(f"{cmd}: {e}") from e
        return 127, str(e)
    if out.returncode != 0 and check:
        raise RuntimeError(
            f"{cmd} exited {out.returncode}: "
            f"{(out.stderr or out.stdout).strip()[:200]}"
        )
    return out.returncode, out.stdout


def _render_system_toml_with_keyring(
    path: Path,
    new_trusted: list[str],
) -> str:
    """Round-trip system.toml replacing the [security.keyring].trusted
    array with the post-merge fingerprint list. Sorted for determinism."""
    import tomlkit  # noqa: PLC0415

    text = path.read_text(encoding="utf-8") if path.exists() else ""
    doc = tomlkit.parse(text)

    security = doc.get("security")
    if security is None:
        security = tomlkit.table()
        doc["security"] = security
    keyring = security.get("keyring")
    if keyring is None:
        keyring = tomlkit.table()
        security["keyring"] = keyring

    arr = tomlkit.array()
    arr.multiline(True)
    for fp in sorted(set(new_trusted)):
        arr.append(fp)
    keyring["trusted"] = arr

    return tomlkit.dumps(doc)


# ---------------------------------------------------------------------------
# Reconciler: fs.mounts — declarative /etc/fstab entries with baseline
# protection for Calamares' install-time lines.
#
# Posture: reconcile (TOML removal → fstab line removal), but ONLY for
# non-baseline entries. The baseline (every fstab entry on first apply)
# is permanently invisible.
#
# Identity = (device, target). `options`/`fstype`/`dump`/`pass` changes
# are updates, not adds/removes.
#
# Tool-managed entries live inside a marker fence at the END of fstab:
#     # >>> shedos-mounts <<<
#     <device>\t<target>\t<fstype>\t<options>\t<dump>\t<pass>
#     # <<< shedos-mounts >>>
# Baseline entries stay outside the fence, byte-preserved.
# ---------------------------------------------------------------------------


_FSTAB_FENCE_OPEN = "# >>> shedos-mounts <<<"
_FSTAB_FENCE_CLOSE = "# <<< shedos-mounts >>>"


def _parse_fstab(text: str) -> tuple[list[MountEntry], list[str]]:
    """Parse /etc/fstab into (entries, raw_lines_outside_fence).

    Returns:
      entries — every non-comment, non-blank fstab line decoded into a
                MountEntry. We auto-name unnamed entries by slugifying
                the target.
      raw_lines_outside_fence — all original lines verbatim (including
                comments + blanks) but EXCLUDING anything between the
                shedos-mounts fence markers. Used to reconstruct fstab
                while preserving non-tool content byte-identically.
    """
    entries: list[MountEntry] = []
    out_lines: list[str] = []
    in_fence = False
    name_counter: dict[str, int] = {}
    for raw in text.splitlines(keepends=False):
        stripped = raw.strip()
        if stripped == _FSTAB_FENCE_OPEN:
            in_fence = True
            continue
        if stripped == _FSTAB_FENCE_CLOSE:
            in_fence = False
            continue
        if in_fence:
            # Tool-managed area. Parse if it's a real entry; skip
            # comments/blanks (we own the fence body).
            if stripped and not stripped.startswith("#"):
                e = _parse_fstab_line(stripped, name_counter)
                if e is not None:
                    entries.append(e)
            continue
        # Outside fence: preserve verbatim.
        out_lines.append(raw)
        if stripped and not stripped.startswith("#"):
            e = _parse_fstab_line(stripped, name_counter)
            if e is not None:
                entries.append(e)
    return entries, out_lines


def _parse_fstab_line(line: str,
                       name_counter: dict[str, int]) -> Optional[MountEntry]:
    parts = line.split()
    if len(parts) < 4:
        return None
    device, target, fstype, options = parts[0], parts[1], parts[2], parts[3]
    # Be strict about target — only absolute paths and `none` (swap) are
    # valid fstab targets. Anything else means the line is malformed
    # (or we're scanning shell prose / comment text). Silently skip.
    if not (target.startswith("/") or target == "none"):
        return None
    dump = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
    pass_ = int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else 0
    name = _slugify_target(target, name_counter)
    return MountEntry(name=name, device=device, target=target, fstype=fstype,
                      options=options, dump=dump, pass_=pass_)


def _slugify_target(target: str, counter: dict[str, int]) -> str:
    """Map /mnt/data → 'data', /mnt/nfs-home → 'nfs-home', `/` → 'root'."""
    if target == "/":
        base = "root"
    else:
        # Last non-empty path component, lowercased, non-alnum→hyphen.
        last = target.rstrip("/").rsplit("/", 1)[-1]
        base = re.sub(r"[^a-z0-9._-]+", "-", last.lower()) or "mount"
    n = counter.get(base, 0) + 1
    counter[base] = n
    if n == 1:
        return base
    return f"{base}-{n}"


def _render_fstab(out_lines: list[str], managed: list[MountEntry]) -> str:
    """Serialize fstab — original non-fence lines, then a fresh fence
    block holding the managed entries. Trailing newline guaranteed."""
    body_parts = list(out_lines)
    while body_parts and body_parts[-1].strip() == "":
        body_parts.pop()
    out = "\n".join(body_parts)
    if out:
        out += "\n"
    if managed:
        out += "\n" + _FSTAB_FENCE_OPEN + "\n"
        out += "# Managed by shedos-apply — do not edit between these markers.\n"
        out += "# Declarative source: /etc/shedos/system.toml ([fs.mounts]).\n"
        for e in managed:
            out += e.to_fstab_line() + "\n"
        out += _FSTAB_FENCE_CLOSE + "\n"
    return out


def plan_mounts(cfg: ValidatedConfig) -> list[Change]:
    if not cfg.fs.present:
        return []

    section_name = "mounts"
    state_p = state_path(section_name)
    baseline_p = baseline_path(section_name)
    config_p = config_path()
    fstab = fstab_path()

    if not fstab.exists():
        return []

    fstab_text = fstab.read_text(encoding="utf-8")
    live_entries, outside_fence_lines = _parse_fstab(fstab_text)

    declared_entries = {e.identity(): e for e in cfg.fs.mounts}
    live_by_id = {e.identity(): e for e in live_entries}

    declared: set[tuple] = set(declared_entries)
    live: set[tuple] = set(live_by_id)
    last_applied: set[tuple] = load_state_set(state_p)

    if baseline_p.exists():
        baseline = load_state_set(baseline_p)
    else:
        # First apply: snapshot every existing fstab entry as baseline.
        baseline = set(live)
        save_state_set(baseline_p, baseline)

    merge = threeway_merge(declared, live, last_applied, baseline)

    changes: list[Change] = []

    # Adoption-write: live - baseline - last_applied - declared.
    if merge.to_adopt:
        # Sort adopted entries by target so the adoption-write is
        # deterministic across runs (frozenset iteration is not).
        adopted_sorted = sorted(merge.to_adopt, key=lambda t: t[1])
        adopted = [live_by_id[t] for t in adopted_sorted]
        new_for_toml = list(cfg.fs.mounts) + adopted
        new_doc_text = _render_system_toml_with_mounts(config_p, new_for_toml)
        old_doc_text = config_p.read_text(encoding="utf-8") if config_p.exists() else ""
        diff_iter = difflib.unified_diff(
            old_doc_text.splitlines(keepends=True),
            new_doc_text.splitlines(keepends=True),
            fromfile="system.toml (current)",
            tofile="system.toml (after adoption)",
            lineterm="",
        )
        diff = "".join(l if l.endswith("\n") else l + "\n" for l in diff_iter)
        summary = (f"adopt {len(adopted)} hand-edited fstab entry(ies) "
                   f"into [[fs.mounts]]")

        def apply_adopt() -> None:
            atomic_write_system_toml(config_p, new_doc_text)

        def undo_adopt() -> None:
            atomic_write_text(config_p, old_doc_text, mode=0o644)

        changes.append(Change(
            kind="~", section="fs.mounts (toml)",
            summary=summary, diff=diff,
            apply_fn=apply_adopt, undo_fn=undo_adopt,
        ))

    # Compute the post-merge "managed" set: declared + adopted, minus
    # baseline (which never lands in the fence).
    managed_ids = sorted((declared | merge.to_adopt) - baseline,
                         key=lambda t: t[1])
    # Resolve each managed id to a MountEntry — prefer declared shape
    # over live (lets options/fstype updates take effect).
    managed_entries: list[MountEntry] = []
    for mid in managed_ids:
        managed_entries.append(declared_entries.get(mid) or live_by_id[mid])

    # Outside-fence lines — strip out tool-managed entries that lived
    # outside the fence (they're being moved into it).
    new_outside: list[str] = []
    managed_target_set = {e.target for e in managed_entries}
    for raw in outside_fence_lines:
        stripped = raw.strip()
        if stripped and not stripped.startswith("#"):
            parts = stripped.split()
            if len(parts) >= 2 and parts[1] in managed_target_set:
                # Drop — moves into the fence.
                continue
        new_outside.append(raw)

    desired_text = _render_fstab(new_outside, managed_entries)
    if desired_text != fstab_text:
        diff_iter = difflib.unified_diff(
            fstab_text.splitlines(keepends=True),
            desired_text.splitlines(keepends=True),
            fromfile="fstab (current)",
            tofile="fstab (declared)",
            lineterm="",
        )
        diff = "".join(l if l.endswith("\n") else l + "\n" for l in diff_iter)
        n_added = len(merge.to_add)
        n_removed = len(merge.to_remove)
        summary = (f"reconcile fstab fence ({len(managed_entries)} managed "
                   f"entry(ies); +{n_added}, -{n_removed})")

        def apply_fstab() -> None:
            atomic_write_text(fstab, desired_text, mode=0o644)
            save_state_set(state_p, declared | merge.to_adopt)

        def undo_fstab() -> None:
            atomic_write_text(fstab, fstab_text, mode=0o644)

        changes.append(Change(
            kind="~", section="fs.mounts",
            summary=summary, diff=diff,
            apply_fn=apply_fstab, undo_fn=undo_fstab,
        ))
    else:
        # Aligned. Still update last_applied to record adoption.
        if changes:
            prev = changes[0].apply_fn

            def combined() -> None:
                if prev is not None:
                    prev()
                save_state_set(state_p, declared | merge.to_adopt)
            changes[0].apply_fn = combined
        else:
            save_state_set(state_p, declared | merge.to_adopt)

    return changes


def _render_system_toml_with_mounts(
    path: Path,
    new_entries: list[MountEntry],
) -> str:
    import tomlkit  # noqa: PLC0415

    text = path.read_text(encoding="utf-8") if path.exists() else ""
    doc = tomlkit.parse(text)

    fs = doc.get("fs")
    if fs is None:
        fs = tomlkit.table()
        doc["fs"] = fs

    aot = tomlkit.aot()
    for e in new_entries:
        t = tomlkit.table()
        t.add("name", e.name)
        t.add("device", e.device)
        t.add("target", e.target)
        t.add("fstype", e.fstype)
        if e.options != "defaults":
            t.add("options", e.options)
        if e.dump != 0:
            t.add("dump", e.dump)
        if e.pass_ != 0:
            t.add("pass", e.pass_)
        aot.append(t)
    fs["mounts"] = aot

    return tomlkit.dumps(doc)


# ---------------------------------------------------------------------------
# Aggregate planner
# ---------------------------------------------------------------------------


def build_plan(cfg: ValidatedConfig,
               manifest: Manifest) -> tuple[Plan, Manifest]:
    changes: list[Change] = []
    changes.extend(plan_systemd(cfg))
    dropin_changes, new_manifest = plan_dropins(cfg, manifest)
    changes.extend(dropin_changes)
    changes.extend(plan_snapper(cfg))
    changes.extend(plan_pacman(cfg))
    changes.extend(plan_services(cfg))
    changes.extend(plan_firewall(cfg))
    changes.extend(plan_keyring(cfg))
    changes.extend(plan_mounts(cfg))
    return Plan(changes=changes), new_manifest


def plan_hash(plan: Plan) -> str:
    """Stable fingerprint of the plan — used by shedos-doctor to decide
    whether drift is genuinely new since the last notification."""
    h = hashlib.sha256()
    for c in plan.changes:
        h.update(c.kind.encode("utf-8"))
        h.update(b"\x00")
        h.update(c.section.encode("utf-8"))
        h.update(b"\x00")
        h.update(c.summary.encode("utf-8"))
        h.update(b"\x01")
    return h.hexdigest()

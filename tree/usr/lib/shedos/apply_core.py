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
    SHEDOS_APPLY_SYSTEMCTL      replaces "systemctl" (shlex-split)
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


def config_path() -> Path:
    return etc_root() / "shedos" / "system.toml"


def manifest_path() -> Path:
    return state_root() / "apply.state.json"


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


_ALLOWED_TOP = {"schema", "systemd", "drop-ins", "snapper"}
_ALLOWED_SYSTEMD = {"system", "user"}
_ALLOWED_SYSTEMD_SUB = {"enable", "disable"}
_ALLOWED_SNAPPER = {"timeline", "cleanup"}
_ALLOWED_SNAPPER_TIMELINE = {"enabled", "hourly", "daily", "weekly",
                             "monthly", "yearly"}
_ALLOWED_SNAPPER_CLEANUP = {"number"}
_ALLOWED_SNAPPER_CLEANUP_NUMBER = {"limit"}

UNIT_NAME_RE = re.compile(r"^[A-Za-z0-9@:_.\-\\x]+\.(service|timer|socket|target|mount|path|slice)$")
DROPIN_KEY_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-/]*\.(conf|json|rules|nsswitch|hosts|cfg)$")


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
class ValidatedConfig:
    schema_version: int = SCHEMA_VERSION
    systemd: SystemdSection = field(default_factory=SystemdSection)
    dropins: dict[str, str] = field(default_factory=dict)
    snapper: SnapperSection = field(default_factory=SnapperSection)


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

    return cfg


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
# Aggregate planner
# ---------------------------------------------------------------------------


def build_plan(cfg: ValidatedConfig,
               manifest: Manifest) -> tuple[Plan, Manifest]:
    changes: list[Change] = []
    changes.extend(plan_systemd(cfg))
    dropin_changes, new_manifest = plan_dropins(cfg, manifest)
    changes.extend(dropin_changes)
    changes.extend(plan_snapper(cfg))
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

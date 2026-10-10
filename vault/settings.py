"""The environment's settings guard: GNOME keys locked through a root-owned dconf system database.

Model: a manifest of (schema, key, value). `locked` means the system db supplies the values and forbids the
user db from overriding them. `unlock` removes only the lock file and opens a window; inside it the owner edits
and `commit`s new values (free, tightening). If the window ends uncommitted, the lock comes back with the old
values, so a forgotten unlock never leaves the environment open.
"""
import datetime as dt
import json
import os
import re
import subprocess
import tempfile
from collections import namedtuple

DB_NAME = "wenv"
WINDOW = dt.timedelta(minutes=20)
MAX_KEYS = 80
MAX_VALUE = 2000
PROFILE_LINES = ("user-db:user", f"system-db:{DB_NAME}")

# Left unlocked on purpose: `wenv-color` toggles the grayscale extension by (de)activating it, which writes
# enabled-extensions, and the extension does not react to filter-active changes.
EXCLUDED = frozenset({
    ("org.gnome.shell", "enabled-extensions"),
    ("org.gnome.shell.extensions.colorblind-filters", "filter-active"),
})
ALLOWED_SCHEMAS = (
    "org.gnome.desktop.interface", "org.gnome.desktop.background", "org.gnome.desktop.screensaver",
    "org.gnome.desktop.notifications", "org.gnome.desktop.search-providers", "org.gnome.shell",
    "org.gnome.shell.keybindings", "org.gnome.shell.extensions.",
)
SCHEMA_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{1,120}$")
KEY_RE = re.compile(r"^[a-z][a-z0-9-]{0,80}$")
LINE_RE = re.compile(r'^\s*"([^"|]+)\|([^"|]+)\|(.*)"\s*$')

Entry = namedtuple("Entry", "schema key value")


class SettingsError(Exception):
    pass


def key_path(schema, key):
    """The dconf path of a key, by the same rule lib.sh:key_path uses."""
    return "/" + schema.replace(".", "/") + "/" + key


def parse_lib_text(text):
    """Entries from lib.sh's STAGE_* arrays. Plain text matching only: the file is never executed."""
    entries, in_array = [], False
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^STAGE_[A-Z_]+=\($", stripped):
            in_array = True
            continue
        if in_array and stripped == ")":
            in_array = False
            continue
        if in_array:
            m = LINE_RE.match(line)
            if m:
                entries.append(Entry(m.group(1), m.group(2), m.group(3)))
    return entries


def _valid_variant(value):
    try:
        import gi
        gi.require_version("GLib", "2.0")
        from gi.repository import GLib
    except (ImportError, ValueError):
        return bool(value.strip())                       # no GLib: the structural checks above still apply
    try:
        GLib.Variant.parse(None, value)
        return True
    except Exception:                                    # noqa: BLE001 - GLib.Error and friends
        return False


def validate_entries(raw):
    """Clean list of Entry. Raises SettingsError naming the first problem. Excluded keys are dropped silently."""
    if not isinstance(raw, (list, tuple)):
        raise SettingsError("entries must be a list")
    out, seen = [], set()
    for item in raw:
        if isinstance(item, dict):
            item = (item.get("schema"), item.get("key"), item.get("value"))
        if not (isinstance(item, (list, tuple)) and len(item) == 3 and all(isinstance(x, str) for x in item)):
            raise SettingsError(f"bad entry: {item!r}")
        schema, key, value = (x.strip() if i < 2 else x.strip() for i, x in enumerate(item))
        if (schema, key) in EXCLUDED:
            continue
        if not SCHEMA_RE.match(schema) or not KEY_RE.match(key):
            raise SettingsError(f"not a settings key: {schema} {key}")
        if not any(schema == a or (a.endswith(".") and schema.startswith(a)) for a in ALLOWED_SCHEMAS):
            raise SettingsError(f"schema not covered by the vault: {schema}")
        if not value or len(value) > MAX_VALUE or "\n" in value or "\0" in value:
            raise SettingsError(f"unusable value for {schema} {key}")
        if not _valid_variant(value):
            raise SettingsError(f"value is not valid GVariant text for {schema} {key}: {value}")
        if (schema, key) in seen:
            raise SettingsError(f"duplicate key: {schema} {key}")
        seen.add((schema, key))
        out.append(Entry(schema, key, value))
    if not out:
        raise SettingsError("no lockable keys")
    if len(out) > MAX_KEYS:
        raise SettingsError(f"too many keys ({len(out)} > {MAX_KEYS})")
    return out


def render_keyfile(entries):
    groups = {}
    for e in entries:
        groups.setdefault(key_path(e.schema, e.key).rsplit("/", 1)[0].lstrip("/"), []).append(e)
    lines = ["# Written by wenv-vaultd. Do not edit; use `wenv-vault settings`.", ""]
    for group in sorted(groups):
        lines.append(f"[{group}]")
        lines.extend(f"{e.key}={e.value}" for e in sorted(groups[group], key=lambda x: x.key))
        lines.append("")
    return "\n".join(lines)


def render_locks(entries):
    return "\n".join(sorted(key_path(e.schema, e.key) for e in entries)) + "\n"


def _atomic_write(path, text, mode=0o644):
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o755, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".wenv-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def dconf_update(dconf_dir):
    """Recompile our database from its keyfile directory: what `dconf update` does for each *.d directory."""
    source = os.path.join(dconf_dir, "db", f"{DB_NAME}.d")
    target = os.path.join(dconf_dir, "db", DB_NAME)
    r = subprocess.run(["/usr/bin/dconf", "compile", target, source], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise SettingsError(f"dconf compile failed: {r.stderr.strip() or r.stdout.strip()}")


class Guard:
    """The on-disk state. `dconf_dir` is /etc/dconf in production; `run_update(dconf_dir)` is injectable."""

    def __init__(self, dconf_dir, manifest_path, run_update=None, clock=dt.datetime.now):
        self.dconf_dir = dconf_dir
        self.manifest_path = manifest_path
        self.run_update = run_update or dconf_update
        self.clock = clock

    # --- paths --------------------------------------------------------------------------------------------------
    @property
    def profile(self):
        return os.path.join(self.dconf_dir, "profile", "user")

    @property
    def values_file(self):
        return os.path.join(self.dconf_dir, "db", f"{DB_NAME}.d", "00-wenv-vault")

    @property
    def locks_file(self):
        return os.path.join(self.dconf_dir, "db", f"{DB_NAME}.d", "locks", "wenv-vault")

    # --- manifest -----------------------------------------------------------------------------------------------
    def manifest(self):
        try:
            with open(self.manifest_path, encoding="utf-8") as fh:
                m = json.load(fh)
            entries = [Entry(e["schema"], e["key"], e["value"]) for e in m["keys"]]
            return {"entries": entries, "locked": bool(m["locked"]), "unlocked_until": m.get("unlocked_until")}
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise SettingsError(f"settings manifest unreadable: {exc}") from exc

    def _save_manifest(self, entries, locked, until):
        _atomic_write(self.manifest_path, json.dumps({
            "version": 1,
            "keys": [{"schema": e.schema, "key": e.key, "value": e.value} for e in entries],
            "locked": locked,
            "unlocked_until": until,
        }, indent=1, sort_keys=True) + "\n")

    # --- the dconf files ----------------------------------------------------------------------------------------
    def _ensure_profile(self):
        try:
            with open(self.profile, encoding="utf-8") as fh:
                lines = [l.rstrip("\n") for l in fh]
        except FileNotFoundError:
            lines = []
        if PROFILE_LINES[1] in lines:
            return
        if not lines:
            lines = list(PROFILE_LINES)
        else:
            lines.append(PROFILE_LINES[1])           # keep whatever the profile already said, add ours
        _atomic_write(self.profile, "\n".join(lines) + "\n")

    def _snapshot(self):
        out = {}
        for path in (self.values_file, self.locks_file, self.profile):
            try:
                with open(path, encoding="utf-8") as fh:
                    out[path] = fh.read()
            except OSError:
                out[path] = None
        return out

    def _restore(self, snap):
        for path, text in snap.items():
            if text is None:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            else:
                _atomic_write(path, text)

    def _apply(self, entries, locked):
        """Write values (+ locks when locked) and recompile. Rolls the files back if the compile fails."""
        snap = self._snapshot()
        try:
            self._ensure_profile()
            _atomic_write(self.values_file, render_keyfile(entries))
            if locked:
                _atomic_write(self.locks_file, render_locks(entries))
            else:
                try:
                    os.unlink(self.locks_file)
                except FileNotFoundError:
                    pass
            self.run_update(self.dconf_dir)
        except BaseException:
            self._restore(snap)
            raise

    # --- operations ---------------------------------------------------------------------------------------------
    def status(self):
        m = self.manifest()
        if m is None:
            return {"initialized": False, "locked": False, "keys": 0, "unlocked_until": None}
        return {"initialized": True, "locked": m["locked"], "keys": len(m["entries"]),
                "unlocked_until": m["unlocked_until"]}

    def init(self, raw_entries):
        if self.manifest() is not None:
            raise SettingsError("already initialized; unlock (120 questions) and commit to change it")
        entries = validate_entries(raw_entries)
        self._apply(entries, locked=True)
        self._save_manifest(entries, True, None)
        return len(entries)

    def commit(self, raw_entries):
        """Lock `raw_entries`. While locked this may only repeat the current values; the new values must be
        committed from an open window. Returns the number of keys."""
        m = self.manifest()
        if m is None:
            raise SettingsError("not initialized; run `wenv-vault settings init`")
        entries = validate_entries(raw_entries)
        if m["locked"]:
            if sorted(entries) != sorted(m["entries"]):
                raise SettingsError("locked: new values need the 120-question unlock first")
            return len(entries)
        self._apply(entries, locked=True)
        self._save_manifest(entries, True, None)
        return len(entries)

    def unlock(self):
        """Open the window. The caller has already checked the 120-question ticket."""
        m = self.manifest()
        if m is None:
            raise SettingsError("not initialized; nothing to unlock")
        now = self.clock()
        if not m["locked"]:
            return m["unlocked_until"]
        until = (now + WINDOW).isoformat(timespec="seconds")
        self._apply(m["entries"], locked=False)
        self._save_manifest(m["entries"], False, until)
        return until

    def tick(self):
        """Close an expired window, putting the old values back. True if something changed."""
        m = self.manifest()
        if m is None or m["locked"]:
            return False
        until = m["unlocked_until"]
        if until and self.clock() < dt.datetime.fromisoformat(until):
            return False
        self._apply(m["entries"], locked=True)
        self._save_manifest(m["entries"], True, None)
        return True

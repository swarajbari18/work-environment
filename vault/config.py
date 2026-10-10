"""Paths and the on-disk policy of the vault daemon.

The policy is a small JSON file written only by the daemon (root-owned in production):
  {"version": 1, "owner_uid": 1000, "enforce": false, "apps": [{"name", "comm", "exe_prefix", "env_any", "launch"}]}
Anything unreadable or invalid makes the daemon fail open (no kills) and say so, never guess.
"""
import json
import os
import re
import tempfile

import matcher

VERSION = 1
SOCKET_NAME = "vaultd.sock"

# Things a vault rule must never be able to match, whatever the owner or an agent asks for.
DENY_COMMS = frozenset({
    "init", "systemd", "systemd-logind", "systemd-journal", "dbus-daemon", "dbus-broker", "dbus-broker-lau",
    "gnome-shell", "gnome-session-b", "gnome-session-binary", "Xwayland", "mutter", "gdm3", "gdm", "sshd",
    "NetworkManager", "polkitd", "agetty", "login", "wenv-vaultd", "vaultd.py",
})
BROAD_PREFIXES = frozenset({
    "", "/", "/usr", "/usr/bin", "/usr/lib", "/usr/libexec", "/usr/local", "/usr/local/bin", "/usr/share",
    "/usr/games", "/bin", "/sbin", "/lib", "/lib64", "/opt", "/home", "/snap", "/snap/bin", "/var", "/etc", "/run",
    "/tmp", "/proc", "/sys", "/dev",
})
DENY_EXE_PREFIXES = (
    "/usr/lib/systemd/", "/lib/systemd/", "/usr/bin/gnome-shell", "/usr/libexec/gnome-shell",
    "/usr/bin/Xwayland", "/opt/wenv-vault", "/usr/sbin/", "/sbin/",
)
COMMON_ENV = frozenset({
    "HOME", "PATH", "USER", "LOGNAME", "LANG", "SHELL", "PWD", "TERM", "DISPLAY", "WAYLAND_DISPLAY",
    "XDG_RUNTIME_DIR", "XDG_SESSION_TYPE", "DBUS_SESSION_BUS_ADDRESS", "XDG_CURRENT_DESKTOP", "LC_ALL",
})
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
MAX_ITEMS = 16
MAX_TEXT = 300

DEFAULT_APPS = [
    {"name": "steam", "comm": ["steam"], "exe_prefix": [], "env_any": ["SteamAppId", "SteamGameId"],
     "launch": ["/usr/games/steam"]},
    {"name": "firefox", "comm": [], "exe_prefix": ["/snap/firefox/", "/usr/lib/firefox/"], "env_any": [],
     "launch": ["/snap/bin/firefox"]},
]


class Paths:
    def __init__(self, etc_dir, state_dir, run_dir):
        self.etc_dir, self.state_dir, self.run_dir = etc_dir, state_dir, run_dir

    @classmethod
    def from_root(cls, root=""):
        root = root.rstrip("/")
        return cls(f"{root}/etc/wenv-vault", f"{root}/var/lib/wenv-vault", f"{root}/run/wenv-vault")

    @classmethod
    def from_env(cls):
        return cls.from_root(os.environ.get("WENV_VAULT_ROOT", ""))

    @property
    def policy_file(self):
        return os.path.join(self.etc_dir, "policy.json")

    @property
    def state_file(self):
        return os.path.join(self.state_dir, "state.json")

    @property
    def bank_dir(self):
        return os.path.join(self.state_dir, "bank")

    @property
    def log_file(self):
        return os.path.join(self.state_dir, "log.jsonl")

    @property
    def socket(self):
        return os.environ.get("WENV_VAULT_SOCKET") or os.path.join(self.run_dir, SOCKET_NAME)

    @property
    def dconf_dir(self):
        return os.path.join(os.path.dirname(self.etc_dir), "dconf")

    @property
    def settings_file(self):
        return os.path.join(self.etc_dir, "settings.json")

    @property
    def uninstall_token(self):
        return os.path.join(self.run_dir, "uninstall-ok")

    @property
    def practice_state(self):
        return os.path.join(self.run_dir, "practice-state.json")


def _text_list(value, field, pattern=None):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_ITEMS:
        raise ValueError(f"{field} must be a list of at most {MAX_ITEMS} strings")
    out = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > MAX_TEXT or "\0" in item:
            raise ValueError(f"{field} has an invalid entry: {item!r}")
        item = item.strip()
        if pattern is not None and not pattern.match(item):
            raise ValueError(f"{field} entry {item!r} is not a plain name")
        if item not in out:
            out.append(item)
    return out


def exe_prefix_problem(prefix):
    """Why `prefix` may not be used as an executable-path rule, or None."""
    if not prefix.startswith("/"):
        return "must be an absolute path"
    bare = prefix.rstrip("/")
    if bare in BROAD_PREFIXES or len(bare) < 8:
        return "too broad: it would match system programs"
    for deny in DENY_EXE_PREFIXES:
        d = deny.rstrip("/")
        if bare == d or d.startswith(bare + "/") or prefix.startswith(deny):
            return f"would match protected path {d}"
    return None


def normalize_app(raw):
    """A clean app rule, or ValueError saying what is wrong."""
    if not isinstance(raw, dict):
        raise ValueError("an app rule must be an object")
    name = raw.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError("name must be lowercase letters, digits, - or _ (1-32 characters)")
    comm = _text_list(raw.get("comm"), "comm")
    exe_prefix = _text_list(raw.get("exe_prefix"), "exe_prefix")
    env_any = _text_list(raw.get("env_any"), "env_any", ENV_RE)
    launch = _text_list(raw.get("launch"), "launch")
    if not (comm or exe_prefix or env_any):
        raise ValueError("an app needs at least one of comm, exe_prefix, env_any")
    for c in comm:
        if len(c) > 15:
            raise ValueError(f"comm {c!r} is longer than the kernel's 15 characters, so it could never match")
        if c in matcher.SAFE_COMMS or c in DENY_COMMS:
            raise ValueError(f"comm {c!r} is a protected system or developer process")
    for p in exe_prefix:
        problem = exe_prefix_problem(p)
        if problem:
            raise ValueError(f"exe_prefix {p!r}: {problem}")
    for e in env_any:
        if e in COMMON_ENV:
            raise ValueError(f"env marker {e!r} is set for almost every process")
    return {"name": name, "comm": comm, "exe_prefix": exe_prefix, "env_any": env_any, "launch": launch}


def default_policy(owner_uid):
    return {"version": VERSION, "owner_uid": owner_uid, "enforce": False,
            "apps": [normalize_app(a) for a in DEFAULT_APPS]}


def validate_policy(raw):
    if not isinstance(raw, dict):
        raise ValueError("policy is not an object")
    if raw.get("version") != VERSION:
        raise ValueError(f"unsupported policy version {raw.get('version')!r}")
    uid = raw.get("owner_uid")
    if not isinstance(uid, int) or isinstance(uid, bool) or uid < 0:
        raise ValueError("owner_uid must be a non-negative integer")
    if not isinstance(raw.get("enforce"), bool):
        raise ValueError("enforce must be true or false")
    apps = raw.get("apps")
    if not isinstance(apps, list):
        raise ValueError("apps must be a list")
    clean, seen = [], set()
    for app in apps:
        a = normalize_app(app)
        if a["name"] in seen:
            raise ValueError(f"duplicate app {a['name']!r}")
        seen.add(a["name"])
        clean.append(a)
    return {"version": VERSION, "owner_uid": uid, "enforce": raw["enforce"], "apps": clean}


def load_policy(path):
    """(policy, None) or (None, reason). The caller fails open on None."""
    try:
        with open(path, encoding="utf-8") as fh:
            return validate_policy(json.load(fh)), None
    except FileNotFoundError:
        return None, f"policy missing: {path}"
    except (OSError, ValueError) as exc:
        return None, f"policy unusable ({path}): {exc}"


def save_policy(path, policy):
    clean = validate_policy(policy)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".policy-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(clean, fh, indent=1, sort_keys=True)
            fh.write("\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

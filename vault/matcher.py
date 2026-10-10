"""Decide which running processes belong to a vault app.

A process belongs to an app if it matches the app's rules directly (command name, executable path prefix, or a
marker environment variable) or descends from one that does. Shells, terminals, the desktop shell and the agents
themselves are never members and stop the descent, so a terminal opened from a vault app is left alone.
"""
import os

SAFE_COMMS = frozenset({
    "bash", "zsh", "fish", "sh", "dash", "tmux", "screen", "ssh", "sudo",
    "gnome-shell", "gnome-terminal-", "gnome-terminal-server", "ptyxis", "kitty", "alacritty", "konsole",
    "claude", "node", "python3", "python", "gjs", "code", "cursor",
    "systemd", "dbus-daemon", "Xwayland", "mutter", "gdm", "gdm-session-wor",
    "aw-tauri", "aw-awatcher", "wenv-vaultd",
})

DEFAULT_APPS = [
    # The Steam client and everything it starts. Proton games carry SteamAppId in their environment, which also
    # catches a game that outlives the client.
    {"name": "steam", "comm": ["steam"], "exe_prefix": [], "env_any": ["SteamAppId", "SteamGameId"]},
    # Firefox is the snap build; every process of it runs from /snap/firefox/.
    {"name": "firefox", "comm": [], "exe_prefix": ["/snap/firefox/", "/usr/lib/firefox/"], "env_any": []},
]


def parse_stat_full(text):
    """(comm, ppid, starttime) from /proc/<pid>/stat. The command name may contain spaces and parentheses."""
    head, _, tail = text.rpartition(")")
    comm = head.partition("(")[2]
    fields = tail.split()                    # fields[0] is the state; starttime is field 22 overall
    start = int(fields[19]) if len(fields) > 19 else 0     # a real stat line always has it
    return comm, int(fields[1]), start


def parse_stat(text):
    """(comm, ppid): the part of parse_stat_full that most callers need."""
    comm, ppid, _ = parse_stat_full(text)
    return comm, ppid


def _read(path):
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except (OSError, ValueError):
        return None


def snapshot(proc="/proc", env_keys=(), cache=None):
    """{pid: {"pid", "ppid", "comm", "exe", "start", "env": [matching marker keys]}} for every readable process.

    `cache` (a dict the caller keeps between calls) remembers each process's environment markers, keyed by
    (pid, start time, exe) so a recycled pid or an exec is read afresh. Entries of dead processes are dropped.
    """
    wanted = {k.encode() for k in env_keys}
    procs, alive = {}, set()
    try:
        entries = os.listdir(proc)
    except OSError:
        return procs
    for entry in entries:
        if not entry.isdigit():
            continue
        base = os.path.join(proc, entry)
        raw = _read(os.path.join(base, "stat"))
        if raw is None:
            continue
        try:
            comm, ppid, start = parse_stat_full(raw.decode("utf-8", "replace"))
        except (ValueError, IndexError):
            continue
        exe = ""
        try:
            exe = os.readlink(os.path.join(base, "exe"))
        except OSError:
            cmdline = _read(os.path.join(base, "cmdline")) or b""
            exe = cmdline.split(b"\0", 1)[0].decode("utf-8", "replace")
        env = []
        if wanted:
            key = (int(entry), start, exe)
            alive.add(key)
            if cache is not None and key in cache:
                env = cache[key]
            else:
                blob = _read(os.path.join(base, "environ"))
                if blob:
                    env = sorted(k.decode() for k in (item.split(b"=", 1)[0] for item in blob.split(b"\0")) if k in wanted)
                if cache is not None:
                    cache[key] = env
        procs[int(entry)] = {"pid": int(entry), "ppid": ppid, "comm": comm, "exe": exe, "start": start, "env": env}
    if cache is not None:
        for key in [k for k in cache if k not in alive]:
            del cache[key]
    return procs


def env_keys_of(apps):
    return sorted({k for app in apps for k in app.get("env_any", [])})


def _direct(proc, app):
    if proc["comm"] in app.get("comm", []):
        return True
    if any(proc["exe"].startswith(p) for p in app.get("exe_prefix", [])):
        return True
    return bool(set(proc["env"]) & set(app.get("env_any", [])))


def classify(procs, apps, safe=SAFE_COMMS, ignore_pids=()):
    """{app name: sorted pids}. Every app name appears, with an empty list when nothing matches."""
    children = {}
    for p in procs.values():
        children.setdefault(p["ppid"], []).append(p["pid"])
    ignored = set(ignore_pids)
    result = {}
    for app in apps:
        members, queue = set(), []
        for p in procs.values():
            if p["pid"] in ignored or p["comm"] in safe:
                continue
            if _direct(p, app):
                members.add(p["pid"])
                queue.append(p["pid"])
        while queue:
            for child in children.get(queue.pop(), []):
                c = procs[child]
                if child in members or child in ignored or c["comm"] in safe:
                    continue
                members.add(child)
                queue.append(child)
        result[app["name"]] = sorted(members)
    return result

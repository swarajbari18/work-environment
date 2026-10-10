"""Rewrite .desktop launchers so the quiz runs *before* the app starts.

An override is written to the user's applications directory under the same file name, which shadows the system
entry. It is a convenience layer only: the daemon stops a locked app however it was started.
"""
import os
import re

SYSTEM_DIRS = ("/usr/share/applications", "/usr/local/share/applications", "/var/lib/snapd/desktop/applications",
               "/var/lib/flatpak/exports/share/applications")
MARKER = "X-WENV-Vault=true"


def user_dir():
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "applications")


def search_dirs():
    override = os.environ.get("WENV_DESKTOP_DIRS")           # colon-separated; for tests and unusual systems
    if override:
        return tuple(p for p in override.split(":") if p)
    return (user_dir(), *SYSTEM_DIRS)


def find_desktop(desktop_id, dirs=None):
    name = desktop_id if desktop_id.endswith(".desktop") else desktop_id + ".desktop"
    for d in dirs or search_dirs():
        path = os.path.join(d, name)
        if os.path.isfile(path):
            return path
    return None


def exec_of(text):
    """The main Exec line of a desktop entry (from [Desktop Entry]), or None."""
    section = None
    for line in text.splitlines():
        m = re.match(r"\[(.+)\]\s*$", line)
        if m:
            section = m.group(1)
        elif section == "Desktop Entry" and line.startswith("Exec="):
            return line[5:].strip()
    return None


def make_override(text, name, launcher):
    """`text` with every Exec= line wrapped as `<launcher> <name> -- <original>`. Already-wrapped lines are kept."""
    out, wrapped = [], 0
    for line in text.splitlines():
        if line.startswith("Exec="):
            original = line[5:].strip()
            if not original.startswith(launcher):
                line = f"Exec={launcher} {name} -- {original}"
                wrapped += 1
        elif line.strip() == "DBusActivatable=true":
            line = "DBusActivatable=false"                 # D-Bus activation would bypass Exec entirely
        out.append(line)
    if wrapped == 0 and not any(l.startswith("Exec=") for l in out):
        raise ValueError("no Exec line to wrap")
    if MARKER not in out:
        for i, line in enumerate(out):
            if line.strip() == "[Desktop Entry]":
                out.insert(i + 1, MARKER)
                break
    return "\n".join(out) + "\n"


ORIG_SUFFIX = ".wenv-orig"       # the user's own entry, kept next to our override
LINK_SUFFIX = ".wenv-link"       # where the entry used to point, when it was a symlink
_WRAPPED_EXEC = re.compile(r"^Exec=\S*wenv-launch\s+\S+\s+--\s+(.*)$")


def strip_override(text):
    """Best-effort inverse of make_override, for a wrapped entry whose saved original is gone."""
    out = []
    for line in text.splitlines():
        if line.strip() == MARKER:
            continue
        m = _WRAPPED_EXEC.match(line)
        out.append(f"Exec={m.group(1)}" if m else line)
    return "\n".join(out) + "\n"


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def install_override(desktop_id, name, launcher, dest_dir=None, source_dirs=None):
    """Write the override and return its path. Raises FileNotFoundError / ValueError.

    The user's own entry is kept next to ours as <file>.wenv-orig and put back by remove_override. If the entry
    is a symlink (Steam makes one into its own installer folder) the link is *replaced* by our file, never written
    through, so the program's own file is not touched; the link is remembered in <file>.wenv-link.
    """
    src = find_desktop(desktop_id, source_dirs)
    if src is None:
        raise FileNotFoundError(f"no desktop entry named {desktop_id}")
    text = _read(src)
    dest_dir = dest_dir or user_dir()
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(src))
    saved, link_note = dest + ORIG_SUFFIX, dest + LINK_SUFFIX

    if os.path.islink(dest):
        target = os.readlink(dest)
        real = os.path.realpath(dest)
        if MARKER in text:
            # An earlier version wrote through the link and wrapped the program's own file. Put it back.
            original = _read(saved) if os.path.exists(saved) else strip_override(text)
            _write(real, original)
            text = original
        elif not os.path.exists(saved):
            _write(saved, text)
        _write(link_note, target)
        os.unlink(dest)                                      # the program's file is no longer ours to touch
        _write(dest, make_override(text, name, launcher))
        return dest

    if os.path.abspath(dest) == os.path.abspath(src):
        if MARKER in text:
            return dest                                      # already ours
        _write(saved, text)                                  # keep the user's own entry to restore later
    _write(dest, make_override(text, name, launcher))
    return dest


def remove_override(desktop_id, dest_dir=None):
    """Undo install_override: put back the symlink or the saved original, or delete our file. Never touches a
    file we did not write. Returns True if something was undone."""
    name = desktop_id if desktop_id.endswith(".desktop") else desktop_id + ".desktop"
    path = os.path.join(dest_dir or user_dir(), name)
    saved, link_note = path + ORIG_SUFFIX, path + LINK_SUFFIX
    try:
        if os.path.islink(path) or MARKER not in _read(path):
            return False
        if os.path.exists(link_note):
            target = _read(link_note).strip()
            os.unlink(path)
            os.symlink(target, path)
            for leftover in (link_note, saved):
                if os.path.exists(leftover):
                    os.unlink(leftover)
        elif os.path.exists(saved):
            os.replace(saved, path)
        else:
            os.unlink(path)
        return True
    except OSError:
        return False

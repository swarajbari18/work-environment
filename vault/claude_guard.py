#!/usr/bin/env python3
"""Claude Code PreToolUse hook: keeps agents away from the vault's guard.

Registered in /etc/claude-code/managed-settings.json (root-owned, outranks user settings). It reads the tool call
as JSON on stdin and exits 2 with a reason on stderr to block it; exit 0 with no output means "no objection".
It is advisory by design: the real wall is root ownership, the dconf locks and the missing docker group. What it
adds is a clear refusal (instead of a confusing permission error) and a catch for commands that spell a guard
path out in any form, which prefix-based deny rules miss.

Known cost: a Bash command that merely *mentions* a guard path is refused too (a heredoc into a README, say).
File tools are checked by their path only, so documentation can be written with Write/Edit.
"""
import json
import os
import re
import sys

GUARD_PATHS = (
    "/etc/wenv-vault", "/opt/wenv-vault", "/var/lib/wenv-vault", "/run/wenv-vault",
    "/etc/dconf", "/etc/claude-code", "/etc/systemd/system/wenv-vaultd",
)
GUARDED_SCHEMAS = (
    "org.gnome.desktop.interface", "org.gnome.desktop.background", "org.gnome.desktop.screensaver",
    "org.gnome.desktop.notifications", "org.gnome.desktop.search-providers", "org.gnome.shell",
)
MUTATING = r"add|remove|enforce|settings\s+(?:init|lock|unlock)|uninstall|unlock|gate"

START = r"(?:^|[;&|(){}`\s]|\$\()"
BASH_RULES = (
    (re.compile(START + r"(sudo|pkexec|doas)\b"), "agents never run sudo; ask the owner"),
    (re.compile(START + r"su(\s+-|\s+\w|\s*$)"), "agents never switch user; ask the owner"),
    (re.compile(r"\bchattr\b"), "file attributes of the guard are the owner's to change"),
    (re.compile(r"\bdconf\s+(write|reset|update|load)\b"), "the settings database is locked by the vault"),
    (re.compile(r"\bgsettings\s+(set|reset|reset-recursively)\s+(" + "|".join(re.escape(s) for s in GUARDED_SCHEMAS) +
                r")\b"), "these settings belong to the environment; they need the 120-question quiz"),
    (re.compile(r"\bsystemctl\b[^\n]*\bwenv-vault"), "the vault services are the owner's to control"),
    (re.compile(r"\b(kill|pkill|killall)\b[^\n]*(vaultd|wenv)"), "the vault daemon is not for agents to stop"),
    (re.compile(r"wenv-vault(?:\.py)?\s+(?:-\S+\s+)*(" + MUTATING + r")\b"),
     "changing the vault is the owner's decision (it costs them a quiz)"),
    (re.compile(r"\bcli\.py\s+(?:-\S+\s+)*(" + MUTATING + r")\b"), "changing the vault is the owner's decision"),
)
PATH_TOKEN = re.compile(r"(?:~|/)[\w@%+=:,.\-/~]*")
FILE_FIELDS = ("file_path", "notebook_path", "path")
FILE_TOOLS = ("Edit", "Write", "NotebookEdit", "MultiEdit")


def _normalised(path):
    return os.path.normpath(os.path.expanduser(path))


def guard_path_in(text):
    """The guard path a piece of text points at (raw or after normalising ../ and //), or None."""
    squeezed = re.sub(r"/{2,}", "/", text)
    for guard in GUARD_PATHS:
        if guard in squeezed:
            return guard
    for token in PATH_TOKEN.findall(squeezed):
        norm = _normalised(token)
        for guard in GUARD_PATHS:
            if norm == guard or norm.startswith(guard + "/"):
                return guard
    return None


def _file_path_guard(path):
    for candidate in (path, os.path.realpath(os.path.expanduser(path))):
        norm = _normalised(candidate)
        for guard in GUARD_PATHS:
            if norm == guard or norm.startswith(guard + "/"):
                return guard
    return None


def check(tool_name, tool_input):
    """None if the call is fine, else a reason string."""
    if not isinstance(tool_input, dict):
        return None
    if tool_name == "Bash":
        command = str(tool_input.get("command", ""))
        guard = guard_path_in(command)
        if guard:
            return (f"this command touches {guard}, which belongs to the vault guard and is root-owned. "
                    "Use `wenv-vault status|apps|log` to look, or ask the owner")
        for pattern, reason in BASH_RULES:
            if pattern.search(command):
                return reason
        return None
    if tool_name in FILE_TOOLS:
        for field in FILE_FIELDS:
            value = tool_input.get(field)
            if isinstance(value, str):
                guard = _file_path_guard(value)
                if guard:
                    return f"{guard} belongs to the vault guard and is root-owned; the owner changes it with sudo"
    return None


def main(stdin=None, stderr=None):
    stdin = stdin or sys.stdin
    stderr = stderr or sys.stderr
    try:
        event = json.load(stdin)
    except ValueError:
        return 0                                   # not our input: no objection
    if not isinstance(event, dict):
        return 0
    try:
        reason = check(event.get("tool_name", ""), event.get("tool_input"))
    except Exception:                              # noqa: BLE001 - a hook bug must never wedge the agent
        return 0
    if reason:
        stderr.write(f"Blocked by the vault: {reason}.\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

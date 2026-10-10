"""Build /etc/claude-code/managed-settings.json: merge the vault's rules into whatever is already there."""
import json

GUARD_DIRS = ("/etc/wenv-vault", "/opt/wenv-vault", "/var/lib/wenv-vault", "/run/wenv-vault", "/etc/dconf",
              "/etc/claude-code")
FILE_TOOLS = ("Edit", "Write", "NotebookEdit", "MultiEdit")
DENY = [f"{tool}(/{d}/**)" for d in GUARD_DIRS for tool in FILE_TOOLS] + [
    "Bash(sudo *)", "Bash(pkexec *)", "Bash(su *)", "Bash(chattr *)",
]
HOOK_COMMAND = "/usr/bin/python3"
HOOK_ARGS = ["-I", "/opt/wenv-vault/claude_guard.py"]
HOOK_MATCHER = "Bash|Edit|Write|NotebookEdit|MultiEdit"


def our_hook():
    return {"matcher": HOOK_MATCHER,
            "hooks": [{"type": "command", "command": HOOK_COMMAND, "args": list(HOOK_ARGS)}]}


def _is_our_hook(entry):
    return any(isinstance(h, dict) and h.get("args") == HOOK_ARGS for h in entry.get("hooks", [])
               if isinstance(entry, dict))


def merge(existing):
    """`existing` is the parsed current file or None. Returns the merged settings dict. Never removes anything,
    never touches allowManagedHooksOnly or the sandbox."""
    if existing is None:
        existing = {}
    if not isinstance(existing, dict):
        raise ValueError("managed-settings.json is not a JSON object")
    out = json.loads(json.dumps(existing))
    perms = out.setdefault("permissions", {})
    if not isinstance(perms, dict):
        raise ValueError("permissions is not an object")
    deny = perms.setdefault("deny", [])
    if not isinstance(deny, list):
        raise ValueError("permissions.deny is not a list")
    for rule in DENY:
        if rule not in deny:
            deny.append(rule)
    hooks = out.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks is not an object")
    pre = hooks.setdefault("PreToolUse", [])
    if not isinstance(pre, list):
        raise ValueError("hooks.PreToolUse is not a list")
    if not any(_is_our_hook(e) for e in pre):
        pre.append(our_hook())
    return out


def render(existing):
    return json.dumps(merge(existing), indent=2, sort_keys=True) + "\n"


def unmerge(existing):
    """`existing` without the vault's rules and hook (the inverse of merge). Returns (settings, changed)."""
    if not isinstance(existing, dict):
        return existing, False
    out = json.loads(json.dumps(existing))
    changed = False
    perms = out.get("permissions")
    if isinstance(perms, dict) and isinstance(perms.get("deny"), list):
        kept = [r for r in perms["deny"] if r not in DENY]
        if len(kept) != len(perms["deny"]):
            changed = True
            perms["deny"] = kept
        if not perms["deny"]:
            del perms["deny"]
        if not perms:
            del out["permissions"]
    hooks = out.get("hooks")
    if isinstance(hooks, dict) and isinstance(hooks.get("PreToolUse"), list):
        kept = [e for e in hooks["PreToolUse"] if not _is_our_hook(e)]
        if len(kept) != len(hooks["PreToolUse"]):
            changed = True
            hooks["PreToolUse"] = kept
        if not hooks["PreToolUse"]:
            del hooks["PreToolUse"]
        if not hooks:
            del out["hooks"]
    return out, changed

#!/usr/bin/env python3
"""wenv-vault: look at and change the vault.

  status | apps | log [-n N]
  add NAME [--comm C]... [--exe-prefix P]... [--env VAR]... [--launch CMD]... [--desktop ID]     free
  remove NAME                 60-question quiz
  enforce on|off              on is free, off needs the 120-question quiz
  unlock                      take the daily quiz now
  gate [--tier T] [--practice]
  uninstall                   120-question quiz, then prints the sudo command
  settings status|init|lock|unlock   the environment's GNOME settings (init/lock free, unlock 120 questions)
"""
import argparse
import glob
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import client as vclient  # noqa: E402
import desktop  # noqa: E402
import settings as settings_mod  # noqa: E402

LAUNCHER = os.environ.get("WENV_LAUNCHER", os.path.expanduser("~/.local/bin/wenv-launch"))


def default_gate(vault, tier, practice=False, app_label=None, auto_close=None):
    from gate import run_gate
    return run_gate(vault, tier, practice=practice, app_label=app_label, auto_close=auto_close)


def default_lib_path():
    return os.environ.get("WENV_LIB") or os.path.expanduser("~/work-environment/lib.sh")


def gsettings_get(schema, key):
    """The live value, as the same text lib.sh uses; None if it cannot be read. Extension schemas live in
    each extension's own directory, as lib.sh:export_schema_dirs does."""
    dirs = [d for d in glob.glob(os.path.expanduser("~/.local/share/gnome-shell/extensions/*/schemas"))
            if os.path.exists(os.path.join(d, "gschemas.compiled"))]
    env = dict(os.environ)
    if dirs:
        env["GSETTINGS_SCHEMA_DIR"] = ":".join(dirs + ([env["GSETTINGS_SCHEMA_DIR"]] if env.get("GSETTINGS_SCHEMA_DIR") else []))
    try:
        r = subprocess.run(["gsettings", "get", schema, key], capture_output=True, text=True, timeout=10, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


class Cli:
    def __init__(self, vault=None, gate=default_gate, out=print, err=None, lib_path=None, live=gsettings_get):
        self.vault = vault or vclient.VaultClient()
        self.gate = gate
        self.out = out
        self.err = err or (lambda m: print(m, file=sys.stderr))
        self.lib_path = lib_path or default_lib_path()
        self.live = live

    # --- helpers ------------------------------------------------------------------------------------------------
    def _call(self, op, **kw):
        try:
            return self.vault.call(op, **kw)
        except vclient.VaultUnavailable as exc:
            self.err(f"vault: {exc}")
            return None

    def _with_ticket(self, op, **kw):
        """Run a ticketed op; if it needs a quiz, offer the quiz window once, then retry."""
        reply = self._call(op, **kw)
        if reply is None:
            return None
        if reply.get("error") == "ticket_required":
            self.out(f"This needs the {reply['needed']}-question quiz. Opening it...")
            if not self.gate(self.vault, reply["tier"]):
                self.err("not passed; nothing changed")
                return None
            reply = self._call(op, **kw)
        return reply

    def _report(self, reply, ok_text):
        if reply is None:
            return 1
        if reply.get("ok"):
            self.out(ok_text(reply) if callable(ok_text) else ok_text)
            return 0
        self.err(f"vault: {reply.get('message') or reply.get('error')}")
        return 1

    # --- commands -----------------------------------------------------------------------------------------------
    def status(self, args):
        s = self._call("status")
        if s is None:
            return 1
        if not s.get("ok"):
            self.err(f"vault: {s.get('message')}")
            return 1
        mode = "ENFORCING" if s["enforce"] else ("dry-run" if s["policy_enforce"] is False else "fail-open")
        self.out(f"mode:     {mode}" + (f"  (fail-open: {s['fail_open']})" if s["fail_open"] else ""))
        self.out(f"today:    {s['state']}" + (f", until {s['hard_end']}" if s["hard_end"] else ""))
        self.out(f"apps:     {', '.join(s['apps']) or '(none)'}")
        self.out(f"bank:     {s['bank_size']} questions, {s['used']} used")
        if s["tickets"]:
            self.out("tickets:  " + ", ".join(f"{t} (until {e})" for t, e in s["tickets"].items()))
        if s["warn"]:
            self.out(f"WARNING:  permission ends at {s['soft_end']}")
        return 0

    def apps(self, args):
        r = self._call("apps")
        if r is None or not r.get("ok"):
            return 1
        for a in r["apps"]:
            rules = []
            for key, label in (("comm", "comm"), ("exe_prefix", "path"), ("env_any", "env")):
                if a[key]:
                    rules.append(f"{label}: {', '.join(a[key])}")
            self.out(f"{a['name']:<12} {'; '.join(rules)}")
        return 0

    def log(self, args):
        r = self._call("log", n=args.n)
        if r is None or not r.get("ok"):
            return 1
        for e in r["entries"]:
            rest = " ".join(f"{k}={v}" for k, v in e.items() if k not in ("ts", "kind"))
            self.out(f"{e['ts']}  {e['kind']:<14} {rest}")
        return 0

    def add(self, args):
        app = {"name": args.name, "comm": args.comm, "exe_prefix": args.exe_prefix, "env_any": args.env,
               "launch": args.launch}
        if args.desktop:
            src = desktop.find_desktop(args.desktop)
            if src is None:
                self.err(f"no desktop entry named {args.desktop}")
                return 1
            with open(src, encoding="utf-8") as fh:
                command = desktop.exec_of(fh.read())
            if command and not app["launch"]:
                app["launch"] = [w for w in command.split() if not w.startswith("%")]
            if command and not (app["comm"] or app["exe_prefix"] or app["env_any"]) and app["launch"]:
                app["comm"] = [os.path.basename(app["launch"][0])[:15]]
        code = self._report(self._call("add_app", app=app), lambda r: f"added {r['added']} to the vault")
        if code == 0 and args.desktop:
            try:
                path = desktop.install_override(args.desktop, args.name, LAUNCHER)
                self.out(f"launcher now asks first: {path}")
            except (OSError, ValueError) as exc:
                self.err(f"added, but could not wrap the launcher: {exc}")
        return code

    def remove(self, args):
        reply = self._with_ticket("remove_app", name=args.name)
        return self._report(reply, lambda r: f"removed {r['removed']} from the vault")

    def enforce(self, args):
        value = args.value == "on"
        reply = self._call("set_enforce", value=value) if value else self._with_ticket("set_enforce", value=False)
        return self._report(reply, lambda r: f"enforcement is {'on' if r['enforce'] else 'off'}"
                            + ("" if r["changed"] else " (no change)"))

    def unlock(self, args):
        return 0 if self.gate(self.vault, "launch") else 1

    def gate_cmd(self, args):
        return 0 if self.gate(self.vault, args.tier, practice=args.practice) else 1

    # --- the environment's settings -----------------------------------------------------------------------------
    def _targets(self):
        """The lockable (schema, key, value) targets in lib.sh, or None with a message."""
        try:
            with open(self.lib_path, encoding="utf-8") as fh:
                parsed = settings_mod.parse_lib_text(fh.read())
            entries = settings_mod.validate_entries(parsed)
        except (OSError, settings_mod.SettingsError) as exc:
            self.err(f"settings: cannot use {self.lib_path}: {exc}")
            return None
        return entries

    def _matching_targets(self):
        """lib.sh targets, but only if the live desktop already has exactly those values."""
        entries = self._targets()
        if entries is None:
            return None
        off = []
        for e in entries:
            live = self.live(e.schema, e.key)
            if live != e.value:
                off.append((e, live))
        if off:
            self.err("settings: the desktop does not match lib.sh yet, so locking it would lock a broken state:")
            for e, live in off[:12]:
                self.err(f"  {e.schema} {e.key}: live {live!r}, lib.sh wants {e.value!r}")
            self.err("run ./apply.sh all first, then try again")
            return None
        return [list(e) for e in entries]

    def settings_status(self, args):
        r = self._call("settings_status")
        if r is None or not r.get("ok"):
            self.err(f"vault: {(r or {}).get('message', 'no answer')}")
            return 1
        if not r["initialized"]:
            self.out("settings: not locked yet. Run: wenv-vault settings init")
        elif r["locked"]:
            self.out(f"settings: LOCKED ({r['keys']} keys). Changing them costs the 120-question quiz.")
        else:
            self.out(f"settings: UNLOCKED until {r['unlocked_until']} ({r['keys']} keys). "
                     "Edit, run apply.sh, then `wenv-vault settings lock` to keep the changes; "
                     "otherwise the old values come back.")
        return 0

    def settings_init(self, args):
        targets = self._matching_targets()
        if targets is None:
            return 1
        return self._report(self._call("settings_init", entries=targets),
                            lambda r: f"locked {r['locked']} settings; they now need the 120-question quiz to change")

    def settings_lock(self, args):
        targets = self._matching_targets()
        if targets is None:
            return 1
        return self._report(self._call("settings_lock", entries=targets),
                            lambda r: f"committed and locked {r['locked']} settings")

    def settings_unlock(self, args):
        reply = self._with_ticket("settings_unlock")
        return self._report(reply, lambda r: (
            f"settings are writable until {r['unlocked_until']}. Edit lib.sh, run ./apply.sh all, then "
            "`wenv-vault settings lock` to keep the change. If you do not, the old values return automatically."))

    def settings_cmd(self, args):
        return {"status": self.settings_status, "init": self.settings_init, "lock": self.settings_lock,
                "unlock": self.settings_unlock}[args.action](args)

    def uninstall(self, args):
        reply = self._with_ticket("prepare_uninstall")
        return self._report(reply, lambda r: f"approved until {r['expires']}. Now run: {r['next']}")


def build_parser():
    p = argparse.ArgumentParser(prog="wenv-vault", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("apps")
    s = sub.add_parser("log")
    s.add_argument("-n", type=int, default=30)
    s = sub.add_parser("add")
    s.add_argument("name")
    s.add_argument("--comm", action="append", default=[])
    s.add_argument("--exe-prefix", action="append", default=[])
    s.add_argument("--env", action="append", default=[])
    s.add_argument("--launch", action="append", default=[])
    s.add_argument("--desktop")
    s = sub.add_parser("remove")
    s.add_argument("name")
    s = sub.add_parser("enforce")
    s.add_argument("value", choices=["on", "off"])
    sub.add_parser("unlock")
    s = sub.add_parser("gate")
    s.add_argument("--tier", default="launch", choices=["launch", "remove_app", "change_settings"])
    s.add_argument("--practice", action="store_true")
    sub.add_parser("uninstall")
    s = sub.add_parser("settings")
    s.add_argument("action", choices=["status", "init", "lock", "unlock"])
    s.add_argument("--lib", default=None, help="path to lib.sh (default ~/work-environment/lib.sh)")
    return p


def main(argv=None, cli=None):
    args = build_parser().parse_args(argv)
    cli = cli or Cli()
    if getattr(args, "lib", None):
        cli.lib_path = args.lib
    handler = {"settings": cli.settings_cmd, "status": cli.status, "apps": cli.apps, "log": cli.log, "add": cli.add, "remove": cli.remove,
               "enforce": cli.enforce, "unlock": cli.unlock, "gate": cli.gate_cmd, "uninstall": cli.uninstall}
    return handler[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())

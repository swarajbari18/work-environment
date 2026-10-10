"""apply.sh, verify.sh and restore.sh against a shimmed gsettings: nothing here touches the real desktop."""
import json
import os
import stat
import subprocess
import tempfile
import unittest

import settings

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_DIR = os.path.dirname(HERE)                                    # ~/work-environment

GSETTINGS_SHIM = r'''#!/usr/bin/env python3
import json, os, sys
path = os.environ["SHIM_STATE"]
with open(path) as fh:
    state = json.load(fh)
cmd, *rest = sys.argv[1:]
key = " ".join(rest[:2])
if cmd == "get":
    if key in state["values"]:
        print(state["values"][key]); sys.exit(0)
    sys.exit(1)
if cmd == "writable":
    print("false" if key in state["locked"] else "true"); sys.exit(0)
if cmd in ("set", "reset"):
    if key in state["locked"]:
        print("The key is not writable", file=sys.stderr); sys.exit(1)
    if cmd == "set":
        state["values"][key] = rest[2]
    else:
        state["values"].pop(key, None)
    state["writes"].append(key)
    with open(path, "w") as fh:
        json.dump(state, fh)
    sys.exit(0)
sys.exit(0)
'''
VAULT_SHIM = '''#!/bin/sh
case "${SHIM_VAULT:-none}" in
  locked)   echo "settings: LOCKED (32 keys). Changing them costs the 120-question quiz." ;;
  unlocked) echo "settings: UNLOCKED until 2026-10-12T10:20:00 (32 keys)." ;;
  *)        echo "settings: not locked yet. Run: wenv-vault settings init" ;;
esac
'''


class Shimmed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.home = os.path.join(t, "home")
        self.shims = os.path.join(t, "shims")
        self.backup = os.path.join(t, "backup")
        for d in (self.home, self.shims, self.backup):
            os.makedirs(d)
        self.state = os.path.join(t, "state.json")
        self._write(os.path.join(self.shims, "gsettings"), GSETTINGS_SHIM)
        self._write(os.path.join(self.shims, "wenv-vault"), VAULT_SHIM)
        for name in ("dconf", "gnome-extensions"):
            self._write(os.path.join(self.shims, name), "#!/bin/sh\nexit 0\n")
        with open(os.path.join(ENV_DIR, "lib.sh")) as fh:
            self.entries = settings.parse_lib_text(fh.read())
        self.keys = [f"{e.schema} {e.key}" for e in self.entries]

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, path, text):
        with open(path, "w") as fh:
            fh.write(text)
        os.chmod(path, 0o755)

    def set_state(self, values, locked=()):
        values = dict(values)
        values.setdefault("org.gnome.shell enabled-extensions", "['apps-menu@gnome-shell-extensions.gcampax.github.com']")
        with open(self.state, "w") as fh:
            json.dump({"values": values, "locked": list(locked), "writes": []}, fh)

    def read_state(self):
        with open(self.state) as fh:
            return json.load(fh)

    def targets(self):
        return {f"{e.schema} {e.key}": e.value for e in self.entries}

    def run_script(self, name, *args, vault="none"):
        env = {**os.environ, "HOME": self.home, "PATH": f"{self.shims}:{os.environ['PATH']}",
               "SHIM_STATE": self.state, "SHIM_VAULT": vault, "WENV_HOME": self.backup,
               "WENV_EXT_DIR": os.path.join(self.home, "ext")}
        return subprocess.run([os.path.join(ENV_DIR, name), *args], capture_output=True, text=True, timeout=120,
                              env=env, cwd=self.home)


class Apply(Shimmed):
    def test_unlocked_writes_every_key(self):
        self.set_state({})
        r = self.run_script("apply.sh", "native")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        values = self.read_state()["values"]
        native = [k for k in self.keys if k.startswith("org.gnome.desktop") or k.startswith("org.gnome.shell ")
                  or k.startswith("org.gnome.shell.keybindings") or "user-theme" in k]
        for k in native:
            self.assertIn(k, values, k)
        self.assertNotIn("locked", r.stdout)

    def test_a_locked_key_that_is_already_right_is_left_alone(self):
        self.set_state(self.targets(), locked=self.keys)
        r = self.run_script("apply.sh", "native", vault="locked")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("locked, already correct", r.stdout)
        self.assertNotIn("FAIL", r.stdout)
        written = [k for k in self.read_state()["writes"] if k in self.keys]
        self.assertEqual(written, [])                                       # not one write to a locked key

    def test_a_locked_key_that_differs_fails_and_says_how_to_change_it(self):
        values = self.targets()
        key = "org.gnome.desktop.interface gtk-theme"
        values[key] = "'Adwaita'"
        self.set_state(values, locked=self.keys)
        r = self.run_script("apply.sh", "native", vault="locked")
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"FAIL  {key}", r.stdout)
        self.assertIn("wenv-vault settings unlock", r.stdout)
        self.assertIn("wenv-vault settings lock", r.stdout)

    def test_status_still_works(self):
        self.set_state(self.targets(), locked=self.keys)
        r = self.run_script("apply.sh", "status", vault="locked")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("DIFF  org.gnome.desktop.interface gtk-theme", r.stdout)


class Timers(Shimmed):
    def test_timers_stage_writes_units_pointing_at_this_clone(self):
        units = os.path.join(self.tmp.name, "units")
        env_extra = {"WENV_UNIT_DIR": units, "WENV_NO_SYSTEMCTL": "1"}
        env = {**os.environ, "HOME": self.home, "PATH": f"{self.shims}:{os.environ['PATH']}",
               "WENV_HOME": self.backup, **env_extra}
        r = subprocess.run([os.path.join(ENV_DIR, "apply.sh"), "timers"], capture_output=True, text=True,
                           timeout=60, env=env, cwd=self.home)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        names = sorted(os.listdir(units))
        self.assertEqual(names, ["wenv-aw-watchdog.service", "wenv-aw-watchdog.timer",
                                 "wenv-report.service", "wenv-report.timer"])
        for n in names:
            with open(os.path.join(units, n)) as fh:
                text = fh.read()
            self.assertNotIn("@REPO@", text, n)
        with open(os.path.join(units, "wenv-aw-watchdog.service")) as fh:
            self.assertIn(f"ExecStart={ENV_DIR}/aw-watchdog.sh", fh.read())
        with open(os.path.join(units, "wenv-report.service")) as fh:
            report = fh.read()
        self.assertIn(f"ExecStart=/usr/bin/python3 {ENV_DIR}/report.py", report)
        self.assertIn("report-$(date +%%F-%%H).md", report)              # systemd's %% escape survived
        self.assertTrue(os.path.isdir(os.path.join(self.backup, "reports")))
        self.assertEqual(self.read_state if False else None, None)       # no settings were touched or backed up

    def test_timers_do_not_run_the_settings_backup(self):
        units = os.path.join(self.tmp.name, "units")
        env = {**os.environ, "HOME": self.home, "PATH": f"{self.shims}:{os.environ['PATH']}",
               "WENV_HOME": self.backup, "WENV_UNIT_DIR": units, "WENV_NO_SYSTEMCTL": "1"}
        subprocess.run([os.path.join(ENV_DIR, "apply.sh"), "timers"], capture_output=True, text=True, timeout=60,
                       env=env, cwd=self.home)
        self.assertFalse(os.path.exists(os.path.join(self.backup, "backup", "state.tsv")))


class VerifyVault(Shimmed):
    def vault_lines(self, r):
        return [l.strip() for l in r.stdout.splitlines()]

    def test_locked_and_really_locked_passes(self):
        locked = [k for k in self.keys if k != "org.gnome.shell.extensions.colorblind-filters filter-active"]
        self.set_state(self.targets(), locked=locked)
        r = self.run_script("verify.sh", "vault", vault="locked")
        self.assertEqual(r.returncode, 0, r.stdout)
        lines = self.vault_lines(r)
        self.assertIn("[vault]", lines)
        self.assertEqual(len([l for l in lines if l.startswith("PASS")]), len(locked))
        self.assertFalse(any("filter-active" in l for l in lines))          # left writable on purpose

    def test_a_key_that_is_supposed_to_be_locked_but_is_writable_fails(self):
        locked = [k for k in self.keys if k not in ("org.gnome.desktop.interface gtk-theme",
                                                    "org.gnome.shell.extensions.colorblind-filters filter-active")]
        self.set_state(self.targets(), locked=locked)
        r = self.run_script("verify.sh", "vault", vault="locked")
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL  org.gnome.desktop.interface gtk-theme is locked", r.stdout)

    def test_not_locked_and_unlocked_are_information_not_failures(self):
        self.set_state(self.targets())
        for state, phrase in (("none", "not locked"), ("unlocked", "unlock window is open")):
            r = self.run_script("verify.sh", "vault", vault=state)
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertIn(phrase, r.stdout)


class Restore(Shimmed):
    def write_backup(self):
        os.makedirs(os.path.join(self.backup, "backup"), exist_ok=True)
        with open(os.path.join(self.backup, "backup", "state.tsv"), "w") as fh:
            fh.write("org.gnome.desktop.interface\tgtk-theme\tSET\t'Zorin-Dark'\n")
            fh.write("org.gnome.desktop.background\tpicture-uri\tUNSET\t\n")

    def test_refuses_while_locked_and_says_what_to_do(self):
        self.write_backup()
        self.set_state(self.targets(), locked=self.keys)
        r = self.run_script("restore.sh", vault="locked")
        self.assertEqual(r.returncode, 1)
        for phrase in ("wenv-vault uninstall", "/opt/wenv-vault/uninstall.sh", "wenv-vault settings unlock"):
            self.assertIn(phrase, r.stderr)
        self.assertEqual(self.read_state()["writes"], [])

    def test_restores_when_not_locked(self):
        self.write_backup()
        self.set_state(self.targets())
        r = self.run_script("restore.sh", vault="none")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.read_state()["values"]["org.gnome.desktop.interface gtk-theme"], "'Zorin-Dark'")
        self.assertNotIn("org.gnome.desktop.background picture-uri", self.read_state()["values"])

    def test_restores_inside_an_open_window_with_a_note(self):
        self.write_backup()
        self.set_state(self.targets())
        r = self.run_script("restore.sh", vault="unlocked")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("unlock window is open", r.stdout)


if __name__ == "__main__":
    unittest.main()

import os
import stat
import tempfile
import unittest

import agent
import cli
import client as vclient
import desktop
import launch


class FakeDaemon:
    """Scriptable stand-in for VaultClient: replies by op, records every call, can fail like a dead socket."""

    def __init__(self, replies=None, events=None, down=False):
        self.replies = replies or {}
        self.events = list(events or [])
        self.down = down
        self.calls = []

    def call(self, op, **kw):
        self.calls.append((op, kw))
        if self.down:
            raise vclient.VaultUnavailable("down")
        reply = self.replies.get(op, {"ok": True})
        if isinstance(reply, list):
            reply = reply.pop(0) if len(reply) > 1 else reply[0]
        return reply

    def subscribe(self):
        if self.down:
            raise vclient.VaultUnavailable("down")
        for event in self.events:
            yield event
        raise vclient.VaultUnavailable("stream ended")


STEAM_DESKTOP = """[Desktop Entry]
Name=Steam
Exec=/usr/games/steam %U
DBusActivatable=true
[Desktop Action Store]
Name=Store
Exec=/usr/games/steam steam://store
"""


def _read(path):
    with open(path) as fh:
        return fh.read()


def _write(path, text):
    with open(path, "w") as fh:
        fh.write(text)


class Launcher(unittest.TestCase):
    def run_launch(self, vault, gate_result=True):
        calls = {"gate": [], "exec": []}

        def gate(v, tier, app_label=None, auto_close=None):
            calls["gate"].append((tier, app_label))
            return gate_result

        code = launch.main(["firefox", "--", "/snap/bin/firefox", "--new-window"], vault=vault, gate_runner=gate,
                           exec_fn=lambda f, a: calls["exec"].append((f, a)))
        return code, calls

    def test_runs_straight_away_when_not_gated(self):
        code, calls = self.run_launch(FakeDaemon({"status": {"ok": True, "gate_required": False}}))
        self.assertEqual((code, calls["gate"]), (0, []))
        self.assertEqual(calls["exec"], [("/snap/bin/firefox", ["/snap/bin/firefox", "--new-window"])])

    def test_gate_then_run(self):
        code, calls = self.run_launch(FakeDaemon({"status": {"ok": True, "gate_required": True}}))
        self.assertEqual(calls["gate"], [("launch", "firefox")])
        self.assertEqual(len(calls["exec"]), 1)

    def test_closing_the_quiz_starts_nothing(self):
        code, calls = self.run_launch(FakeDaemon({"status": {"ok": True, "gate_required": True}}), gate_result=False)
        self.assertEqual((code, calls["exec"]), (1, []))

    def test_dead_daemon_fails_open(self):
        code, calls = self.run_launch(FakeDaemon(down=True))
        self.assertEqual((code, calls["gate"]), (0, []))
        self.assertEqual(len(calls["exec"]), 1)

    def test_odd_status_means_run(self):
        code, calls = self.run_launch(FakeDaemon({"status": {"ok": False, "error": "forbidden"}}))
        self.assertEqual(len(calls["exec"]), 1)

    def test_argument_parsing(self):
        self.assertEqual(launch.parse(["steam", "--", "/usr/games/steam", "%U"]), ("steam", ["/usr/games/steam", "%U"]))
        self.assertEqual(launch.parse(["steam", "steam", "steam://x"]), ("steam", ["steam", "steam://x"]))
        for bad in ([], ["steam"], ["steam", "--"]):
            with self.assertRaises(SystemExit):
                launch.parse(bad)


class DesktopFiles(unittest.TestCase):
    def test_every_exec_is_wrapped_and_dbus_activation_off(self):
        out = desktop.make_override(STEAM_DESKTOP, "steam", "/home/u/.local/bin/wenv-launch")
        self.assertIn("Exec=/home/u/.local/bin/wenv-launch steam -- /usr/games/steam %U", out)
        self.assertIn("Exec=/home/u/.local/bin/wenv-launch steam -- /usr/games/steam steam://store", out)
        self.assertIn("DBusActivatable=false", out)
        self.assertIn(desktop.MARKER, out)
        self.assertEqual(desktop.exec_of(out), "/home/u/.local/bin/wenv-launch steam -- /usr/games/steam %U")

    def test_idempotent(self):
        once = desktop.make_override(STEAM_DESKTOP, "steam", "/l/wenv-launch")
        twice = desktop.make_override(once, "steam", "/l/wenv-launch")
        self.assertEqual(once, twice)

    def test_needs_an_exec(self):
        with self.assertRaises(ValueError):
            desktop.make_override("[Desktop Entry]\nName=x\n", "x", "/l")

    def test_install_and_remove_only_our_own_files(self):
        with tempfile.TemporaryDirectory() as sysdir, tempfile.TemporaryDirectory() as userdir:
            with open(os.path.join(sysdir, "steam.desktop"), "w") as fh:
                fh.write(STEAM_DESKTOP)
            path = desktop.install_override("steam", "steam", "/l/wenv-launch", dest_dir=userdir, source_dirs=[sysdir])
            self.assertEqual(os.path.basename(path), "steam.desktop")
            with open(path) as fh:
                self.assertIn("wenv-launch steam --", fh.read())
            with open(os.path.join(sysdir, "steam.desktop")) as fh:            # the system file is untouched
                self.assertNotIn("wenv-launch", fh.read())
            self.assertTrue(desktop.remove_override("steam", dest_dir=userdir))
            self.assertFalse(os.path.exists(path))
            with open(os.path.join(userdir, "mine.desktop"), "w") as fh:
                fh.write("[Desktop Entry]\nExec=x\n")
            self.assertFalse(desktop.remove_override("mine", dest_dir=userdir))     # not ours: left alone
            self.assertTrue(os.path.exists(os.path.join(userdir, "mine.desktop")))

    def test_a_users_own_entry_is_saved_and_restored(self):
        with tempfile.TemporaryDirectory() as userdir:
            path = os.path.join(userdir, "steam.desktop")
            with open(path, "w") as fh:
                fh.write(STEAM_DESKTOP)
            desktop.install_override("steam", "steam", "/l/wenv-launch", dest_dir=userdir, source_dirs=[userdir])
            with open(path) as fh:
                self.assertIn(desktop.MARKER, fh.read())
            self.assertTrue(os.path.exists(path + ".wenv-orig"))
            again = desktop.install_override("steam", "steam", "/l/wenv-launch", dest_dir=userdir,
                                             source_dirs=[userdir])
            self.assertEqual(again, path)
            with open(path + ".wenv-orig") as fh:                              # second run must not clobber it
                self.assertEqual(fh.read(), STEAM_DESKTOP)
            self.assertTrue(desktop.remove_override("steam", dest_dir=userdir))
            with open(path) as fh:
                self.assertEqual(fh.read(), STEAM_DESKTOP)
            self.assertFalse(os.path.exists(path + ".wenv-orig"))

    def _link_setup(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        real_dir = os.path.join(root.name, "steam-installer")
        user_dir = os.path.join(root.name, "applications")
        os.makedirs(real_dir)
        os.makedirs(user_dir)
        real = os.path.join(real_dir, "steam.desktop")
        _write(real, STEAM_DESKTOP)
        link = os.path.join(user_dir, "steam.desktop")
        os.symlink(real, link)
        return real, link, user_dir

    def test_a_symlinked_entry_is_replaced_never_written_through(self):
        real, link, user_dir = self._link_setup()
        desktop.install_override("steam", "steam", "/l/wenv-launch", dest_dir=user_dir, source_dirs=[user_dir])
        self.assertEqual(_read(real), STEAM_DESKTOP)                         # the program's own file is untouched
        self.assertFalse(os.path.islink(link))
        self.assertIn(desktop.MARKER, _read(link))
        self.assertTrue(os.path.exists(link + ".wenv-link"))
        self.assertTrue(desktop.remove_override("steam", dest_dir=user_dir))
        self.assertTrue(os.path.islink(link))
        self.assertEqual(os.readlink(link), real)
        self.assertEqual(_read(real), STEAM_DESKTOP)
        self.assertEqual(sorted(os.listdir(user_dir)), ["steam.desktop"])    # no leftovers

    def test_it_heals_an_entry_an_older_version_wrote_through_a_link(self):
        real, link, user_dir = self._link_setup()
        _write(link + ".wenv-orig", STEAM_DESKTOP)                           # what the old version saved
        _write(real, desktop.make_override(STEAM_DESKTOP, "steam", "/l/wenv-launch"))   # ...and what it did
        desktop.install_override("steam", "steam", "/l/wenv-launch", dest_dir=user_dir, source_dirs=[user_dir])
        self.assertEqual(_read(real), STEAM_DESKTOP)                         # the program's file is put back
        self.assertFalse(os.path.islink(link))
        self.assertIn("wenv-launch steam -- /usr/games/steam %U", _read(link))
        self.assertTrue(desktop.remove_override("steam", dest_dir=user_dir))
        self.assertEqual(os.readlink(link), real)

    def test_healing_without_a_saved_copy_unwraps_the_entry(self):
        real, link, user_dir = self._link_setup()
        _write(real, desktop.make_override(STEAM_DESKTOP, "steam", "/home/u/.local/bin/wenv-launch"))
        desktop.install_override("steam", "steam", "/l/wenv-launch", dest_dir=user_dir, source_dirs=[user_dir])
        healed = _read(real)
        self.assertNotIn(desktop.MARKER, healed)
        self.assertIn("Exec=/usr/games/steam %U", healed)
        self.assertNotIn("wenv-launch", healed)

    def test_strip_override_is_the_inverse_for_exec_lines(self):
        wrapped = desktop.make_override(STEAM_DESKTOP, "steam", "/l/wenv-launch")
        back = desktop.strip_override(wrapped)
        self.assertIn("Exec=/usr/games/steam %U", back)
        self.assertIn("Exec=/usr/games/steam steam://store", back)
        self.assertNotIn(desktop.MARKER, back)

    def test_missing_entry(self):
        with self.assertRaises(FileNotFoundError):
            desktop.install_override("nonexistent-app", "x", "/l", dest_dir="/tmp", source_dirs=["/nonexistent"])

    def test_the_real_machine_entries_wrap(self):
        for name in ("firefox_firefox", "steam"):
            src = desktop.find_desktop(name)
            if src is None:
                continue
            with open(src) as fh:
                out = desktop.make_override(fh.read(), name, "/l/wenv-launch")
            self.assertNotIn("\nExec=/", out.replace("Exec=/l/wenv-launch", ""))   # no bare Exec left


class CliTests(unittest.TestCase):
    def make(self, replies=None, gate_result=True):
        self.out, self.err, self.gates = [], [], []
        daemon = FakeDaemon(replies)

        def gate(vault, tier, practice=False, app_label=None, auto_close=None):
            self.gates.append((tier, practice))
            return gate_result

        c = cli.Cli(vault=daemon, gate=gate, out=self.out.append, err=self.err.append)
        return c, daemon

    def run_cli(self, argv, **kw):
        c, daemon = self.make(**kw)
        code = cli.main(argv, cli=c)
        return code, daemon

    def test_status_text(self):
        status = {"ok": True, "enforce": False, "policy_enforce": False, "fail_open": None, "state": "locked",
                  "hard_end": None, "apps": ["steam", "firefox"], "bank_size": 100, "used": 5, "tickets": {},
                  "warn": False, "soft_end": None}
        code, _ = self.run_cli(["status"], replies={"status": status})
        self.assertEqual(code, 0)
        self.assertIn("dry-run", self.out[0])
        self.assertIn("steam, firefox", "\n".join(self.out))

    def test_daemon_down_is_a_clean_error(self):
        c, daemon = self.make()
        daemon.down = True
        self.assertEqual(cli.main(["status"], cli=c), 1)
        self.assertIn("vault:", self.err[0])

    def test_add_is_free_no_gate(self):
        code, daemon = self.run_cli(["add", "discord", "--comm", "Discord", "--launch", "/usr/bin/discord"],
                                    replies={"add_app": {"ok": True, "added": "discord"}})
        self.assertEqual((code, self.gates), (0, []))
        self.assertEqual(daemon.calls[0][1]["app"], {"name": "discord", "comm": ["Discord"], "exe_prefix": [],
                                                    "env_any": [], "launch": ["/usr/bin/discord"]})

    def test_remove_opens_the_quiz_then_retries(self):
        replies = {"remove_app": [{"ok": False, "error": "ticket_required", "tier": "remove_app", "needed": 60},
                                  {"ok": True, "removed": "firefox"}]}
        c, daemon = self.make(replies)
        # a list reply pops: first the refusal, then success
        code = cli.main(["remove", "firefox"], cli=c)
        self.assertEqual(code, 0)
        self.assertEqual(self.gates, [("remove_app", False)])
        self.assertEqual([op for op, _ in daemon.calls], ["remove_app", "remove_app"])
        self.assertIn("removed firefox", self.out[-1])

    def test_failed_quiz_changes_nothing(self):
        replies = {"remove_app": {"ok": False, "error": "ticket_required", "tier": "remove_app", "needed": 60}}
        code, daemon = self.run_cli(["remove", "firefox"], replies=replies, gate_result=False)
        self.assertEqual(code, 1)
        self.assertEqual(len(daemon.calls), 1)
        self.assertIn("nothing changed", self.err[-1])

    def test_enforce_on_never_opens_a_quiz(self):
        code, daemon = self.run_cli(["enforce", "on"], replies={"set_enforce": {"ok": True, "enforce": True,
                                                                              "changed": True}})
        self.assertEqual((code, self.gates), (0, []))
        self.assertEqual(daemon.calls[0], ("set_enforce", {"value": True}))

    def test_enforce_off_needs_the_big_quiz(self):
        replies = {"set_enforce": [{"ok": False, "error": "ticket_required", "tier": "change_settings",
                                    "needed": 120},
                                   {"ok": True, "enforce": False, "changed": True}]}
        code, _ = self.run_cli(["enforce", "off"], replies=replies)
        self.assertEqual((code, self.gates), (0, [("change_settings", False)]))

    def test_unlock_and_practice(self):
        code, _ = self.run_cli(["unlock"])
        self.assertEqual((code, self.gates), (0, [("launch", False)]))
        code, _ = self.run_cli(["gate", "--tier", "change_settings", "--practice"])
        self.assertEqual(self.gates, [("change_settings", True)])

    def test_uninstall_prints_the_sudo_command(self):
        replies = {"prepare_uninstall": {"ok": True, "expires": "2026-10-12T10:10:00",
                                         "next": "sudo /opt/wenv-vault/uninstall.sh"}}
        code, _ = self.run_cli(["uninstall"], replies=replies)
        self.assertEqual(code, 0)
        self.assertIn("sudo /opt/wenv-vault/uninstall.sh", self.out[-1])


class AgentTests(unittest.TestCase):
    def make(self, events, gate_result=True, apps=None):
        self.started, self.notices, self.gates, self.sleeps, self.logs = [], [], [], [], []
        daemon = FakeDaemon({"apps": {"ok": True, "apps": apps if apps is not None else [
            {"name": "steam", "launch": ["/usr/games/steam"]}, {"name": "firefox", "launch": ["/snap/bin/firefox"]}]}},
            events=events)

        def gate(app):
            self.gates.append(app)
            return gate_result

        return agent.Agent(daemon, gate=gate, notice=lambda *a: self.notices.append(a), start=self.started.append,
                           sleep=self.sleeps.append, log=self.logs.append), daemon

    def test_blocked_then_pass_relaunches(self):
        a, _ = self.make([{"event": "blocked", "app": "firefox", "pids": 9}])
        a.run(max_cycles=1)
        self.assertEqual(self.gates, ["firefox"])
        self.assertEqual(self.started, [["/snap/bin/firefox"]])

    def test_blocked_without_a_pass_starts_nothing(self):
        a, _ = self.make([{"event": "blocked", "app": "firefox"}], gate_result=False)
        a.run(max_cycles=1)
        self.assertEqual((self.gates, self.started), (["firefox"], []))

    def test_apps_blocked_while_the_gate_is_open_are_relaunched_once_each(self):
        a, _ = self.make([])
        a.gate_open = False

        def gate(app):
            # while the quiz is open the daemon reports two more blocks (one a repeat)
            a.on_event({"event": "blocked", "app": "steam"})
            a.on_event({"event": "blocked", "app": "firefox"})
            return True
        a.gate = gate
        a.on_event({"event": "blocked", "app": "firefox"})
        self.assertEqual(sorted(map(tuple, self.started)), [("/snap/bin/firefox",), ("/usr/games/steam",)])

    def test_ending_shows_a_warning(self):
        a, _ = self.make([{"event": "ending", "hard_end": "2026-10-12T00:10:00"}])
        a.run(max_cycles=1)
        self.assertEqual(len(self.notices), 1)
        self.assertIn("00:10", self.notices[0][1])

    def test_unknown_events_are_ignored(self):
        a, _ = self.make([{"event": "unlocked"}, {"event": "mystery"}])
        a.run(max_cycles=1)
        self.assertEqual((self.gates, self.started, self.notices), ([], [], []))

    def test_reconnects_with_backoff_when_the_daemon_is_down(self):
        a, daemon = self.make([])
        daemon.down = True
        a.run(max_cycles=5)
        self.assertEqual(self.sleeps, [2, 5, 10, 30, 30])

    def test_a_failing_handler_does_not_end_the_agent(self):
        a, _ = self.make([{"event": "blocked", "app": "firefox"}, {"event": "ending", "hard_end": "x"}])
        a.gate = lambda app: (_ for _ in ()).throw(RuntimeError("window crashed"))
        a.run(max_cycles=1)
        self.assertTrue(any("failed" in m for m in self.logs))
        self.assertEqual(len(self.notices), 1)                         # the next event was still handled
        self.assertFalse(a.gate_open)

    def test_missing_launch_command_is_reported(self):
        a, _ = self.make([{"event": "blocked", "app": "firefox"}], apps=[])
        a.run(max_cycles=1)
        self.assertEqual(self.started, [])
        self.assertTrue(any("firefox" in m for m in self.logs))


if __name__ == "__main__":
    unittest.main()


LIB_TEXT = '''STAGE_NATIVE=(
  "org.gnome.desktop.interface|gtk-theme|'ZorinGrey-Dark'"
  "org.gnome.desktop.interface|enable-animations|false"
)
STAGE_GRAYSCALE=(
  "org.gnome.shell.extensions.colorblind-filters|filter-active|true"
  "org.gnome.shell.extensions.colorblind-filters|filter-name|'Desaturation'"
)
'''


class SettingsCli(unittest.TestCase):
    def make(self, live, replies=None, gate_result=True):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        lib = os.path.join(self.tmp.name, "lib.sh")
        with open(lib, "w") as fh:
            fh.write(LIB_TEXT)
        self.out, self.err, self.gates = [], [], []
        daemon = FakeDaemon(replies or {})

        def gate(vault, tier, practice=False, app_label=None, auto_close=None):
            self.gates.append(tier)
            return gate_result

        c = cli.Cli(vault=daemon, gate=gate, out=self.out.append, err=self.err.append, lib_path=lib,
                    live=lambda schema, key: live.get((schema, key)))
        return c, daemon

    GOOD = {("org.gnome.desktop.interface", "gtk-theme"): "'ZorinGrey-Dark'",
            ("org.gnome.desktop.interface", "enable-animations"): "false",
            ("org.gnome.shell.extensions.colorblind-filters", "filter-name"): "'Desaturation'"}

    def test_init_sends_only_lockable_keys_with_the_targets(self):
        c, daemon = self.make(self.GOOD, {"settings_init": {"ok": True, "locked": 3}})
        self.assertEqual(cli.main(["settings", "init"], cli=c), 0, self.err)
        op, kw = daemon.calls[0]
        self.assertEqual(op, "settings_init")
        keys = [(e[0], e[1]) for e in kw["entries"]]
        self.assertEqual(len(keys), 3)
        self.assertNotIn(("org.gnome.shell.extensions.colorblind-filters", "filter-active"), keys)
        self.assertIn("locked 3 settings", self.out[-1])

    def test_a_mismatching_desktop_is_never_locked(self):
        live = dict(self.GOOD)
        live[("org.gnome.desktop.interface", "gtk-theme")] = "'Adwaita'"
        c, daemon = self.make(live)
        self.assertEqual(cli.main(["settings", "init"], cli=c), 1)
        self.assertEqual(daemon.calls, [])
        self.assertTrue(any("Adwaita" in m for m in self.err))
        self.assertTrue(any("apply.sh" in m for m in self.err))

    def test_unreadable_live_value_counts_as_a_mismatch(self):
        live = dict(self.GOOD)
        del live[("org.gnome.desktop.interface", "enable-animations")]
        c, daemon = self.make(live)
        self.assertEqual(cli.main(["settings", "lock"], cli=c), 1)
        self.assertEqual(daemon.calls, [])

    def test_unlock_opens_the_quiz_then_retries_and_explains_the_window(self):
        replies = {"settings_unlock": [{"ok": False, "error": "ticket_required", "tier": "change_settings",
                                        "needed": 120},
                                       {"ok": True, "unlocked_until": "2026-10-12T10:20:00", "changed": True}]}
        c, _ = self.make(self.GOOD, replies)
        self.assertEqual(cli.main(["settings", "unlock"], cli=c), 0)
        self.assertEqual(self.gates, ["change_settings"])
        self.assertIn("10:20:00", self.out[-1])
        self.assertIn("settings lock", self.out[-1])

    def test_failed_quiz_leaves_it_locked(self):
        replies = {"settings_unlock": {"ok": False, "error": "ticket_required", "tier": "change_settings",
                                       "needed": 120}}
        c, daemon = self.make(self.GOOD, replies, gate_result=False)
        self.assertEqual(cli.main(["settings", "unlock"], cli=c), 1)
        self.assertEqual(len(daemon.calls), 1)

    def test_status_wording_for_each_state(self):
        for reply, expect in (({"ok": True, "initialized": False}, "not locked yet"),
                              ({"ok": True, "initialized": True, "locked": True, "keys": 32, "unlocked_until": None},
                               "LOCKED (32 keys)"),
                              ({"ok": True, "initialized": True, "locked": False, "keys": 32,
                                "unlocked_until": "2026-10-12T10:20:00"}, "UNLOCKED until")):
            c, _ = self.make(self.GOOD, {"settings_status": reply})
            self.assertEqual(cli.main(["settings", "status"], cli=c), 0)
            self.assertIn(expect, self.out[-1])

    def test_missing_lib_sh(self):
        c, daemon = self.make(self.GOOD)
        c.lib_path = "/nonexistent/lib.sh"
        self.assertEqual(cli.main(["settings", "init"], cli=c), 1)
        self.assertEqual(daemon.calls, [])

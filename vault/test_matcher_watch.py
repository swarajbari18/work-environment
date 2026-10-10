import contextlib
import datetime as dt
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import matcher
import policy
import watch


def proc(pid, ppid, comm, exe="", env=()):
    return {"pid": pid, "ppid": ppid, "comm": comm, "exe": exe, "env": list(env)}


def table(*procs):
    return {p["pid"]: p for p in procs}


APPS = matcher.DEFAULT_APPS


class ParseStat(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(matcher.parse_stat("1234 (firefox) S 1 1234 1234 0 -1 4194560"), ("firefox", 1))

    def test_command_name_with_spaces_and_parentheses(self):
        self.assertEqual(matcher.parse_stat("77 (Web Content) S 42 1 1 0"), ("Web Content", 42))
        self.assertEqual(matcher.parse_stat("78 (a) b) R 9 1 1 0"), ("a) b", 9))


class Classify(unittest.TestCase):
    def setUp(self):
        self.procs = table(
            proc(10, 1, "gnome-shell", "/usr/bin/gnome-shell"),
            proc(20, 10, "gnome-terminal-server", "/usr/libexec/gnome-terminal-server"),
            proc(21, 20, "bash", "/usr/bin/bash"),
            # Steam client, its helpers, and a Proton game
            proc(100, 10, "steam", "/home/u/.local/share/Steam/ubuntu12_32/steam"),
            proc(101, 100, "steamwebhelper", "/home/u/.local/share/Steam/ubuntu12_64/steamwebhelper"),
            proc(102, 100, "reaper", "/home/u/.local/share/Steam/ubuntu12_32/reaper"),
            proc(103, 102, "ForzaHorizon5.e", "/proton/files/bin/wine64-preloader", env=["SteamAppId"]),
            # a game that outlived the client: reparented to init, only the environment gives it away
            proc(200, 1, "wine64-preloader", "/proton/files/bin/wine64-preloader", env=["SteamAppId", "SteamGameId"]),
            # a shell and a script someone started from inside Steam
            proc(110, 100, "bash", "/usr/bin/bash"),
            proc(111, 110, "python3", "/usr/bin/python3"),
            # snap Firefox: root, content processes, and a helper whose parent is elsewhere
            proc(400, 10, "firefox", "/snap/firefox/9036/usr/lib/firefox/firefox"),
            proc(401, 400, "Web Content", "/snap/firefox/9036/usr/lib/firefox/firefox"),
            proc(410, 999, "crashhelper", "/snap/firefox/9036/usr/lib/firefox/crashhelper"),
            # not part of the vault
            proc(500, 10, "chrome", "/opt/google/chrome/chrome"),
            proc(501, 500, "chrome", "/opt/google/chrome/chrome"),
            proc(600, 10, "claude", "/usr/bin/claude"),
            proc(601, 600, "node", "/usr/bin/node"),
        )

    def test_steam_and_games(self):
        got = matcher.classify(self.procs, APPS)
        self.assertEqual(got["steam"], [100, 101, 102, 103, 200])

    def test_firefox_snap_including_orphaned_helper(self):
        self.assertEqual(matcher.classify(self.procs, APPS)["firefox"], [400, 401, 410])

    def test_shells_agents_and_chrome_are_never_members(self):
        members = {p for pids in matcher.classify(self.procs, APPS).values() for p in pids}
        for pid in (10, 20, 21, 110, 111, 500, 501, 600, 601):
            self.assertNotIn(pid, members)

    def test_descent_stops_at_a_safe_process(self):
        procs = dict(self.procs)
        procs[112] = proc(112, 111, "mygame", "/home/u/mygame")        # grandchild of Steam via bash and python
        self.assertNotIn(112, matcher.classify(procs, APPS)["steam"])

    def test_ignore_pids(self):
        got = matcher.classify(self.procs, APPS, ignore_pids=[100])
        self.assertNotIn(100, got["steam"])
        self.assertNotIn(101, got["steam"])                              # its children are no longer reachable

    def test_nothing_running(self):
        got = matcher.classify(table(proc(10, 1, "gnome-shell")), APPS)
        self.assertEqual(got, {"steam": [], "firefox": []})

    def test_custom_app(self):
        apps = [{"name": "discord", "comm": ["Discord"], "exe_prefix": [], "env_any": []}]
        procs = table(proc(1, 0, "Discord", "/usr/share/discord/Discord"), proc(2, 1, "Discord", ""))
        self.assertEqual(matcher.classify(procs, apps), {"discord": [1, 2]})


class Snapshot(unittest.TestCase):
    def test_reads_the_real_process_table(self):
        procs = matcher.snapshot()
        me = procs[os.getpid()]
        self.assertTrue(me["comm"])
        self.assertEqual(me["ppid"], os.getppid())

    def test_fake_proc_dir_env_filter_and_exe_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            def add(pid, comm, ppid, cmdline, environ, exe=None):
                d = os.path.join(root, str(pid))
                os.makedirs(d)
                with open(os.path.join(d, "stat"), "w") as fh:
                    fh.write(f"{pid} ({comm}) S {ppid} 1 1 0\n")
                with open(os.path.join(d, "cmdline"), "wb") as fh:
                    fh.write(cmdline.replace(" ", "\0").encode() + b"\0")
                with open(os.path.join(d, "environ"), "wb") as fh:
                    fh.write("\0".join(environ).encode() + b"\0")
                if exe:
                    os.symlink(exe, os.path.join(d, "exe"))
            add(7, "game", 1, "/games/g --fullscreen", ["HOME=/home/u", "SteamAppId=1551360", "PATH=/bin"], exe="/games/g")
            add(8, "helper", 1, "/snap/firefox/1/firefox -contentproc", ["HOME=/home/u"])
            os.makedirs(os.path.join(root, "notapid"))
            procs = matcher.snapshot(root, env_keys=["SteamAppId", "SteamGameId"])
            self.assertEqual(set(procs), {7, 8})
            self.assertEqual(procs[7]["env"], ["SteamAppId"])
            self.assertEqual(procs[8]["env"], [])
            self.assertEqual(procs[8]["exe"], "/snap/firefox/1/firefox")   # no exe link: argv[0] is used

    def test_unreadable_root(self):
        self.assertEqual(matcher.snapshot("/nonexistent-proc"), {})


class TrackerEvents(unittest.TestCase):
    def test_start_stop_and_already_running(self):
        t0 = dt.datetime(2026, 10, 12, 10, 0, 0)
        tr = watch.Tracker()
        ev = tr.update(t0, {"steam": [1, 2], "firefox": []})
        self.assertEqual([(e["event"], e["app"], e["already_running"]) for e in ev], [("start", "steam", True)])
        self.assertEqual(tr.update(t0 + dt.timedelta(seconds=3), {"steam": [1], "firefox": []}), [])
        ev = tr.update(t0 + dt.timedelta(minutes=5), {"steam": [], "firefox": [9]})
        kinds = sorted((e["event"], e["app"]) for e in ev)
        self.assertEqual(kinds, [("start", "firefox"), ("stop", "steam")])
        stop = next(e for e in ev if e["event"] == "stop")
        self.assertEqual(stop["duration_s"], 300)
        start = next(e for e in ev if e["event"] == "start")
        self.assertFalse(start["already_running"])

    def test_restart_of_an_app_is_a_new_launch(self):
        t0 = dt.datetime(2026, 10, 12, 10, 0, 0)
        tr = watch.Tracker()
        tr.update(t0, {"steam": []})
        a = tr.update(t0 + dt.timedelta(seconds=3), {"steam": [1]})
        b = tr.update(t0 + dt.timedelta(seconds=6), {"steam": []})
        c = tr.update(t0 + dt.timedelta(seconds=9), {"steam": [1]})
        self.assertEqual([e["event"] for e in a + b + c], ["start", "stop", "start"])


class Stamping(unittest.TestCase):
    def test_would_block_only_on_locked_days(self):
        mon = watch.stamp({"event": "start", "app": "steam"}, dt.datetime(2026, 10, 12, 10, 0))
        sat = watch.stamp({"event": "start", "app": "steam"}, dt.datetime(2026, 10, 17, 10, 0))
        self.assertTrue(mon["would_block"])
        self.assertEqual((mon["weekday"], mon["phase"]), ("Mon", "locked"))
        self.assertFalse(sat["would_block"])
        self.assertEqual(sat["phase"], "free")


class Report(unittest.TestCase):
    def lines(self):
        def ev(ts, **kw):
            return json.dumps(watch.stamp(dict(kw), dt.datetime.fromisoformat(ts)))
        return [
            ev("2026-10-12T10:00:00", event="start", app="steam", pids=3, already_running=False),
            ev("2026-10-12T10:01:00", event="tick", apps=["steam"]),
            ev("2026-10-12T10:02:00", event="tick", apps=["steam", "firefox"]),
            ev("2026-10-12T10:03:00", event="stop", app="steam", duration_s=180),
            ev("2026-10-17T11:00:00", event="start", app="firefox", pids=9, already_running=False),
            ev("2026-10-17T11:01:00", event="tick", apps=["firefox"]),
            "not json at all",
        ]

    def test_summarize(self):
        rows = watch.summarize(self.lines())
        steam = rows[("2026-10-12", "steam")]
        self.assertEqual((steam["launches"], steam["minutes"], steam["would_block"]), (1, 2, 1))
        self.assertEqual(rows[("2026-10-12", "firefox")]["minutes"], 1)
        sat = rows[("2026-10-17", "firefox")]
        self.assertEqual((sat["launches"], sat["would_block"], sat["phase"]), (1, 0, "free"))

    def test_since_filter(self):
        rows = watch.summarize(self.lines(), since="2026-10-15")
        self.assertEqual(set(rows), {("2026-10-17", "firefox")})

    def test_report_command(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"WENV_VAULT_DATA": d}):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(watch.main(["report"]), 1)                # no log yet
            with open(os.path.join(d, "watch.jsonl"), "w") as fh:
                fh.write("\n".join(self.lines()) + "\n")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(watch.main(["report"]), 0)
            text = out.getvalue()
            self.assertIn("steam", text)
            self.assertIn("launches enforcement would have stopped: 1", text)


class LoadApps(unittest.TestCase):
    def test_default_when_missing_or_broken(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(watch.load_apps(d), matcher.DEFAULT_APPS)
            with open(os.path.join(d, "apps.json"), "w") as fh:
                fh.write("{broken")
            self.assertEqual(watch.load_apps(d), matcher.DEFAULT_APPS)

    def test_custom_rules(self):
        with tempfile.TemporaryDirectory() as d:
            rules = [{"name": "x", "comm": ["x"]}]
            with open(os.path.join(d, "apps.json"), "w") as fh:
                json.dump(rules, fh)
            self.assertEqual(watch.load_apps(d), rules)


if __name__ == "__main__":
    unittest.main()


class SnapshotCache(unittest.TestCase):
    def test_environment_is_read_once_per_process_identity(self):
        with tempfile.TemporaryDirectory() as root:
            def make(pid, start, environ):
                d = os.path.join(root, str(pid))
                os.makedirs(d, exist_ok=True)
                fields = ["S", "1", "1", "1", "0", "-1", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0",
                          "0", str(start)]
                with open(os.path.join(d, "stat"), "w") as fh:
                    fh.write(f"{pid} (game) " + " ".join(fields) + " 0 0\n")
                with open(os.path.join(d, "cmdline"), "wb") as fh:
                    fh.write(b"/games/g\0")
                with open(os.path.join(d, "environ"), "wb") as fh:
                    fh.write(environ.encode() + b"\0")

            make(7, 100, "SteamAppId=1")
            cache = {}
            first = matcher.snapshot(root, env_keys=["SteamAppId"], cache=cache)
            self.assertEqual(first[7]["env"], ["SteamAppId"])
            self.assertEqual(first[7]["start"], 100)
            make(7, 100, "OTHER=1")                                       # same identity: cached value wins
            self.assertEqual(matcher.snapshot(root, env_keys=["SteamAppId"], cache=cache)[7]["env"], ["SteamAppId"])
            make(7, 999, "OTHER=1")                                       # pid reused: read afresh
            self.assertEqual(matcher.snapshot(root, env_keys=["SteamAppId"], cache=cache)[7]["env"], [])
            self.assertEqual(len(cache), 1)                               # the stale identity was dropped
            os.rename(os.path.join(root, "7"), os.path.join(root, "gone"))
            matcher.snapshot(root, env_keys=["SteamAppId"], cache=cache)
            self.assertEqual(cache, {})

import datetime as dt
import os
import random
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

import config
import enforce
import protocol
from test_engine import bank_of

MON = dt.datetime(2026, 10, 12, 10, 0)
SAT = dt.datetime(2026, 10, 17, 10, 0)
OWNER = 1000


def proc(pid, ppid, comm, exe="", env=(), start=None):
    return {"pid": pid, "ppid": ppid, "comm": comm, "exe": exe, "env": list(env),
            "start": pid * 10 if start is None else start}


class Fixture(unittest.TestCase):
    bank = None

    @classmethod
    def setUpClass(cls):
        cls.bank = bank_of(500)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = config.Paths.from_root(self.tmp.name)
        self.now = MON
        self.mono_now = 1000.0
        self.table = {}
        self.sent = []
        self.emitted = []
        self.build()

    def build(self, policy="default", enforce_on=True):
        if policy == "default":
            policy = config.default_policy(OWNER)
            policy["enforce"] = enforce_on
        self.svc = protocol.Service(self.paths, self.bank, policy, OWNER, clock=lambda: self.now,
                                    rng=random.Random(3), emit=self.emitted.append,
                                    load_error=None if policy else "policy missing")
        self.enf = enforce.Enforcer(self.svc, kill=lambda pid, sig: self.sent.append((pid, sig)),
                                    snapshot=lambda **kw: dict(self.table), mono=lambda: self.mono_now,
                                    term_grace=8.0, self_pid=4242)

    def tearDown(self):
        self.tmp.cleanup()

    def steam_running(self):
        self.table = {p["pid"]: p for p in (
            proc(1, 0, "systemd"), proc(10, 1, "gnome-shell"),
            proc(100, 10, "steam", "/home/u/.local/share/Steam/steam"),
            proc(101, 100, "steamwebhelper"),
            proc(103, 101, "wine64-preloader", env=["SteamAppId"]),
            proc(500, 10, "chrome", "/opt/google/chrome/chrome"),
            proc(600, 10, "claude", "/usr/bin/claude"),
        )}

    def signals(self, sig):
        return sorted(pid for pid, s in self.sent if s == sig)


class DryRun(Fixture):
    def test_logs_would_block_once_and_acts_on_nothing(self):
        self.build(enforce_on=False)
        self.steam_running()
        events = self.enf.tick()
        self.assertEqual([e["event"] for e in events], ["would_block"])
        self.assertEqual(self.sent, [])
        self.assertEqual(self.emitted, [])                        # the agent is not woken in dry-run
        self.enf.tick()
        kinds = [e["kind"] for e in self.svc.call_log()] if hasattr(self.svc, "call_log") else None
        entries = self.svc.op_log({"n": 10})["entries"]
        self.assertEqual([e["kind"] for e in entries].count("would_block"), 1)

    def test_a_new_run_is_a_new_report(self):
        self.build(enforce_on=False)
        self.steam_running()
        self.enf.tick()
        self.table = {}
        self.enf.tick()
        self.steam_running()
        self.assertEqual([e["event"] for e in self.enf.tick()], ["would_block"])


class Enforcing(Fixture):
    def test_term_then_kill_only_the_vault_processes(self):
        self.steam_running()
        events = self.enf.tick()
        self.assertEqual([e["event"] for e in events], ["blocked"])
        self.assertEqual(self.signals(signal.SIGTERM), [100, 101, 103])
        self.assertEqual(self.signals(signal.SIGKILL), [])
        self.assertEqual(self.emitted, [{"event": "blocked", "app": "steam", "pids": 3}])
        untouched = {10, 500, 600, 1}
        self.assertFalse(untouched & {pid for pid, _ in self.sent})

    def test_sigterm_once_then_sigkill_after_the_grace(self):
        self.steam_running()
        self.enf.tick()
        self.mono_now += 2
        self.enf.tick()
        self.assertEqual(self.signals(signal.SIGTERM), [100, 101, 103])    # not repeated
        self.assertEqual(self.signals(signal.SIGKILL), [])
        self.mono_now += 7
        self.enf.tick()
        self.assertEqual(self.signals(signal.SIGKILL), [100, 101, 103])
        self.mono_now += 5
        self.enf.tick()
        self.assertEqual(self.signals(signal.SIGKILL), [100, 101, 103])    # once only

    def test_survivor_only_is_killed(self):
        self.steam_running()
        self.enf.tick()
        self.mono_now += 9
        del self.table[100], self.table[101]                              # these exited after SIGTERM
        self.enf.tick()
        self.assertEqual(self.signals(signal.SIGKILL), [103])

    def test_nothing_after_a_pass(self):
        self.steam_running()
        self.svc.vault.state["day_token"] = "2026-10-12"
        self.assertEqual(self.enf.tick(), [])
        self.assertEqual(self.sent, [])

    def test_weekend_is_free(self):
        self.now = SAT
        self.steam_running()
        self.assertEqual(self.enf.tick(), [])
        self.assertEqual(self.sent, [])

    def test_unlocking_mid_run_stops_the_killing(self):
        self.steam_running()
        self.enf.tick()
        sent = len(self.sent)
        self.svc.vault.state["day_token"] = "2026-10-12"
        self.mono_now += 20
        self.enf.tick()
        self.assertEqual(len(self.sent), sent)

    def test_pid_reuse_is_a_new_process(self):
        self.steam_running()
        self.enf.tick()
        self.mono_now += 100
        self.table[100] = proc(100, 10, "steam", start=99999)             # same pid, new process
        self.enf.tick()
        self.assertEqual(self.signals(signal.SIGTERM).count(100), 2)

    def test_never_targets_itself_init_or_its_children(self):
        self.steam_running()
        self.table[4242] = proc(4242, 100, "steam")
        self.table[4243] = proc(4243, 4242, "steam")
        self.enf.tick()
        self.assertFalse({4242, 4243, 1, 0} & {pid for pid, _ in self.sent})

    def test_a_vanished_process_is_not_an_error(self):
        def kill(pid, sig):
            raise ProcessLookupError()
        self.enf.kill = kill
        self.steam_running()
        self.enf.tick()                                                    # must not raise


class Warnings(Fixture):
    def test_dry_run_never_warns(self):
        self.build(enforce_on=False)
        self.now = dt.datetime(2026, 10, 11, 23, 55)
        self.assertEqual(self.enf.tick(), [])
        self.assertEqual(self.emitted, [])

    def test_ending_warning_once_per_window(self):
        self.now = dt.datetime(2026, 10, 11, 23, 49)
        self.assertEqual(self.enf.tick(), [])
        self.now = dt.datetime(2026, 10, 11, 23, 50)
        events = self.enf.tick()
        self.assertEqual(events, [{"event": "ending", "hard_end": "2026-10-12T00:10:00"}])
        self.assertEqual(self.emitted, events)
        self.now = dt.datetime(2026, 10, 11, 23, 55)
        self.assertEqual(self.enf.tick(), [])

    def test_a_weekday_pass_warns_before_it_runs_out(self):
        self.svc.vault.state["day_token"] = "2026-10-12"
        self.now = dt.datetime(2026, 10, 12, 23, 52)
        self.assertEqual([e["event"] for e in self.enf.tick()], ["ending"])


class SettingsWindow(Fixture):
    def test_an_expired_unlock_window_is_closed_by_the_loop_even_in_dry_run_and_fail_open(self):
        for kwargs in ({"enforce_on": False}, {"policy": None}):
            self.build(**kwargs)
            calls = []
            self.svc.settings = type("G", (), {"tick": lambda s: calls.append(1) or False})()
            self.enf.tick()
            self.assertEqual(calls, [1], kwargs)


class FailOpen(Fixture):
    def test_no_policy_means_no_action_and_one_log_line(self):
        self.build(policy=None)
        self.steam_running()
        self.assertEqual(self.enf.tick(), [])
        self.enf.tick()
        self.assertEqual(self.sent, [])
        entries = self.svc.op_log({"n": 10})["entries"]
        self.assertEqual([e["kind"] for e in entries], ["fail_open"])

    def test_empty_bank_means_no_action(self):
        self.svc = protocol.Service(self.paths, {}, {**config.default_policy(OWNER), "enforce": True}, OWNER,
                                    clock=lambda: self.now, emit=self.emitted.append)
        self.enf.service = self.svc
        self.steam_running()
        self.assertEqual(self.enf.tick(), [])
        self.assertEqual(self.sent, [])

    def test_a_crashing_tick_does_not_stop_the_loop(self):
        import threading
        calls = []

        def boom(**kw):
            calls.append(1)
            if len(calls) >= 3:
                stop.set()
            raise RuntimeError("proc table exploded")
        self.enf.snapshot = boom
        stop = threading.Event()
        self.enf.run(stop, interval=0.01)
        self.assertGreaterEqual(len(calls), 3)
        kinds = [e["kind"] for e in self.svc.op_log({"n": 10})["entries"]]
        self.assertIn("enforce_error", kinds)


class RealProcesses(unittest.TestCase):
    """The same loop against the real process table and real signals."""
    bank = None

    @classmethod
    def setUpClass(cls):
        cls.bank = bank_of(500)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = config.Paths.from_root(self.tmp.name)
        self.bin = os.path.join(self.tmp.name, "bin")
        os.makedirs(self.bin)
        self.children = []

    def tearDown(self):
        for c in self.children:
            try:
                c.kill()
                c.wait(timeout=5)
            except Exception:                                              # noqa: BLE001
                pass
        self.tmp.cleanup()

    def spawn(self, real, name, *args):
        link = os.path.join(self.bin, name)
        if not os.path.exists(link):
            os.symlink(real, link)
        child = subprocess.Popen([link, *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.children.append(child)
        return child

    def service(self, name, enforce_on=True, when=MON):
        # Built by hand: normalize_app would (rightly) refuse the safe-listed names one test deliberately uses.
        policy = {"version": 1, "owner_uid": os.getuid(), "enforce": enforce_on,
                  "apps": [{"name": "dummy", "comm": [name], "exe_prefix": [], "env_any": [], "launch": []}]}
        return protocol.Service(self.paths, self.bank, policy, os.getuid(), clock=lambda: when,
                                rng=random.Random(1), emit=lambda e: None)

    def wait_for(self, predicate, seconds=10.0):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if predicate():
                return True
            time.sleep(0.05)
        return False

    def test_a_locked_app_is_terminated(self):
        child = self.spawn("/bin/sleep", "vaultdummy", "60")
        time.sleep(0.2)
        enf = enforce.Enforcer(self.service("vaultdummy"), term_grace=1.0, self_pid=999999)
        self.assertTrue(self.wait_for(lambda: (enf.tick(), child.poll() is not None)[1]))
        self.assertEqual(child.returncode, -signal.SIGTERM)

    def test_a_stubborn_app_is_killed_after_the_grace(self):
        child = self.spawn("/bin/bash", "vaultstub", "-c", 'trap "" TERM; while :; do sleep 0.1; done')
        time.sleep(0.4)
        enf = enforce.Enforcer(self.service("vaultstub"), term_grace=0.5, self_pid=999999)
        self.assertTrue(self.wait_for(lambda: (enf.tick(), child.poll() is not None)[1], 15))
        self.assertEqual(child.returncode, -signal.SIGKILL)

    def test_the_same_app_is_left_alone_when_unlocked_or_dry_run(self):
        child = self.spawn("/bin/sleep", "vaultdummy", "60")
        time.sleep(0.2)
        for svc in (self.service("vaultdummy", enforce_on=False), self.service("vaultdummy", when=SAT)):
            enf = enforce.Enforcer(svc, term_grace=0.2, self_pid=999999)
            for _ in range(5):
                enf.tick()
                time.sleep(0.1)
        self.assertIsNone(child.poll())

    def test_the_test_runner_and_its_shell_are_never_touched(self):
        # A rule that names the shell and python directly (config refuses it; the matcher must backstop that).
        svc = self.service("python3")
        svc.policy["apps"][0]["comm"] = ["python3", "bash", "sh", "zsh"]
        enf = enforce.Enforcer(svc, term_grace=0.1, self_pid=999999)
        for _ in range(3):
            self.assertEqual([e for e in enf.tick() if e["event"] == "blocked"], [])


if __name__ == "__main__":
    unittest.main()

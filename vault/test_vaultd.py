import datetime as dt
import json
import os
import random
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import client
import config
import enforce
import protocol
import questions
import vaultd
from test_engine import bank_of

MON = dt.datetime(2026, 10, 12, 10, 0)
HERE = os.path.dirname(os.path.abspath(__file__))


class Live(unittest.TestCase):
    """The real server on a real socket, with the enforcer thread, in this process."""
    bank = None

    @classmethod
    def setUpClass(cls):
        cls.bank = bank_of(500)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = config.Paths.from_root(self.tmp.name)
        self.uid = os.getuid()
        self.bin = os.path.join(self.tmp.name, "bin")
        os.makedirs(self.bin)
        self.children = []
        self.listeners = []
        self.make(owner=self.uid)

    def make(self, owner, enforce_on=True, apps=None):
        policy = {"version": 1, "owner_uid": owner, "enforce": enforce_on,
                  "apps": apps if apps is not None else [
                      {"name": "dummy", "comm": ["vaultdummy"], "exe_prefix": [], "env_any": [], "launch": []}]}
        config.save_policy(self.paths.policy_file, policy)
        self.bus = vaultd.EventBus()
        self.now = MON
        self.svc = protocol.Service(self.paths, self.bank, policy, owner, clock=lambda: self.now,
                                    rng=random.Random(5), emit=self.bus.emit)
        self.sock = os.path.join(self.tmp.name, "s.sock")
        self.server = vaultd.Server(self.sock, self.svc, self.bus)
        self.srv_thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05},
                                           daemon=True)
        self.srv_thread.start()
        self.enf = enforce.Enforcer(self.svc, term_grace=1.0, self_pid=999999)
        self.stop, self.enf_thread = enforce.start_thread(self.enf, 0.1)
        self.c = client.VaultClient(self.sock, timeout=5)

    def tearDown(self):
        self.stop.set()
        self.enf_thread.join(timeout=3)
        self.server.shutdown_all()
        self.server.server_close()
        for t in self.listeners:
            t.join(timeout=5)
        for ch in self.children:
            try:
                ch.kill()
                ch.wait(timeout=5)
            except Exception:                                                # noqa: BLE001
                pass
        self.tmp.cleanup()

    def spawn_dummy(self):
        link = os.path.join(self.bin, "vaultdummy")
        if not os.path.exists(link):
            os.symlink("/bin/sleep", link)
        ch = subprocess.Popen([link, "120"])
        self.children.append(ch)
        return ch

    def right_answers(self, tier):
        attempt = self.svc.vault.state["attempts"][tier]
        out = []
        for qid in attempt["ids"]:
            q = self.bank[qid]
            out.append(protocol.engine._perm(attempt, qid, len(q["options"])).index(q["answer"]))
        return out

    def pass_quiz(self, tier="launch"):
        self.assertTrue(self.c.call("present", tier=tier)["ok"])
        r = self.c.call("submit", tier=tier, answers=self.right_answers(tier))
        self.assertTrue(r["passed"])
        return r

    def wait_for(self, predicate, seconds=10.0):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if predicate():
                return True
            time.sleep(0.05)
        return False


class Requests(Live):
    def test_status_over_the_socket(self):
        s = self.c.call("status")
        self.assertTrue(s["ok"])
        self.assertEqual(s["apps"], ["dummy"])
        self.assertTrue(s["gate_required"])

    def test_garbage_does_not_kill_the_connection(self):
        sock = socket.socket(socket.AF_UNIX)
        sock.settimeout(5)
        sock.connect(self.sock)
        sock.sendall(b"this is not json\n" + json.dumps({"op": "status"}).encode() + b"\n")
        reader = client.LineReader(sock)
        first = reader.read()
        second = reader.read()
        sock.close()
        self.assertEqual(first["error"], "bad_request")
        self.assertTrue(second["ok"])

    def test_an_oversized_request_is_refused(self):
        sock = socket.socket(socket.AF_UNIX)
        sock.settimeout(5)
        sock.connect(self.sock)
        sock.sendall(b"x" * (vaultd.MAX_REQUEST + 10))
        reply = client.read_line(sock)
        sock.close()
        self.assertEqual(reply["error"], "too_large")

    def test_other_users_are_refused(self):
        self.stop.set()
        self.server.shutdown_all()
        self.server.server_close()
        self.make(owner=self.uid + 1)
        self.assertEqual(self.c.call("status")["error"], "forbidden")
        with self.assertRaises(client.VaultUnavailable):
            next(self.c.subscribe())

    def test_unreachable_daemon(self):
        with self.assertRaises(client.VaultUnavailable):
            client.VaultClient(os.path.join(self.tmp.name, "absent.sock")).call("status")


class Chain(Live):
    def test_locked_app_dies_then_a_pass_lets_it_live_and_subscribers_hear_about_it(self):
        events, ready = [], threading.Event()

        def listen():
            try:
                for ev in self.c.subscribe():
                    events.append(ev)
            except client.VaultUnavailable:
                pass

        listener = threading.Thread(target=listen, daemon=True)
        listener.start()
        self.listeners.append(listener)
        self.assertTrue(self.wait_for(lambda: self.bus.count() == 1))

        dummy = self.spawn_dummy()
        self.assertTrue(self.wait_for(lambda: dummy.poll() is not None), "locked app survived")
        self.assertEqual(dummy.returncode, -signal.SIGTERM)
        self.assertTrue(self.wait_for(lambda: any(e["event"] == "blocked" for e in events)))

        self.pass_quiz("launch")
        self.assertTrue(self.wait_for(lambda: any(e["event"] == "unlocked" for e in events)))
        again = self.spawn_dummy()
        time.sleep(1.0)
        self.assertIsNone(again.poll(), "an unlocked app was killed")

    def test_events_arriving_together_are_all_delivered(self):
        got = []

        def listen():
            try:
                for ev in self.c.subscribe():
                    got.append(ev)
            except client.VaultUnavailable:
                pass

        listener = threading.Thread(target=listen, daemon=True)
        listener.start()
        self.listeners.append(listener)
        self.assertTrue(self.wait_for(lambda: self.bus.count() == 1))
        for n in range(20):
            self.bus.emit({"event": "n", "n": n})                      # a burst, likely coalesced into few reads
        self.assertTrue(self.wait_for(lambda: len(got) == 20))
        self.assertEqual([e["n"] for e in got], list(range(20)))

    def test_dry_run_leaves_it_running(self):
        self.stop.set()
        self.server.shutdown_all()
        self.server.server_close()
        self.make(owner=self.uid, enforce_on=False)
        dummy = self.spawn_dummy()
        time.sleep(1.0)
        self.assertIsNone(dummy.poll())
        self.assertTrue(self.wait_for(
            lambda: any(e["kind"] == "would_block" for e in self.c.call("log", n=20)["entries"])))

    def test_ratchet_over_the_wire(self):
        self.assertEqual(self.c.call("set_enforce", value=False)["error"], "ticket_required")
        self.pass_quiz("change_settings")
        self.assertTrue(self.c.call("set_enforce", value=False)["ok"])
        dummy = self.spawn_dummy()
        time.sleep(1.0)
        self.assertIsNone(dummy.poll())                         # enforcement is now off


class Process(unittest.TestCase):
    """The daemon as its own process: start, selftest, SIGHUP reload, SIGTERM."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = config.Paths.from_root(self.tmp.name)
        qs = bank_of(300)
        os.makedirs(self.paths.bank_dir)
        questions.write_jsonl(os.path.join(self.paths.bank_dir, "b.jsonl"), list(qs.values()))
        config.save_policy(self.paths.policy_file, config.default_policy(os.getuid()))
        self.sock = os.path.join(self.tmp.name, "d.sock")

    def tearDown(self):
        self.tmp.cleanup()

    def run_daemon(self, *extra):
        return subprocess.Popen([sys.executable, "-I", os.path.join(HERE, "vaultd.py"), "--root", self.tmp.name,
                                 "--socket", self.sock, "--interval", "0.2", *extra],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def wait_socket(self, proc):
        end = time.monotonic() + 10
        while time.monotonic() < end:
            if os.path.exists(self.sock):
                return True
            if proc.poll() is not None:
                return False
            time.sleep(0.05)
        return False

    def test_selftest_ok_and_fail_open(self):
        r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "vaultd.py"), "--root", self.tmp.name,
                            "--selftest"], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        info = json.loads(r.stdout)
        self.assertEqual((info["apps"], info["bank"], info["fail_open"]), (["steam", "firefox"], 300, None))
        os.unlink(self.paths.policy_file)
        r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "vaultd.py"), "--root", self.tmp.name,
                            "--owner-uid", str(os.getuid()), "--selftest"], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 1)
        self.assertIn("policy", json.loads(r.stdout)["fail_open"])

    def test_serves_reloads_and_stops(self):
        proc = self.run_daemon()
        try:
            self.assertTrue(self.wait_socket(proc), proc.stderr.read().decode() if proc.poll() else "no socket")
            c = client.VaultClient(self.sock, timeout=5)
            s = c.call("status")
            self.assertEqual((s["bank_size"], s["enforce"], s["policy_enforce"]), (300, False, False))
            # Enforcement turned on by editing the file and sending SIGHUP (as an admin would).
            policy = config.default_policy(os.getuid())
            policy["enforce"] = True
            config.save_policy(self.paths.policy_file, policy)
            proc.send_signal(signal.SIGHUP)
            end = time.monotonic() + 5
            while time.monotonic() < end and not c.call("status")["policy_enforce"]:
                time.sleep(0.05)
            self.assertTrue(c.call("status")["policy_enforce"])
            proc.send_signal(signal.SIGTERM)
            self.assertEqual(proc.wait(timeout=10), 0)
            self.assertFalse(os.path.exists(self.sock))
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            proc.stdout.close()
            proc.stderr.close()


if __name__ == "__main__":
    unittest.main()

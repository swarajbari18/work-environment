"""The real daemon process, the real client library, the real quiz model and the real CLI, all together."""
import datetime as dt
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest

import cli
import client as vclient
import settings
import config
import engine
import gate
import launch
import questions
from test_engine import bank_of

HERE = os.path.dirname(os.path.abspath(__file__))


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = cls.tmp.name
        cls.paths = config.Paths.from_root(t)
        cls.bank = bank_of(500)
        os.makedirs(cls.paths.bank_dir)
        questions.write_jsonl(os.path.join(cls.paths.bank_dir, "b.jsonl"), list(cls.bank.values()))
        config.save_policy(cls.paths.policy_file, config.default_policy(os.getuid()))
        cls.sock = os.path.join(t, "d.sock")
        # A Monday: the real daemon is started with the injected clock so the weekday rules apply today.
        env = {**os.environ, "WENV_VAULT_NOW": "2026-10-12T10:00:00"}
        cls.proc = subprocess.Popen([sys.executable, "-I", os.path.join(HERE, "vaultd.py"), "--root", t,
                                     "--socket", cls.sock, "--interval", "0.2"], env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        end = time.monotonic() + 10
        while not os.path.exists(cls.sock) and time.monotonic() < end:
            time.sleep(0.05)
        cls.vault = vclient.VaultClient(cls.sock, timeout=10)
        cls.drafts = os.path.join(t, "drafts")

    @classmethod
    def tearDownClass(cls):
        cls.proc.send_signal(signal.SIGTERM)
        try:
            cls.proc.wait(timeout=10)
        finally:
            cls.proc.stdout.close()
            cls.proc.stderr.close()
            cls.tmp.cleanup()

    def right_answers(self, tier):
        """What a perfect quiz-taker would answer, read from the daemon's persisted attempt."""
        with open(self.paths.state_file) as fh:
            attempt = json.load(fh)["attempts"][tier]
        return [engine._perm(attempt, qid, len(self.bank[qid]["options"])).index(self.bank[qid]["answer"])
                for qid in attempt["ids"]]

    def model(self, tier, practice=False):
        return gate.GateModel(self.vault, tier, practice=practice, drafts=self.drafts)

    def test_01_status_before_anything(self):
        s = self.vault.call("status")
        self.assertEqual((s["state"], s["enforce"], s["bank_size"]), ("locked", False, 500))
        self.assertEqual(s["apps"], ["steam", "firefox"])

    def test_02_practice_is_harmless(self):
        m = self.model("launch", practice=True)
        self.assertTrue(m.load())
        self.assertEqual((m.total, m.needed), (30, 24))
        for i in range(m.total):
            m.select(0, index=i)
        self.assertFalse(m.submit())
        self.assertIsNone(m.result["review"])
        self.assertEqual(self.vault.call("status")["state"], "locked")

    def test_03_a_failed_real_attempt_then_a_pass_unlocks(self):
        m = self.model("launch")
        self.assertTrue(m.load())
        first_ids = [q["id"] for q in m.questions]
        self.assertFalse(m.submit())                                  # nothing answered
        self.assertEqual(m.result["score"], 0)
        self.assertTrue(m.retry())
        self.assertEqual([q["id"] for q in m.questions], first_ids)    # same set
        self.assertEqual(m.tries, 1)
        for i, a in enumerate(self.right_answers("launch")):
            m.select(a, index=i)
        self.assertTrue(m.submit())
        self.assertEqual(len(m.result["review"]), 30)
        self.assertTrue(all(r["ok"] for r in m.result["review"]))
        s = self.vault.call("status")
        self.assertEqual((s["state"], s["allowed"]), ("unlocked", True))

    def test_04_next_gate_uses_a_different_set(self):
        used_after_first = self.vault.call("status")["used"]
        self.assertEqual(used_after_first, 30)
        m = self.model("remove_app")
        self.assertTrue(m.load())
        self.assertEqual((m.total, m.needed), (60, 48))
        with open(self.paths.state_file) as fh:
            used = set(json.load(fh)["used"])
        self.assertFalse(used & {q["id"] for q in m.questions})

    def test_05_cli_remove_uses_the_ticket_then_enforce_on_is_free(self):
        out, err = [], []
        passes = []

        def real_gate(vault, tier, practice=False, app_label=None, auto_close=None):
            m = gate.GateModel(vault, tier, practice=practice, drafts=self.drafts)
            m.load()
            for i, a in enumerate(self.right_answers(tier)):
                m.select(a, index=i)
            passes.append(tier)
            return m.submit()

        c = cli.Cli(vault=self.vault, gate=real_gate, out=out.append, err=err.append)
        self.assertEqual(cli.main(["remove", "firefox"], cli=c), 0, err)
        self.assertEqual(passes, ["remove_app"])
        self.assertEqual(self.vault.call("apps")["apps"][0]["name"], "steam")
        self.assertEqual([a["name"] for a in self.vault.call("apps")["apps"]], ["steam"])
        self.assertEqual(cli.main(["enforce", "on"], cli=c), 0, err)
        self.assertEqual(passes, ["remove_app"])                       # no quiz for tightening
        self.assertTrue(self.vault.call("status")["enforce"])
        # the policy file on disk agrees with what the daemon says
        policy, _ = config.load_policy(self.paths.policy_file)
        self.assertEqual(([a["name"] for a in policy["apps"]], policy["enforce"]), (["steam"], True))

    def test_06_launcher_runs_straight_through_while_unlocked(self):
        ran = []
        code = launch.main(["steam", "--", "/bin/true"], vault=self.vault,
                           gate_runner=lambda *a, **k: self.fail("the gate opened while unlocked"),
                           exec_fn=lambda f, a: ran.append(a))
        self.assertEqual((code, ran), (0, [["/bin/true"]]))

    def test_07_log_tells_the_story(self):
        kinds = [e["kind"] for e in self.vault.call("log", n=100)["entries"]]
        for expected in ("start", "quiz", "remove_app", "set_enforce"):
            self.assertIn(expected, kinds)

    def lock_entries(self):
        with open(os.path.join(os.path.dirname(HERE), "lib.sh")) as fh:
            parsed = settings.validate_entries(settings.parse_lib_text(fh.read()))
        return [list(e) for e in parsed]

    def pass_quiz(self, tier):
        m = gate.GateModel(self.vault, tier, drafts=self.drafts)
        self.assertTrue(m.load())
        for i, a in enumerate(self.right_answers(tier)):
            m.select(a, index=i)
        self.assertTrue(m.submit())

    def test_09_settings_lock_with_a_real_dconf_compile(self):
        entries = self.lock_entries()
        self.assertEqual(len(entries), 32)
        self.assertFalse(self.vault.call("settings_status")["initialized"])
        r = self.vault.call("settings_init", entries=entries)
        self.assertEqual(r, {"ok": True, "locked": 32})
        d = self.paths.dconf_dir
        with open(os.path.join(d, "profile", "user")) as fh:
            self.assertEqual(fh.read(), "user-db:user\nsystem-db:wenv\n")
        with open(os.path.join(d, "db", "wenv.d", "locks", "wenv-vault")) as fh:
            locks = fh.read().splitlines()
        self.assertEqual(len(locks), 32)
        self.assertIn("/org/gnome/desktop/interface/gtk-theme", locks)
        self.assertNotIn("/org/gnome/shell/extensions/colorblind-filters/filter-active", locks)
        with open(os.path.join(d, "db", "wenv"), "rb") as fh:
            blob = fh.read()
        self.assertIn(b"ZorinGrey-Dark", blob)                       # a real compiled database, with the values
        self.assertTrue(self.vault.call("settings_status")["locked"])

    def test_10_unlock_needs_the_big_quiz_and_commit_relocks(self):
        entries = self.lock_entries()
        r = self.vault.call("settings_unlock")
        self.assertEqual((r["error"], r["needed"]), ("ticket_required", 120))
        self.pass_quiz("change_settings")
        r = self.vault.call("settings_unlock")
        self.assertEqual((r["ok"], r["changed"]), (True, True))
        locks = os.path.join(self.paths.dconf_dir, "db", "wenv.d", "locks", "wenv-vault")
        self.assertFalse(os.path.exists(locks))                      # writable during the window
        changed = [list(e) for e in entries]
        changed[0][2] = "'Adwaita'" if changed[0][0] == "org.gnome.desktop.interface" else changed[0][2]
        self.assertEqual(self.vault.call("settings_lock", entries=changed)["locked"], 32)
        self.assertTrue(os.path.exists(locks))
        self.assertTrue(self.vault.call("settings_status")["locked"])
        kinds = [e["kind"] for e in self.vault.call("log", n=200)["entries"]]
        for expected in ("settings_init", "settings_unlock", "settings_lock"):
            self.assertIn(expected, kinds)

    def test_08_secrets_never_crossed_the_wire(self):
        # an attempt in progress must reveal neither answers nor explanations
        m = self.model("change_settings")
        self.assertTrue(m.load())
        blob = json.dumps(self.vault.call("present", tier="change_settings"))
        self.assertNotIn('"explanation"', blob)
        self.assertNotIn('"correct"', blob)


if __name__ == "__main__":
    unittest.main()

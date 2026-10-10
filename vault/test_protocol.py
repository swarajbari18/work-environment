import datetime as dt
import json
import os
import random
import stat
import tempfile
import unittest

import config
import protocol
from test_engine import bank_of

MON = dt.datetime(2026, 10, 12, 10, 0)
SAT = dt.datetime(2026, 10, 17, 10, 0)
OWNER = 1000


class Base(unittest.TestCase):
    bank = None

    @classmethod
    def setUpClass(cls):
        cls.bank = bank_of(500)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = config.Paths.from_root(self.tmp.name)
        self.now = MON
        self.events = []
        self.policy = config.default_policy(OWNER)
        config.save_policy(self.paths.policy_file, self.policy)
        self.svc = self.make(policy=self.policy)

    def make(self, policy, bank=None):
        return protocol.Service(self.paths, self.bank if bank is None else bank, policy, OWNER,
                                clock=lambda: self.now, rng=random.Random(11), emit=self.events.append,
                                load_error=None if policy else "policy missing")

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, op, peer=OWNER, **kw):
        return self.svc.handle({"op": op, **kw}, peer)

    def answers(self, tier, n_wrong=0, practice=False):
        vault = self.svc.practice if practice else self.svc.vault
        attempt = vault.state["attempts"][tier]
        out = []
        for qid in attempt["ids"]:
            q = self.bank[qid]
            order = protocol.engine._perm(attempt, qid, len(q["options"]))
            out.append(order.index(q["answer"]))
        for k in range(n_wrong):
            out[k] = (out[k] + 1) % len(self.bank[attempt["ids"][k]]["options"])
        return out

    def pass_quiz(self, tier):
        self.assertTrue(self.call("present", tier=tier)["ok"])
        r = self.call("submit", tier=tier, answers=self.answers(tier))
        self.assertTrue(r["passed"], r)
        return r


class Access(Base):
    def test_only_the_owner_and_root(self):
        self.assertEqual(self.call("status", peer=1001)["error"], "forbidden")
        self.assertTrue(self.call("status", peer=0)["ok"])
        self.assertTrue(self.call("status", peer=OWNER)["ok"])

    def test_bad_requests(self):
        for req in ("status", {}, {"op": 5}, {"op": "nope"}, {"op": "__init__"}, {"op": "../x"}):
            r = self.svc.handle(req, OWNER)
            self.assertFalse(r["ok"], req)
        self.assertEqual(self.call("present", tier="everything")["error"], "bad_request")
        self.assertEqual(self.call("submit", tier="launch", answers="abc")["error"], "bad_request")


class Status(Base):
    def test_dry_run_never_requires_a_gate(self):
        s = self.call("status")
        self.assertEqual((s["state"], s["allowed"], s["enforce"], s["gate_required"]), ("locked", False, False, False))
        self.assertEqual(s["apps"], ["steam", "firefox"])
        self.assertEqual(s["tiers"], {"launch": 30, "remove_app": 60, "change_settings": 120})

    def test_enforcing_on_a_locked_day_requires_the_gate(self):
        self.call("set_enforce", value=True)
        s = self.call("status")
        self.assertTrue(s["enforce"] and s["gate_required"])
        self.now = SAT
        s = self.call("status")
        self.assertEqual((s["state"], s["gate_required"]), ("free", False))

    def test_pass_unlocks_until_midnight(self):
        self.call("set_enforce", value=True)
        self.pass_quiz("launch")
        s = self.call("status")
        self.assertEqual((s["state"], s["allowed"], s["gate_required"]), ("unlocked", True, False))
        self.assertEqual(s["hard_end"], "2026-10-13T00:10:00")
        self.assertEqual(self.events, [{"event": "unlocked"}])
        self.now = dt.datetime(2026, 10, 13, 10, 0)
        self.assertTrue(self.call("status")["gate_required"])


class Quiz(Base):
    def test_fail_gives_no_review_and_no_unlock(self):
        self.call("present", tier="launch")
        r = self.call("submit", tier="launch", answers=self.answers("launch", n_wrong=10))
        self.assertEqual((r["passed"], r["score"], r["review"]), (False, 20, None))
        self.assertNotIn("unlocked", [e["event"] for e in self.events])

    def test_present_never_contains_answers(self):
        r = self.call("present", tier="launch")
        self.assertEqual(len(r["questions"]), 30)
        self.assertEqual(set(r), {"ok", "tier", "tries", "needed", "questions"})
        for q in r["questions"]:
            self.assertEqual(set(q) - {"passage"}, {"id", "stem", "options"})

    def test_review_only_after_a_pass(self):
        r = self.pass_quiz("launch")
        self.assertEqual(len(r["review"]), 30)

    def test_log_records_quizzes(self):
        self.pass_quiz("launch")
        entries = self.call("log", n=5)["entries"]
        self.assertEqual(entries[-1]["kind"], "quiz")
        self.assertTrue(entries[-1]["passed"])

    def test_empty_bank(self):
        self.svc = self.make(self.policy, bank={})
        self.assertEqual(self.call("present", tier="launch")["error"], "bank_unavailable")
        s = self.call("status")
        self.assertIn("bank", s["fail_open"])
        self.assertFalse(s["enforce"])


class Practice(Base):
    def test_practice_changes_nothing_real_except_burning_questions(self):
        first = self.call("practice_present", tier="launch")
        self.assertTrue(first["practice"])
        ids = [q["id"] for q in first["questions"]]
        r = self.call("practice_submit", tier="launch", answers=self.answers("launch", practice=True))
        self.assertTrue(r["passed"])
        self.assertIsNone(self.svc.vault.state["day_token"])
        self.assertEqual(self.svc.vault.state["tickets"], {})
        self.assertTrue(set(ids) <= set(self.svc.vault.state["used"]))        # revealed, so burned for real gates
        real = self.call("present", tier="launch")
        self.assertFalse(set(ids) & {q["id"] for q in real["questions"]})

    def test_failed_practice_burns_nothing(self):
        self.call("practice_present", tier="launch")
        self.call("practice_submit", tier="launch", answers=[None] * 30)
        self.assertEqual(self.svc.vault.state["used"], {})


class Apps(Base):
    def new_app(self, **kw):
        return {"name": "discord", "comm": ["Discord"], "launch": ["/usr/bin/discord"], **kw}

    def test_adding_is_free_and_persists(self):
        r = self.call("add_app", app=self.new_app())
        self.assertEqual(r, {"ok": True, "added": "discord"})
        saved, err = config.load_policy(self.paths.policy_file)
        self.assertIsNone(err)
        self.assertEqual([a["name"] for a in saved["apps"]], ["steam", "firefox", "discord"])
        self.assertEqual(self.call("status")["tickets"], {})

    def test_cannot_add_protected_or_duplicate(self):
        r = self.call("add_app", app={"name": "shell", "comm": ["bash"]})
        self.assertEqual(r["error"], "bad_request")
        self.assertEqual(self.call("add_app", app={"name": "steam", "comm": ["steam2"]})["error"], "exists")
        self.assertEqual(len(self.svc.apps()), 2)

    def test_removal_needs_the_60_quiz_and_spends_the_ticket(self):
        r = self.call("remove_app", name="firefox")
        self.assertEqual((r["error"], r["tier"], r["needed"]), ("ticket_required", "remove_app", 60))
        self.assertEqual(len(self.svc.apps()), 2)
        self.pass_quiz("remove_app")
        self.assertEqual(self.call("remove_app", name="firefox"), {"ok": True, "removed": "firefox"})
        self.assertEqual([a["name"] for a in self.svc.apps()], ["steam"])
        self.assertEqual(self.call("remove_app", name="steam")["error"], "ticket_required")   # one removal per pass

    def test_removing_a_missing_app_spends_no_ticket(self):
        self.pass_quiz("remove_app")
        self.assertEqual(self.call("remove_app", name="ghost")["error"], "not_found")
        self.assertIn("remove_app", self.call("status")["tickets"])

    def test_a_launch_pass_is_not_a_removal_ticket(self):
        self.pass_quiz("launch")
        self.assertEqual(self.call("remove_app", name="firefox")["error"], "ticket_required")

    def test_removal_is_allowed_on_a_free_day_too_but_still_costs(self):
        self.now = SAT
        self.assertEqual(self.call("remove_app", name="firefox")["error"], "ticket_required")


class Enforce(Base):
    def test_turning_on_is_free_and_off_costs_120(self):
        self.assertEqual(self.call("set_enforce", value=True), {"ok": True, "enforce": True, "changed": True})
        r = self.call("set_enforce", value=False)
        self.assertEqual((r["error"], r["tier"], r["needed"]), ("ticket_required", "change_settings", 120))
        self.assertTrue(self.call("status")["enforce"])
        self.pass_quiz("remove_app")                                  # 60 is not enough
        self.assertEqual(self.call("set_enforce", value=False)["error"], "ticket_required")
        self.pass_quiz("change_settings")
        self.assertEqual(self.call("set_enforce", value=False), {"ok": True, "enforce": False, "changed": True})
        self.assertFalse(self.call("status")["enforce"])

    def test_no_change_costs_nothing(self):
        self.assertEqual(self.call("set_enforce", value=False), {"ok": True, "enforce": False, "changed": False})

    def test_value_must_be_boolean(self):
        self.assertEqual(self.call("set_enforce", value="off")["error"], "bad_request")


class Uninstall(Base):
    def test_needs_120_and_writes_a_private_one_time_token(self):
        self.assertEqual(self.call("prepare_uninstall")["error"], "ticket_required")
        self.assertFalse(os.path.exists(self.paths.uninstall_token))
        self.pass_quiz("change_settings")
        r = self.call("prepare_uninstall")
        self.assertTrue(r["ok"])
        self.assertEqual(stat.S_IMODE(os.stat(self.paths.uninstall_token).st_mode), 0o600)
        self.assertEqual(self.call("prepare_uninstall")["error"], "ticket_required")


class FailOpen(Base):
    def test_unusable_policy(self):
        self.svc = self.make(None)
        s = self.call("status")
        self.assertEqual((s["enforce"], s["gate_required"], s["apps"]), (False, False, []))
        self.assertIn("policy", s["fail_open"])
        self.assertEqual(self.call("add_app", app={"name": "x", "comm": ["x"]})["error"], "no_policy")
        self.assertEqual(self.call("set_enforce", value=True)["error"], "no_policy")


if __name__ == "__main__":
    unittest.main()


class SettingsOps(Base):
    ENTRIES = [("org.gnome.desktop.interface", "gtk-theme", "'ZorinGrey-Dark'"),
               ("org.gnome.shell", "favorite-apps", "@as []"),
               ("org.gnome.shell.extensions.just-perfection", "panel", "false")]

    def setUp(self):
        super().setUp()
        self.updates = []
        self.svc = protocol.Service(self.paths, self.bank, self.policy, OWNER, clock=lambda: self.now,
                                    rng=random.Random(11), emit=self.events.append,
                                    dconf_update=lambda d: self.updates.append(d))

    def changed(self):
        return [("org.gnome.desktop.interface", "gtk-theme", "'Adwaita'")] + self.ENTRIES[1:]

    def test_status_before_init(self):
        r = self.call("settings_status")
        self.assertEqual((r["ok"], r["initialized"], r["locked"], r["window_minutes"]), (True, False, False, 20))

    def test_init_and_lock_are_free(self):
        self.assertEqual(self.call("settings_init", entries=self.ENTRIES), {"ok": True, "locked": 3})
        self.assertEqual(self.call("settings_lock", entries=self.ENTRIES), {"ok": True, "locked": 3})
        self.assertEqual(self.call("status")["tickets"], {})
        self.assertTrue(self.call("settings_status")["locked"])
        self.assertEqual(self.call("settings_init", entries=self.ENTRIES)["error"], "settings_error")

    def test_changing_values_while_locked_is_refused(self):
        self.call("settings_init", entries=self.ENTRIES)
        r = self.call("settings_lock", entries=self.changed())
        self.assertEqual((r["ok"], r["error"]), (False, "settings_error"))

    def test_unlock_needs_the_120_quiz(self):
        self.call("settings_init", entries=self.ENTRIES)
        r = self.call("settings_unlock")
        self.assertEqual((r["error"], r["tier"], r["needed"]), ("ticket_required", "change_settings", 120))
        self.pass_quiz("remove_app")                                    # 60 is not enough
        self.assertEqual(self.call("settings_unlock")["error"], "ticket_required")
        self.pass_quiz("change_settings")
        r = self.call("settings_unlock")
        self.assertEqual((r["ok"], r["changed"], r["unlocked_until"]), (True, True, "2026-10-12T10:20:00"))
        self.assertEqual(self.call("status")["tickets"].get("change_settings"), None)     # spent
        self.assertFalse(self.call("settings_status")["locked"])

    def test_commit_inside_the_window_and_unlock_is_idempotent(self):
        self.call("settings_init", entries=self.ENTRIES)
        self.pass_quiz("change_settings")
        self.call("settings_unlock")
        again = self.call("settings_unlock")                             # no second ticket needed
        self.assertEqual((again["ok"], again["changed"]), (True, False))
        self.assertEqual(self.call("settings_lock", entries=self.changed())["locked"], 3)
        self.assertTrue(self.call("settings_status")["locked"])

    def test_unlock_before_init_spends_no_ticket(self):
        self.pass_quiz("change_settings")
        self.assertEqual(self.call("settings_unlock")["error"], "not_initialized")
        self.assertIn("change_settings", self.call("status")["tickets"])

    def test_window_expiry_is_driven_by_the_tick(self):
        self.call("settings_init", entries=self.ENTRIES)
        self.pass_quiz("change_settings")
        self.call("settings_unlock")
        self.now += dt.timedelta(minutes=21)
        self.svc.settings_tick()
        self.assertTrue(self.call("settings_status")["locked"])
        kinds = [e["kind"] for e in self.call("log", n=20)["entries"]]
        self.assertIn("settings_relock", kinds)

    def test_garbage_entries_are_a_bad_request_not_a_crash(self):
        for entries in (None, "x", [("org.gnome.shell", "favorite-apps")], [("org.evil", "k", "1")]):
            r = self.call("settings_init", entries=entries)
            self.assertFalse(r["ok"], entries)
        self.assertEqual(self.updates, [])

    def test_other_users_cannot_touch_settings(self):
        self.assertEqual(self.call("settings_init", peer=1001, entries=self.ENTRIES)["error"], "forbidden")

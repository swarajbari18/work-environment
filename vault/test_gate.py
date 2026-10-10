import os
import tempfile
import unittest

import client as vclient
import gate


class FakeVault:
    """Stands in for the daemon: three questions, the right answer is always shown option 1."""

    def __init__(self, n=3, tries=0, fail_reply=None):
        self.n, self.tries, self.fail_reply, self.calls = n, tries, fail_reply, []

    def questions(self):
        return [{"id": f"q{i}", "stem": f"Question {i}?", "options": ["a", "b", "c", "d"]} for i in range(self.n)]

    def call(self, op, **kw):
        self.calls.append((op, kw))
        if self.fail_reply:
            return self.fail_reply
        if op in ("present", "practice_present"):
            return {"ok": True, "tier": kw["tier"], "tries": self.tries, "needed": 2, "questions": self.questions()}
        correct = sum(1 for a in kw["answers"] if a == 1)
        passed = correct >= 2
        reply = {"ok": True, "tier": kw["tier"], "score": correct, "total": self.n, "needed": 2, "passed": passed,
                 "review": None}
        if passed:
            reply["review"] = [{"id": f"q{i}", "stem": "s", "correct": "b", "given": "b", "ok": a == 1,
                                "explanation": "why"} for i, a in enumerate(kw["answers"])]
        else:
            self.tries += 1
        return reply


class Down:
    def call(self, op, **kw):
        raise vclient.VaultUnavailable("no daemon")


class Model(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def model(self, vault=None, **kw):
        return gate.GateModel(vault or FakeVault(), "launch", drafts=self.tmp.name, **kw)

    def test_load_and_navigation(self):
        m = self.model()
        self.assertTrue(m.load())
        self.assertEqual((m.state, m.total, m.needed, m.index), ("quiz", 3, 2, 0))
        m.next()
        m.next()
        m.next()                                     # clamps
        self.assertEqual(m.index, 2)
        m.prev()
        m.prev()
        m.prev()
        self.assertEqual(m.index, 0)

    def test_unanswered_tracking_and_jump(self):
        m = self.model()
        m.load()
        m.select(1)
        m.go(2)
        m.select(0)
        self.assertEqual(m.unanswered(), [1])
        self.assertEqual(m.next_unanswered(), 1)
        m.select(1)
        self.assertIsNone(m.next_unanswered())

    def test_next_unanswered_wraps_around(self):
        m = self.model(FakeVault(n=4))
        m.load()
        m.select(1, index=1)
        m.select(1, index=2)
        m.select(1, index=3)
        m.go(2)
        self.assertEqual(m.next_unanswered(), 0)

    def test_select_rejects_bad_option(self):
        m = self.model()
        m.load()
        self.assertFalse(m.select(7))
        self.assertEqual(m.answers, [None, None, None])

    def test_pass_gives_a_review_and_clears_the_draft(self):
        m = self.model()
        m.load()
        for i in range(3):
            m.select(1, index=i)
        self.assertTrue(os.path.exists(m._draft_path()))
        self.assertTrue(m.submit())
        self.assertEqual(m.state, "result")
        self.assertEqual(len(m.result["review"]), 3)
        self.assertFalse(os.path.exists(m._draft_path()))

    def test_fail_has_no_review_and_retry_reloads(self):
        vault = FakeVault()
        m = self.model(vault)
        m.load()
        for i in range(3):
            m.select(0, index=i)
        self.assertFalse(m.submit())
        self.assertIsNone(m.result["review"])
        self.assertTrue(m.retry())
        self.assertEqual((m.state, m.tries, m.answers), ("quiz", 1, [None, None, None]))
        self.assertEqual([c[0] for c in vault.calls], ["present", "submit", "present"])

    def test_unanswered_are_sent_as_null(self):
        vault = FakeVault()
        m = self.model(vault)
        m.load()
        m.select(1, index=1)
        m.submit()
        self.assertEqual(vault.calls[-1][1]["answers"], [None, 1, None])

    def test_draft_survives_a_crash_and_is_tied_to_the_exact_set(self):
        m = self.model()
        m.load()
        m.select(1, index=0)
        m.select(2, index=1)
        m.go(2)
        again = self.model()
        again.load()
        self.assertEqual((again.answers, again.index), ([1, 2, None], 2))
        retried = self.model(FakeVault(tries=1))                      # same ids but options reshuffled
        retried.load()
        self.assertEqual(retried.answers, [None, None, None])

    def test_corrupt_draft_is_ignored(self):
        m = self.model()
        m.load()
        with open(m._draft_path(), "w") as fh:
            fh.write("{broken")
        again = self.model()
        again.load()
        self.assertEqual(again.answers, [None, None, None])

    def test_practice_uses_the_practice_ops_and_its_own_draft(self):
        vault = FakeVault()
        m = self.model(vault, practice=True)
        m.load()
        m.select(1, index=0)
        m.submit()
        self.assertEqual([c[0] for c in vault.calls], ["practice_present", "practice_submit"])
        self.assertIn("practice", os.path.basename(m._draft_path()))

    def test_daemon_down_and_refusals_are_errors_not_crashes(self):
        m = self.model(Down())
        self.assertFalse(m.load())
        self.assertEqual(m.state, "error")
        self.assertIn("no daemon", m.error)
        m = self.model(FakeVault(fail_reply={"ok": False, "error": "forbidden", "message": "only the owner"}))
        self.assertFalse(m.load())
        self.assertEqual(m.error, "only the owner")

    def test_submit_failure_keeps_answers(self):
        class Flaky(FakeVault):
            def call(self, op, **kw):
                if op == "submit":
                    raise vclient.VaultUnavailable("connection lost")
                return super().call(op, **kw)
        m = self.model(Flaky())
        m.load()
        m.select(1, index=0)
        self.assertFalse(m.submit())
        self.assertEqual((m.state, m.answers[0]), ("error", 1))


if __name__ == "__main__":
    unittest.main()

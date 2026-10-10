import datetime as dt
import json
import os
import random
import tempfile
import unittest

import engine
import generators
import policy
import questions

MON = dt.datetime(2026, 10, 12, 10, 0)
TUE = dt.datetime(2026, 10, 13, 10, 0)
SAT = dt.datetime(2026, 10, 17, 10, 0)


def bank_of(size, seed=1):
    qs = generators.build(random.Random(seed), size // len(generators.GENERATORS) + 2)
    qs = qs[:size] if len(qs) >= size else qs
    return {q["id"]: q for q in qs}


def right_answers(vault, tier, n_wrong=0):
    """Answers (shown-option indexes) with exactly n_wrong deliberately wrong, using the engine's own key."""
    attempt = vault.state["attempts"][tier]
    out = []
    for qid in attempt["ids"]:
        q = vault.bank[qid]
        order = engine._perm(attempt, qid, len(q["options"]))
        out.append(order.index(q["answer"]))
    for k in range(n_wrong):
        n_opts = len(vault.bank[attempt["ids"][k]]["options"])
        out[k] = (out[k] + 1) % n_opts
    return out


class EngineCase(unittest.TestCase):
    bank = None

    @classmethod
    def setUpClass(cls):
        cls.bank = bank_of(500)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state.json")
        self.vault = engine.Vault(self.bank, self.state, random.Random(42))

    def tearDown(self):
        self.tmp.cleanup()


class Presenting(EngineCase):
    def test_no_answer_key_leaks(self):
        shown = self.vault.present("launch", MON)
        self.assertEqual(len(shown["questions"]), 30)
        self.assertEqual(shown["needed"], 24)
        for q in shown["questions"]:
            self.assertEqual(set(q) - {"passage"}, {"id", "stem", "options"})
        self.assertNotIn('"answer"', json.dumps(shown))
        self.assertNotIn("explanation", json.dumps(shown))

    def test_tier_sizes(self):
        for tier, n in (("launch", 30), ("remove_app", 60), ("change_settings", 120)):
            self.assertEqual(len(self.vault.present(tier, MON)["questions"]), n)

    def test_same_set_on_repeat_and_after_restart(self):
        first = [q["id"] for q in self.vault.present("launch", MON)["questions"]]
        again = [q["id"] for q in self.vault.present("launch", MON)["questions"]]
        restarted = engine.Vault(self.bank, self.state)
        after = [q["id"] for q in restarted.present("launch", TUE)["questions"]]
        self.assertEqual(first, again)
        self.assertEqual(first, after)

    def test_unknown_tier(self):
        with self.assertRaises(KeyError):
            self.vault.present("everything", MON)


class Failing(EngineCase):
    def test_fail_reveals_only_the_score(self):
        self.vault.present("launch", MON)
        result = self.vault.submit("launch", right_answers(self.vault, "launch", n_wrong=7), MON)
        self.assertFalse(result.passed)
        self.assertEqual((result.score, result.total, result.needed), (23, 30, 24))
        self.assertIsNone(result.review)
        self.assertFalse(self.vault.authorize("launch", MON))

    def test_one_short_fails_and_exactly_enough_passes(self):
        self.vault.present("launch", MON)
        self.assertFalse(self.vault.submit("launch", right_answers(self.vault, "launch", 7), MON).passed)
        self.assertTrue(self.vault.submit("launch", right_answers(self.vault, "launch", 6), MON).passed)

    def test_retry_is_the_same_set_with_options_reshuffled(self):
        first = self.vault.present("launch", MON)
        self.vault.submit("launch", [0] * 30, MON)
        second = self.vault.present("launch", MON)
        self.assertEqual([q["id"] for q in first["questions"]], [q["id"] for q in second["questions"]])
        self.assertEqual(second["tries"], 1)
        self.assertTrue(any(a["options"] != b["options"] for a, b in zip(first["questions"], second["questions"])))
        self.assertTrue(all(sorted(a["options"]) == sorted(b["options"])
                            for a, b in zip(first["questions"], second["questions"])))

    def test_grading_follows_the_reshuffled_order(self):
        self.vault.present("launch", MON)
        self.vault.submit("launch", [None] * 30, MON)
        self.assertEqual(self.vault.state["attempts"]["launch"]["tries"], 1)
        self.assertTrue(self.vault.submit("launch", right_answers(self.vault, "launch"), MON).passed)

    def test_skipped_answers_count_as_wrong(self):
        self.vault.present("launch", MON)
        result = self.vault.submit("launch", [None] * 30, MON)
        self.assertEqual(result.score, 0)

    def test_wrong_number_of_answers(self):
        self.vault.present("launch", MON)
        with self.assertRaises(ValueError):
            self.vault.submit("launch", [0] * 29, MON)


class Passing(EngineCase):
    def test_pass_gives_review_and_day_token(self):
        self.vault.present("launch", MON)
        self.assertFalse(self.vault.authorize("launch", MON))
        result = self.vault.submit("launch", right_answers(self.vault, "launch"), MON)
        self.assertTrue(result.passed)
        self.assertEqual(result.score, 30)
        self.assertEqual(len(result.review), 30)
        self.assertTrue(all(r["ok"] and r["explanation"] for r in result.review))
        self.assertTrue(self.vault.authorize("launch", MON))
        self.assertTrue(self.vault.authorize("launch", MON.replace(hour=23, minute=59)))
        self.assertFalse(self.vault.authorize("launch", TUE))

    def test_weekend_needs_no_pass(self):
        self.assertTrue(self.vault.authorize("launch", SAT))

    def test_next_gate_uses_different_questions(self):
        first = [q["id"] for q in self.vault.present("launch", MON)["questions"]]
        self.vault.submit("launch", right_answers(self.vault, "launch"), MON)
        second = [q["id"] for q in self.vault.present("launch", TUE)["questions"]]
        self.assertFalse(set(first) & set(second))
        self.assertEqual(len(second), 30)

    def test_used_questions_survive_restart(self):
        first = [q["id"] for q in self.vault.present("launch", MON)["questions"]]
        self.vault.submit("launch", right_answers(self.vault, "launch"), MON)
        again = engine.Vault(self.bank, self.state, random.Random(5))
        second = [q["id"] for q in again.present("launch", TUE)["questions"]]
        self.assertFalse(set(first) & set(second))

    def test_recycles_oldest_when_the_bank_runs_out(self):
        small = bank_of(40, seed=9)
        vault = engine.Vault(small, self.state, random.Random(2))
        seen = []
        for day in (MON, TUE, dt.datetime(2026, 10, 14, 9, 0)):
            ids = [q["id"] for q in vault.present("launch", day)["questions"]]
            self.assertEqual(len(ids), 30)
            seen.append(ids)
            self.assertTrue(vault.submit("launch", right_answers(vault, "launch"), day).passed)
        self.assertGreater(len(set(seen[0]) & set(seen[1])), 0)   # 40 questions cannot give 60 distinct

    def test_failed_set_is_not_marked_used(self):
        ids = [q["id"] for q in self.vault.present("launch", MON)["questions"]]
        self.vault.submit("launch", [0] * 30, MON)
        self.assertFalse(set(ids) & set(self.vault.state["used"]))


class Tickets(EngineCase):
    def test_sixty_pass_buys_a_one_shot_remove_ticket(self):
        self.assertFalse(self.vault.authorize("remove_app", MON))
        self.vault.present("remove_app", MON)
        result = self.vault.submit("remove_app", right_answers(self.vault, "remove_app"), MON)
        self.assertEqual((result.total, result.needed), (60, 48))
        self.assertTrue(self.vault.authorize("remove_app", MON))
        self.assertFalse(self.vault.authorize("change_settings", MON))
        self.assertTrue(self.vault.consume_ticket("remove_app", MON))
        self.assertFalse(self.vault.authorize("remove_app", MON))
        self.assertFalse(self.vault.consume_ticket("remove_app", MON))

    def test_120_pass_covers_removal_too(self):
        self.vault.present("change_settings", MON)
        result = self.vault.submit("change_settings", right_answers(self.vault, "change_settings"), MON)
        self.assertEqual((result.total, result.needed), (120, 96))
        self.assertTrue(self.vault.authorize("change_settings", MON))
        self.assertTrue(self.vault.authorize("remove_app", MON))
        self.assertTrue(self.vault.authorize("delete_vault", MON))

    def test_ticket_expires(self):
        self.vault.present("remove_app", MON)
        self.vault.submit("remove_app", right_answers(self.vault, "remove_app"), MON)
        self.assertTrue(self.vault.authorize("remove_app", MON + dt.timedelta(minutes=9)))
        self.assertFalse(self.vault.authorize("remove_app", MON + dt.timedelta(minutes=11)))

    def test_spends_the_smallest_sufficient_ticket(self):
        self.vault.state["tickets"] = {"remove_app": (MON + dt.timedelta(minutes=5)).isoformat(),
                                       "change_settings": (MON + dt.timedelta(minutes=5)).isoformat()}
        self.assertTrue(self.vault.consume_ticket("remove_app", MON))
        self.assertEqual(list(self.vault.state["tickets"]), ["change_settings"])

    def test_free_operations_never_ask(self):
        self.assertTrue(self.vault.authorize("add_app", MON))
        self.assertTrue(self.vault.authorize("repair", MON))


class Robustness(EngineCase):
    def test_corrupt_state_is_copied_aside_not_overwritten(self):
        with open(self.state, "w") as fh:
            fh.write("{ this is not json")
        vault = engine.Vault(self.bank, self.state)
        self.assertEqual(vault.state["used"], {})
        asides = [n for n in os.listdir(self.tmp.name) if ".corrupt-" in n]
        self.assertEqual(len(asides), 1)
        with open(os.path.join(self.tmp.name, asides[0])) as fh:
            self.assertEqual(fh.read(), "{ this is not json")

    def test_bank_too_small_for_a_tier(self):
        vault = engine.Vault(bank_of(50, seed=4), self.state)
        with self.assertRaises(questions.BankTooSmall):
            vault.present("change_settings", MON)

    def test_history_is_recorded_and_capped(self):
        self.vault.present("launch", MON)
        self.vault.submit("launch", [None] * 30, MON)
        self.assertEqual(self.vault.state["history"][-1]["score"], 0)
        for _ in range(250):
            self.vault.submit("launch", [None] * 30, MON)
        self.assertEqual(len(self.vault.state["history"]), engine.HISTORY_LIMIT)

    def test_attempt_with_vanished_questions_is_replaced(self):
        self.vault.present("launch", MON)
        smaller = {i: q for i, q in list(self.bank.items())[100:]}
        again = engine.Vault(smaller, self.state, random.Random(1))
        ids = [q["id"] for q in again.present("launch", MON)["questions"]]
        self.assertTrue(all(i in smaller for i in ids))


if __name__ == "__main__":
    unittest.main()

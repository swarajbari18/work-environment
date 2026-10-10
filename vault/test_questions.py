import json
import os
import random
import tempfile
import unittest

import questions
from questions import CATEGORIES


def fake(cat, n, group=None, source="t"):
    return questions.make(source, cat, f"{cat} question {n}", ["a", "b", "c", "d"], n % 4, "why", group=group)


def make_bank(per_cat=40):
    bank = {}
    for cat in CATEGORIES:
        for n in range(per_cat):
            q = fake(cat, n)
            bank[q["id"]] = q
    return bank


class Schema(unittest.TestCase):
    def test_id_is_stable_and_depends_on_content(self):
        a = fake("numeracy", 1)
        self.assertEqual(a["id"], fake("numeracy", 1)["id"])
        self.assertNotEqual(a["id"], fake("numeracy", 2)["id"])
        self.assertNotEqual(a["id"], fake("numeracy", 1, source="other")["id"])

    def test_valid_question(self):
        self.assertEqual(questions.validate(fake("logical", 3)), [])

    def test_rejects_bad_questions(self):
        good = fake("logical", 3)
        bad = [
            {**good, "options": ["a", "a", "b"]},
            {**good, "answer": 9},
            {**good, "answer": True},
            {**good, "category": "gossip"},
            {**good, "stem": "  "},
            {**good, "options": ["only"]},
            {**good, "options": ["a", ""]},
        ]
        for q in bad:
            self.assertTrue(questions.validate(q), q)
        no_stem = dict(good)
        del no_stem["stem"]
        self.assertIn("missing stem", questions.validate(no_stem))


class Loading(unittest.TestCase):
    def test_reports_problems_and_keeps_good_questions(self):
        with tempfile.TemporaryDirectory() as d:
            good = fake("verbal", 1)
            dup = dict(good)
            bad = {**fake("verbal", 2), "answer": 99}
            with open(os.path.join(d, "a.jsonl"), "w") as fh:
                fh.write(json.dumps(good) + "\n\nnot json\n" + json.dumps(bad) + "\n" + json.dumps(dup) + "\n")
            with open(os.path.join(d, "ignored.txt"), "w") as fh:
                fh.write("x")
            bank, problems = questions.load_bank(d)
            self.assertEqual(list(bank), [good["id"]])
            self.assertEqual(len(problems), 2)

    def test_missing_directory(self):
        bank, problems = questions.load_bank("/nonexistent/vault-bank")
        self.assertEqual(bank, {})
        self.assertTrue(problems)

    def test_write_then_load_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            qs = [fake("numeracy", i) for i in range(5)]
            questions.write_jsonl(os.path.join(d, "x.jsonl"), qs)
            bank, problems = questions.load_bank(d)
            self.assertEqual(problems, [])
            self.assertEqual(set(bank), {q["id"] for q in qs})


class Allocation(unittest.TestCase):
    def test_sums_to_n_and_follows_weights(self):
        counts = questions.allocate(30, questions.QUOTAS, {c: 100 for c in CATEGORIES})
        self.assertEqual(sum(counts.values()), 30)
        self.assertEqual(counts["numeracy"], 6)                  # 20 %
        self.assertIn(counts["logical"], (7, 8))                 # 25 %
        self.assertEqual(counts["data_interp"], 3)               # 10 %: one 3-question table group

    def test_redistributes_when_a_category_is_empty(self):
        cap = {c: 100 for c in CATEGORIES}
        cap["emotional"] = 0
        counts = questions.allocate(30, questions.QUOTAS, cap)
        self.assertEqual(counts["emotional"], 0)
        self.assertEqual(sum(counts.values()), 30)

    def test_respects_small_capacity(self):
        cap = {c: 100 for c in CATEGORIES}
        cap["numeracy"] = 2
        counts = questions.allocate(30, questions.QUOTAS, cap)
        self.assertEqual(counts["numeracy"], 2)
        self.assertEqual(sum(counts.values()), 30)

    def test_too_small(self):
        with self.assertRaises(questions.BankTooSmall):
            questions.allocate(30, questions.QUOTAS, {"numeracy": 10})


class Selection(unittest.TestCase):
    def test_exact_size_no_duplicates(self):
        bank = make_bank()
        for n in (30, 60, 120):
            ids = questions.select(bank, n, {}, random.Random(1))
            self.assertEqual(len(ids), n)
            self.assertEqual(len(set(ids)), n)

    def test_deterministic_for_a_seed(self):
        bank = make_bank()
        a = questions.select(bank, 30, {}, random.Random(7))
        b = questions.select(bank, 30, {}, random.Random(7))
        self.assertEqual(a, b)

    def test_unused_questions_come_first(self):
        bank = make_bank(40)
        ids = sorted(bank)
        used = {i: n + 1 for n, i in enumerate(ids[:200])}      # 200 of 280 used
        chosen = questions.select(bank, 30, used, random.Random(3))
        self.assertTrue(all(i not in used for i in chosen))

    def test_least_recently_used_are_recycled_when_exhausted(self):
        bank = make_bank(6)                                     # 42 questions, 6 per category
        ids = sorted(bank)
        used = {i: n + 1 for n, i in enumerate(ids)}            # everything used, in a known order
        chosen = set(questions.select(bank, 30, used, random.Random(3)))
        self.assertEqual(len(chosen), 30)
        for cat in CATEGORIES:
            cat_ids = [i for i in bank if bank[i]["category"] == cat]
            left = [used[i] for i in cat_ids if i not in chosen]
            took = [used[i] for i in cat_ids if i in chosen]
            if left and took:
                self.assertLess(max(took), min(left), cat)      # only the oldest of each category come back

    def test_passage_groups_stay_together(self):
        bank = {}
        for g in range(10):
            for k in range(3):
                q = fake("data_interp", g * 3 + k, group=f"g{g}")
                bank[q["id"]] = q
        for cat in CATEGORIES:
            if cat == "data_interp":
                continue
            for n in range(20):
                q = fake(cat, n)
                bank[q["id"]] = q
        for seed in range(20):
            chosen = questions.select(bank, 30, {}, random.Random(seed))
            by_group = {}
            for i in chosen:
                g = bank[i].get("group")
                if g:
                    by_group.setdefault(g, []).append(i)
            for g, members in by_group.items():
                self.assertEqual(len(members), 3, (seed, g))
            self.assertEqual(len(chosen), 30)

    def test_never_recycles_while_unused_exist_even_when_a_group_cannot_fit(self):
        bank = {}
        for g in range(6):
            for k in range(3):
                q = fake("data_interp", g * 3 + k, group=f"g{g}")
                bank[q["id"]] = q
        for cat in CATEGORIES:
            if cat != "data_interp":
                for n in range(15):
                    q = fake(cat, n)
                    bank[q["id"]] = q
        used = {i: s + 1 for s, i in enumerate(i for i, q in bank.items() if q["category"] == "data_interp")}
        for seed in range(10):
            chosen = questions.select(bank, 10, used, random.Random(seed))   # data_interp share is 1: no group fits
            self.assertEqual(len(chosen), 10)
            self.assertFalse(set(chosen) & set(used), seed)

    def test_bank_too_small(self):
        bank = make_bank(2)                                     # 14 questions
        with self.assertRaises(questions.BankTooSmall):
            questions.select(bank, 30, {}, random.Random(1))

    def test_works_with_missing_categories(self):
        bank = {}
        for cat in ("numeracy", "logical"):
            for n in range(40):
                q = fake(cat, n)
                bank[q["id"]] = q
        chosen = questions.select(bank, 30, {}, random.Random(5))
        self.assertEqual(len(chosen), 30)


if __name__ == "__main__":
    unittest.main()

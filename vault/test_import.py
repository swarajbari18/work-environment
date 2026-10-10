import unittest

import import_dataset as imp
import questions


class AquaRat(unittest.TestCase):
    row = {"question": "A train 100 m long runs at 36 km/h. How many seconds to pass a pole?",
           "options": ["A)5", "B)10", "C)15", "D)20", "E)25"], "rationale": "36 km/h = 10 m/s. 100/10 = 10 s. Answer is B",
           "correct": "B"}

    def test_maps_and_strips_option_prefixes(self):
        q = imp.from_aqua_rat(self.row)
        self.assertEqual(q["options"], ["5", "10", "15", "20", "25"])
        self.assertEqual(q["answer"], 1)
        self.assertEqual(q["category"], "numeracy")
        self.assertEqual(questions.validate(q), [])

    def test_drops_rationale_that_contradicts_the_key(self):
        self.assertIsNone(imp.from_aqua_rat({**self.row, "correct": "C"}))

    def test_drops_missing_rationale_figures_and_bad_options(self):
        self.assertIsNone(imp.from_aqua_rat({**self.row, "rationale": ""}))
        self.assertIsNone(imp.from_aqua_rat({**self.row, "question": "See the figure below. " + self.row["question"]}))
        self.assertIsNone(imp.from_aqua_rat({**self.row, "options": ["A)5", "B)5", "C)15", "D)20", "E)25"]}))
        self.assertIsNone(imp.from_aqua_rat({**self.row, "options": ["A)5", "B)10"]}))

    def test_keeps_when_rationale_states_no_letter(self):
        self.assertIsNotNone(imp.from_aqua_rat({**self.row, "rationale": "100/10 = 10 s."}))


class Arc(unittest.TestCase):
    row = {"question": "Which gas do plants take in?", "answerKey": "B",
           "choices": {"text": ["Oxygen", "Carbon dioxide", "Helium", "Neon"], "label": ["A", "B", "C", "D"]}}

    def test_maps_by_label(self):
        q = imp.from_arc(self.row)
        self.assertEqual(q["options"][q["answer"]], "Carbon dioxide")

    def test_numeric_labels(self):
        row = {**self.row, "answerKey": "2", "choices": {"text": ["a", "b", "c", "d"], "label": ["1", "2", "3", "4"]}}
        self.assertEqual(imp.from_arc(row)["answer"], 1)

    def test_drops_picture_questions_and_unknown_keys(self):
        self.assertIsNone(imp.from_arc({**self.row, "question": "Which is shown in the diagram?"}))
        self.assertIsNone(imp.from_arc({**self.row, "answerKey": "Z"}))


class Mmlu(unittest.TestCase):
    row = {"question": "What is 2 + 2?", "subject": "high_school_mathematics", "choices": ["3", "4", "5", "6"], "answer": 1}

    def test_category_by_subject(self):
        self.assertEqual(imp.from_mmlu(self.row)["category"], "hs_math")
        self.assertEqual(imp.from_mmlu({**self.row, "subject": "astronomy"})["category"], "hs_science")
        self.assertIsNone(imp.from_mmlu({**self.row, "subject": "philosophy"}))

    def test_answer_index(self):
        q = imp.from_mmlu(self.row)
        self.assertEqual(q["options"][q["answer"]], "4")


class Sciq(unittest.TestCase):
    row = {"question": "What do bees make?", "correct_answer": "honey", "distractor1": "milk", "distractor2": "silk",
           "distractor3": "wax paper", "support": "Bees make honey from nectar."}

    def test_correct_answer_survives_shuffling(self):
        q = imp.from_sciq(self.row)
        self.assertEqual(q["options"][q["answer"]], "honey")
        self.assertEqual(sorted(q["options"]), ["honey", "milk", "silk", "wax paper"])
        self.assertEqual(imp.from_sciq(self.row)["options"], q["options"])   # deterministic

    def test_drops_duplicates(self):
        self.assertIsNone(imp.from_sciq({**self.row, "distractor1": "honey"}))


class LogiQA2(unittest.TestCase):
    row = {"id": 1, "answer": 2, "text": "All birds fly. Penguins are birds.", "question": "Which must be true?",
           "options": ["Penguins swim", "Penguins fly", "Birds are penguins", "None of these"]}

    def test_passage_and_zero_based_answer(self):
        q = imp.from_logiqa2(self.row)
        self.assertEqual(q["passage"], self.row["text"])
        self.assertEqual(q["options"][q["answer"]], "Birds are penguins")
        self.assertEqual(q["category"], "logical")

    def test_rejects_malformed(self):
        self.assertIsNone(imp.from_logiqa2({**self.row, "answer": 4}))
        self.assertIsNone(imp.from_logiqa2({**self.row, "options": ["a", "b", "c"]}))


class Race(unittest.TestCase):
    def rows(self):
        article = "Nancy lives on a farm. It rained for a week and the river rose."
        opts = ["a", "b", "c", "d"]
        return [
            {"article": article, "question": "Where does Nancy live?", "options": opts, "answer": "A"},
            {"article": article, "question": "How long did it rain?", "options": opts, "answer": "C"},
            {"article": article, "question": "The river _ after the rain.", "options": opts, "answer": "B"},
            {"article": "A lonely article.", "question": "One question only?", "options": opts, "answer": "A"},
        ]

    def test_groups_by_article_and_drops_cloze_and_singletons(self):
        made = imp.from_race(self.rows())
        self.assertEqual(len(made), 2)
        self.assertEqual(len({q["group"] for q in made}), 1)
        self.assertTrue(all(q["category"] == "verbal" and q["passage"] for q in made))
        self.assertEqual(made[1]["answer"], 2)


if __name__ == "__main__":
    unittest.main()

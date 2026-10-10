"""Every generated question is re-solved here by a different method, working from the question text where possible.

A generator and its checker share no code: the checkers parse the stem and brute-force, simulate or use datetime /
Zeller's congruence instead of the formulas the generators use.
"""
import itertools
import math
import random
import re
import unittest
from fractions import Fraction

import generators
import questions

DRAWS = 120


def correct_text(q):
    return q["options"][q["answer"]]


def number(text):
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    return Fraction(m.group(0))


def ints(text):
    return [int(x) for x in re.findall(r"\d+", text)]


# --- independent solvers: each returns True when the stored answer agrees with the recomputed one -------------
def check_percent_change(q):
    stem = q["stem"]
    if "marked price" in stem:
        pct, sp = (int(x) for x in re.search(r"discount of (\d+)%.*?Rs (\d+)", stem).groups())
        for marked in range(1, 5000):                              # brute force: which marked price gives sp
            if marked * (100 - pct) == sp * 100:
                return number(correct_text(q)) == marked
        return False
    old, new = (int(x) for x in re.search(r"from Rs (\d+) to Rs (\d+)", stem).groups())
    word = "increase" if "increase?" in stem else "decrease"
    if (new > old) != (word == "increase"):
        return False
    return number(correct_text(q)) == Fraction(abs(new - old) * 100, old)


def check_successive_change(q):
    a, b = (int(x) for x in re.search(r"by (\d+)% and then \w+ by (\d+)%", q["stem"]).groups())
    s1 = 1 if "increased by %d%% and" % a in q["stem"] else -1
    s2 = 1 if re.search(r"then increased by", q["stem"]) else -1
    value = 100.0
    value += s1 * value * a / 100
    value += s2 * value * b / 100
    return abs(float(number(correct_text(q))) - (value - 100)) < 0.01


def check_ratio_share(q):
    total = int(re.search(r"Rs (\d+) is divided", q["stem"]).group(1))
    parts = [int(x) for x in re.search(r"ratio (\d+):(\d+):(\d+)", q["stem"]).groups()]
    who = "ABC".index(re.search(r"What is (\w)'s share", q["stem"]).group(1))
    shares = [Fraction(total * p, sum(parts)) for p in parts]
    return sum(shares) == total and number(correct_text(q)) == shares[who]


def check_average_speed(q):
    dist, v1, v2 = (int(x) for x in re.search(r"travels (\d+) km at (\d+) km/h and returns the same \d+ km at (\d+)", q["stem"]).groups())
    hours = Fraction(dist, v1) + Fraction(dist, v2)
    return number(correct_text(q)) == Fraction(2 * dist) / hours


def check_train_cross(q):
    length, kmph = (int(x) for x in re.search(r"A (\d+) m long train runs at (\d+) km/h", q["stem"]).groups())
    seconds = Fraction(length) / (Fraction(kmph * 1000, 3600))
    return number(correct_text(q)) == seconds


def check_work_rate(q):
    a, b = (int(x) for x in re.search(r"in (\d+) days and B can finish it in (\d+) days", q["stem"]).groups())
    done, days = Fraction(0), 0
    while done < 1:                                                # simulate: they add their work day by day
        done += Fraction(1, a) + Fraction(1, b)
        days += 1
    return done == 1 and number(correct_text(q)) == days


def check_age_problem(q):
    s, n, k = (int(x) for x in re.search(r"is (\d+) years\. (\d+) years ago, A was (\d+) times", q["stem"]).groups())
    solutions = [(A, s - A) for A in range(1, s) if A - n == k * ((s - A) - n) and (s - A) > n]
    return len(solutions) == 1 and number(correct_text(q)) == solutions[0][0]


def check_profit_loss(q):
    x, y = (int(v) for v in re.search(r"marks his goods (\d+)% above cost price and allows a discount of (\d+)%", q["stem"]).groups())
    cost = Fraction(200)
    selling = cost * (100 + x) / 100 * (100 - y) / 100
    pct = (selling - cost) * 100 / cost
    text = correct_text(q)
    return Fraction(re.search(r"[\d.]+", text).group(0)) == abs(pct) and (("profit" in text) == (pct > 0))


def check_average_removed(q):
    n, a, b = (int(x) for x in re.search(r"average of (\d+) numbers is (\d+).*?becomes (\d+)", q["stem"], re.S).groups())
    for removed in range(1, 2000):
        if (n * a - removed) == (n - 1) * b:
            return number(correct_text(q)) == removed
    return False


def zeller(y, m, d):
    """Day of week by Zeller's congruence: 0 = Monday ... 6 = Sunday."""
    if m < 3:
        m += 12
        y -= 1
    k, j = y % 100, y // 100
    h = (d + 13 * (m + 1) // 5 + k + k // 4 + j // 4 + 5 * j) % 7   # 0 = Saturday
    return (h + 5) % 7


def check_calendar_day(q):
    import datetime as dt
    dates = re.findall(r"(\d{2} \w+ \d{4})", q["stem"])
    d2 = dt.datetime.strptime(dates[1], "%d %B %Y")
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    d1 = dt.datetime.strptime(dates[0], "%d %B %Y")
    stated = re.search(r"is a (\w+),", q["stem"]).group(1)
    return (names[zeller(d1.year, d1.month, d1.day)] == stated
            and correct_text(q) == names[zeller(d2.year, d2.month, d2.day)])


def check_clock_angle(q):
    h, m = (int(x) for x in re.search(r"at (\d+):(\d+)\?", q["stem"]).groups())
    hour_hand = Fraction((h % 12) * 60 + m, 2)                       # degrees, from 12 o'clock
    minute_hand = Fraction(m * 6)
    diff = abs(hour_hand - minute_hand)
    return number(correct_text(q)) == min(diff, 360 - diff)


def check_probability(q):
    stem = q["stem"]
    if "dice" in stem:
        k = int(re.search(r"sum of the numbers is (\d+)", stem).group(1))
        hits = sum(1 for a, b in itertools.product(range(1, 7), repeat=2) if a + b == k)
        want = Fraction(hits, 36)
    else:
        r, b, g = (int(x) for x in re.search(r"(\d+) red, (\d+) blue and (\d+) green", stem).groups())
        colour = re.search(r"not (\w+)\?", stem).group(1)
        n_c = {"red": r, "blue": b, "green": g}[colour]
        want = Fraction(r + b + g - n_c, r + b + g)
    text = correct_text(q)
    got = Fraction(text) if "/" not in text else Fraction(int(text.split("/")[0]), int(text.split("/")[1]))
    return got == want


def check_number_series(q):
    p = q["meta"]["params"]
    kind, t = p["kind"], p["terms"]
    n = len(t)
    if kind == "arithmetic":
        want = [p["a"] + p["d"] * i for i in range(n)]
    elif kind == "geometric":
        want = [p["a"] * p["r"] ** i for i in range(n)]
    elif kind == "squares":
        want = [(p["s"] + i) ** 2 + p["c"] for i in range(n)]
    elif kind == "alt_add":
        want = [p["a"] + (i + 1) // 2 * p["p"] - i // 2 * p["q"] for i in range(n)]
    elif kind == "growing_gap":
        want = [p["a"] + i * p["d"] + p["e"] * i * (i - 1) // 2 for i in range(n)]
    elif kind == "fibonacci":
        phi = lambda i: round(((1 + 5 ** 0.5) / 2) ** i / 5 ** 0.5)
        fib = lambda i: phi(i)                                        # F(0)=0, F(1)=1
        want = [p["a"] * fib(i - 1) + p["b"] * fib(i) if i else p["a"] for i in range(n)]
        want[1] = p["b"]
    else:                                                             # double_add: t(i) = 2^i (a + k) - k
        want = [2 ** i * (p["a"] + p["k"]) - p["k"] for i in range(n)]
    shown = [int(x) for x in re.findall(r"-?\d+", q["stem"].split(":")[1])]
    return want == t and shown == t[:-1] and number(correct_text(q)) == t[-1]


def holds_brute(stmt, universe):
    kind, p, qq = stmt
    in_p = [(r >> p) & 1 for r in universe]
    in_q = [(r >> qq) & 1 for r in universe]
    if kind == "all":
        return all(not a or b for a, b in zip(in_p, in_q))
    if kind == "no":
        return not any(a and b for a, b in zip(in_p, in_q))
    if kind == "some":
        return any(a and b for a, b in zip(in_p, in_q))
    return any(a and not b for a, b in zip(in_p, in_q))               # some_not


def parse_syllogism_stem(q):
    """Statements and conclusions as (kind, p, q) read back out of the displayed text."""
    terms = q["meta"]["params"]["terms"]
    out = []
    for line in q["stem"].splitlines():
        m = re.match(r"\s*(?:\d+|I+)\.\s+(All|No|Some) (\w+) are (not )?(\w+)\.", line)
        if not m:
            continue
        word, p, neg, r = m.groups()
        kind = {"All": "all", "No": "no", "Some": "some_not" if neg else "some"}[word]
        out.append((kind, terms.index(p), terms.index(r)))
    return out


def check_syllogism(q):
    prem = [tuple(x) for x in q["meta"]["params"]["premises"]]
    c1, c2 = (tuple(x) for x in q["meta"]["params"]["conclusions"])
    if parse_syllogism_stem(q) != [*prem, c1, c2]:
        return False                                                  # the text must say what the params say
    f1 = f2 = either = True
    anything = False
    universes = (u for size in range(1, 7) for u in itertools.combinations_with_replacement(range(8), size))
    for universe in universes:                                        # every set of up to 6 individuals
        if not all(any((r >> t) & 1 for r in universe) for t in range(3)):
            continue                                                  # every term has a member
        if not all(holds_brute(s, universe) for s in prem):
            continue
        anything = True
        a, b = holds_brute(c1, universe), holds_brute(c2, universe)
        f1 &= a
        f2 &= b
        either &= (a or b)
    if not anything:
        return False
    if f1 and f2:
        want = 4
    elif f1:
        want = 0
    elif f2:
        want = 1
    elif either:
        want = 2
    else:
        want = 3
    return q["answer"] == want


def check_letter_code(q):
    ex, enc, target = re.search(r"(\w+) is written as (\w+)\. How is (\w+) written", q["stem"]).groups()
    shift = (ord(enc[0]) - ord(ex[0])) % 26
    assert all((ord(e) - ord(x)) % 26 == shift for x, e in zip(ex, enc))
    want = "".join(chr((ord(c) - 65 + shift) % 26 + 65) for c in target)
    return correct_text(q) == want


def check_direction(q):
    a, b, c, d = (int(x) for x in re.search(r"walks (\d+) km North, then (\d+) km East, then (\d+) km South, then (\d+) km West", q["stem"]).groups())
    x, y = b - d, a - c
    if "How far" in q["stem"]:
        return number(correct_text(q)) == math.hypot(x, y) and math.hypot(x, y) == int(math.hypot(x, y))
    names = ["North", "North-East", "East", "South-East", "South", "South-West", "West", "North-West"]
    angle = math.degrees(math.atan2(x, y)) % 360
    if abs(angle / 45 - round(angle / 45)) > 1e-9:
        return False                                                  # must be an exact compass point
    return correct_text(q) == names[round(angle / 45) % 8]


KIN_MALE = {"father", "brother", "son", "husband"}
KIN_FEMALE = {"mother", "sister", "daughter", "wife"}


def check_blood_relation(q):
    """Mini kinship reasoner over the statements in the stem. Standard exam assumptions: full siblings,
    a father's wife is the mother, and a person's sex is known only from the relation words used about them."""
    text = q["stem"]
    pairs = re.findall(r"(\w+) is the ([\w-]+) of (\w+)\.", text)
    asked = re.search(r"How is (\w+) related to (\w+)\?", text).groups()
    parent, sibling, spouse, sex = set(), set(), set(), {}
    for x, rel, y in pairs:
        sex[x] = "m" if rel in KIN_MALE else "f"
        if rel in ("father", "mother"):
            parent.add((x, y))
        elif rel in ("son", "daughter"):
            parent.add((y, x))
        elif rel in ("brother", "sister"):
            sibling.add((x, y))
            sibling.add((y, x))
        elif rel in ("wife", "husband"):
            spouse.add((x, y))
            spouse.add((y, x))
    changed = True
    while changed:
        changed = False
        for p, c in list(parent):
            for a, b in list(spouse):
                if a == p and (b, c) not in parent:
                    parent.add((b, c))
                    changed = True
            for a, b in list(sibling):
                if a == c and (p, b) not in parent:
                    parent.add((p, b))
                    changed = True
    people = {x for pair in parent | sibling | spouse for x in pair}
    X, Y = asked
    gender = sex.get(X)
    term = None
    if (X, Y) in parent:
        term = "father" if gender == "m" else "mother"
    elif (Y, X) in parent:
        term = "son" if gender == "m" else "daughter"
    elif (X, Y) in sibling:
        term = "brother" if gender == "m" else "sister"
    if term is None:
        for m in people:
            if (X, m) in parent and (m, Y) in parent:
                term = "grandfather" if gender == "m" else "grandmother"
            if (X, m) in sibling and (m, Y) in parent:
                term = "uncle" if gender == "m" else "aunt"
            if (Y, m) in sibling and (m, X) in parent:
                term = "nephew" if gender == "m" else "niece"
            if (X, m) in spouse and (Y, m) in parent:
                term = "son-in-law" if gender == "m" else "daughter-in-law"
            if (X, m) in parent and (m, Y) in spouse:
                term = "father-in-law" if gender == "m" else "mother-in-law"
    if term is None:
        for m, n in itertools.product(people, repeat=2):
            if (m, X) in parent and (n, Y) in parent and (m, n) in sibling:
                term = "cousin"
    return term is not None and correct_text(q).lower() == term


def parse_table(passage):
    table = {}
    for line in passage.splitlines()[2:]:
        year, *vals = line.split()
        table[int(year)] = dict(zip(["Alpha", "Beta", "Gamma", "Delta"], (int(v) for v in vals)))
    return table


def check_table_group(q):
    t = parse_table(q["passage"])
    stem = q["stem"]
    if stem.startswith("What percentage"):
        year, comp = re.search(r"in (\d{4}) was produced by (\w+)", stem).groups()
        row = t[int(year)]
        return number(correct_text(q)) == Fraction(row[comp] * 100, sum(row.values()))
    if stem.startswith("By how many"):
        comp, y1, y2 = re.search(r"did (\w+)'s production change from (\d{4}) to (\d{4})", stem).groups()
        return number(correct_text(q)) == t[int(y2)][comp] - t[int(y1)][comp]
    year = int(re.search(r"most in (\d{4})", stem).group(1))
    row = t[year]
    ranked = sorted(row, key=row.get, reverse=True)
    return row[ranked[0]] > row[ranked[1]] and correct_text(q) == ranked[0]


CHECKERS = {
    "percent_change": check_percent_change,
    "successive_change": check_successive_change,
    "ratio_share": check_ratio_share,
    "average_speed": check_average_speed,
    "train_cross": check_train_cross,
    "work_rate": check_work_rate,
    "age_problem": check_age_problem,
    "profit_loss": check_profit_loss,
    "average_removed": check_average_removed,
    "calendar_day": check_calendar_day,
    "clock_angle": check_clock_angle,
    "probability": check_probability,
    "number_series": check_number_series,
    "syllogism": check_syllogism,
    "letter_code": check_letter_code,
    "direction": check_direction,
    "blood_relation": check_blood_relation,
    "table_group": check_table_group,
}


class EveryGeneratorIsChecked(unittest.TestCase):
    def test_checker_exists_for_each_generator(self):
        self.assertEqual(set(CHECKERS), set(generators.GENERATORS))


class GeneratorOutput(unittest.TestCase):
    pass


def _make_test(name):
    def test(self):
        rng = random.Random(f"vault-{name}")
        seen = set()
        for _ in range(DRAWS):
            for q in generators.generate(rng, name):
                self.assertEqual(questions.validate(q), [], q)
                self.assertEqual(q["meta"]["gen"], name)
                self.assertNotIn("None", q["stem"])
                self.assertNotRegex(q["stem"], r"[{}]")
                self.assertTrue(q["explanation"])
                self.assertTrue(CHECKERS[name](q), f"{name}: stored answer disagrees with recomputation\n{q}")
                seen.add(q["id"])
        self.assertGreater(len(seen), DRAWS * 0.2, f"{name} produces too few distinct questions")
    return test


for _name in generators.GENERATORS:
    setattr(GeneratorOutput, f"test_{_name}", _make_test(_name))


class KinshipNames(unittest.TestCase):
    def test_names_agree_with_the_relation_words(self):
        rng = random.Random(77)
        checked = 0
        for _ in range(300):
            q = generators.generate(rng, "blood_relation")[0]
            for who, rel in re.findall(r"(\w+) is the ([\w-]+) of", q["stem"]):
                if rel in generators.MALE_RELATIONS:
                    self.assertIn(who, generators.MALE_NAMES, q["stem"])
                elif rel in generators.FEMALE_RELATIONS:
                    self.assertIn(who, generators.FEMALE_NAMES, q["stem"])
                else:
                    self.fail(f"unclassified relation {rel!r}")
                checked += 1
        self.assertGreater(checked, 400)

    def test_every_template_relation_is_classified(self):
        words = set()
        for statements, _ask, answer in generators._KIN_TEMPLATES:
            words.add(answer)
            for st in statements:
                words.add(re.match(r"\w is the ([\w-]+) of", st).group(1))
        words |= {generators._KIN_SWAP.get(w, w) for w in set(words)}
        known = generators.MALE_RELATIONS | generators.FEMALE_RELATIONS | {"cousin"}
        self.assertEqual(words - known, set())


class Build(unittest.TestCase):
    def test_build_is_deduplicated_and_covers_every_generator(self):
        qs = generators.build(random.Random(5), 20)
        self.assertEqual(len({q["id"] for q in qs}), len(qs))
        self.assertEqual({q["meta"]["gen"] for q in qs}, set(generators.GENERATORS))

    def test_answer_positions_are_spread(self):
        qs = generators.build(random.Random(6), 40)
        positions = [q["answer"] for q in qs if len(q["options"]) == 4]
        for p in range(4):
            self.assertGreater(positions.count(p), len(positions) * 0.1)

    def test_table_questions_share_group_and_passage(self):
        qs = generators.generate(random.Random(1), "table_group")
        self.assertGreaterEqual(len(qs), 2)
        self.assertEqual(len({q["group"] for q in qs}), 1)
        self.assertEqual(len({q["passage"] for q in qs}), 1)


if __name__ == "__main__":
    unittest.main()

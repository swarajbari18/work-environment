"""Parametric question generators: unlimited fresh questions whose answers are computed, not looked up.

Every generator takes a random.Random and returns a list of questions (a passage group returns several). Each question
carries meta = {"gen": name, "params": {...}} so tests can recompute the answer by an independent method.
Conventions follow Indian aptitude papers (CSAT style): in syllogisms the terms are assumed non-empty, so
"All A are B" implies "Some A are B"; in kinship, a father's wife is the mother.
"""
import datetime as dt
import hashlib
import itertools
import re
from fractions import Fraction

import questions

MAX_TRIES = 25


# ---------------------------------------------------------------------------------------------------------------
# formatting and distractors
# ---------------------------------------------------------------------------------------------------------------
def fmt_num(x):
    f = Fraction(x)
    if f.denominator == 1:
        return str(f.numerator)
    text = f"{float(f):.2f}".rstrip("0").rstrip(".")
    return text


def fmt_pct(x):
    return fmt_num(x) + "%"


def fmt_signed_pct(x):
    f = Fraction(x)
    return ("+" if f > 0 else "") + fmt_num(f) + "%"


def fmt_frac(x):
    f = Fraction(x)
    return str(f.numerator) if f.denominator == 1 else f"{f.numerator}/{f.denominator}"


def options_around(rng, correct, wrongs, step, fmt=fmt_num):
    """Four options: the correct value plus three distinct distractors. Returns (strings, answer_index)."""
    correct = Fraction(correct)
    pool = []
    for w in wrongs:
        w = Fraction(w)
        if w != correct and w not in pool:
            pool.append(w)
    rng.shuffle(pool)
    chosen = pool[:3]
    k = 1
    step = Fraction(step)
    while len(chosen) < 3:
        for cand in (correct + k * step, correct - k * step):
            if cand != correct and cand not in chosen and len(chosen) < 3:
                chosen.append(cand)
        k += 1
    values = chosen + [correct]
    rng.shuffle(values)
    return [fmt(v) for v in values], values.index(correct)


def options_from_text(rng, correct, wrongs):
    pool = [w for i, w in enumerate(wrongs) if w != correct and w not in wrongs[:i]]
    rng.shuffle(pool)
    values = pool[:3] + [correct]
    rng.shuffle(values)
    return values, values.index(correct)


def _q(name, category, stem, options, answer, explanation, params, **kw):
    return questions.make(f"gen:{name}", category, stem, options, answer, explanation,
                          meta={"gen": name, "params": params}, **kw)


# ---------------------------------------------------------------------------------------------------------------
# numeracy
# ---------------------------------------------------------------------------------------------------------------
def percent_change(rng):
    kind = rng.choice(["rise", "fall", "marked_price"])
    pct = rng.choice([5, 10, 15, 20, 25, 30, 40, 50, 60, 75])
    base = 20 * rng.randint(5, 100)
    if kind == "marked_price":
        marked = base
        sp = marked * (100 - pct) // 100
        stem = (f"After a discount of {pct}%, a shirt is sold for Rs {sp}. "
                f"What was its marked price (in Rs)?")
        wrongs = [Fraction(sp * (100 + pct), 100), sp + pct, Fraction(sp * 100, 100 - pct) + pct, marked + 20]
        opts, ans = options_around(rng, marked, wrongs, 20)
        expl = f"Marked price x: x * (100 - {pct})/100 = {sp}, so x = {sp} * 100/{100 - pct} = {marked}."
        params = {"kind": kind, "pct": pct, "marked": marked, "sp": sp}
    else:
        old = base
        delta = old * pct // 100
        new = old + delta if kind == "rise" else old - delta
        word = "increase" if kind == "rise" else "decrease"
        stem = f"The price of an article changed from Rs {old} to Rs {new}. What is the percentage {word}?"
        wrongs = [pct + 5, pct - 5 if pct > 5 else pct + 10, Fraction(delta * 100, new), pct * 2 if pct < 40 else pct // 2]
        opts, ans = options_around(rng, pct, wrongs, 5, fmt_pct)
        expl = f"Change = {delta}. Percentage {word} = {delta}/{old} * 100 = {pct}% (taken on the original price)."
        params = {"kind": kind, "pct": pct, "old": old, "new": new}
    return [_q("percent_change", "numeracy", stem, opts, ans, expl, params)]


def successive_change(rng):
    a, b = rng.sample([10, 15, 20, 25, 30, 40, 50], 2)
    kind = rng.choice(["up_up", "up_down", "down_down"])
    first = {"up_up": 1, "up_down": 1, "down_down": -1}[kind]
    second = {"up_up": 1, "up_down": -1, "down_down": -1}[kind]
    factor = (1 + Fraction(first * a, 100)) * (1 + Fraction(second * b, 100))
    net = (factor - 1) * 100
    verb = lambda s: "increased" if s > 0 else "decreased"
    stem = (f"A quantity is first {verb(first)} by {a}% and then {verb(second)} by {b}%. "
            f"What is the net percentage change?")
    naive = first * a + second * b
    wrongs = [naive, net + 5, -net if net else 5, naive + 5]
    opts, ans = options_around(rng, net, wrongs, 5, fmt_signed_pct)
    expl = (f"Net factor = (1 {'+' if first > 0 else '-'} {a}/100)(1 {'+' if second > 0 else '-'} {b}/100) "
            f"= {fmt_num(factor)}, so the change is {fmt_signed_pct(net)}.")
    return [_q("successive_change", "numeracy", stem, opts, ans, expl, {"a": a, "b": b, "kind": kind})]


def ratio_share(rng):
    parts = [rng.randint(1, 9) for _ in range(3)]
    total_unit = sum(parts)
    total = total_unit * 10 * rng.randint(1, 30)
    who = rng.choice([0, 1, 2])
    name = "ABC"[who]
    share = Fraction(total * parts[who], total_unit)
    stem = (f"Rs {total} is divided among A, B and C in the ratio {parts[0]}:{parts[1]}:{parts[2]}. "
            f"What is {name}'s share (in Rs)?")
    other = parts[(who + 1) % 3]
    wrongs = [Fraction(total, 3), Fraction(total * other, total_unit), Fraction(total * parts[who], total_unit + 1),
              share + total // 10]
    opts, ans = options_around(rng, share, wrongs, max(10, total // 20))
    expl = f"Total parts = {total_unit}. One part = {total}/{total_unit}. {name}'s share = {parts[who]} parts = {fmt_num(share)}."
    return [_q("ratio_share", "numeracy", stem, opts, ans, expl, {"total": total, "parts": parts, "who": who})]


def average_speed(rng):
    for _ in range(200):
        v1, v2 = rng.randrange(20, 100, 5), rng.randrange(20, 100, 5)
        if v1 != v2 and (2 * v1 * v2) % (v1 + v2) == 0:
            break
    else:
        v1, v2 = 40, 60
    avg = Fraction(2 * v1 * v2, v1 + v2)
    dist = rng.choice([60, 90, 120, 180, 240])
    stem = (f"A car travels {dist} km at {v1} km/h and returns the same {dist} km at {v2} km/h. "
            f"What is its average speed for the whole journey (km/h)?")
    wrongs = [Fraction(v1 + v2, 2), avg + 4, avg - 4, Fraction(v1 * v2, v1 + v2)]
    opts, ans = options_around(rng, avg, wrongs, 3)
    expl = (f"Total distance {2 * dist} km. Time = {dist}/{v1} + {dist}/{v2}. "
            f"Average = 2*{v1}*{v2}/({v1}+{v2}) = {fmt_num(avg)} km/h (not the simple mean).")
    return [_q("average_speed", "numeracy", stem, opts, ans, expl, {"v1": v1, "v2": v2, "dist": dist})]


def train_cross(rng):
    kmph = rng.choice([36, 54, 72, 90, 108])
    ms = kmph * 5 // 18
    t = rng.randint(3, 20)
    length = ms * t
    stem = f"A {length} m long train runs at {kmph} km/h. How many seconds does it take to cross a pole?"
    wrongs = [Fraction(length, kmph), Fraction(length * 18, kmph * 5 * 2), t + 2, t * 2 if t > 5 else t + 4]
    opts, ans = options_around(rng, t, wrongs, 2)
    expl = f"{kmph} km/h = {kmph} * 5/18 = {ms} m/s. Time = {length}/{ms} = {t} s."
    return [_q("train_cross", "numeracy", stem, opts, ans, expl, {"kmph": kmph, "length": length})]


def work_rate(rng):
    for _ in range(500):
        a, b = rng.randint(4, 60), rng.randint(4, 60)
        if a != b and (a * b) % (a + b) == 0:
            break
    else:
        a, b = 10, 15
    together = Fraction(a * b, a + b)
    stem = f"A can finish a job in {a} days and B can finish it in {b} days. Working together, in how many days do they finish it?"
    wrongs = [Fraction(a + b, 2), together + 2, together - 1 if together > 2 else together + 3, abs(a - b) or 1]
    opts, ans = options_around(rng, together, wrongs, 2)
    expl = f"Daily work = 1/{a} + 1/{b} = {a + b}/{a * b}. Days = {a * b}/{a + b} = {fmt_num(together)}."
    return [_q("work_rate", "numeracy", stem, opts, ans, expl, {"a": a, "b": b})]


def age_problem(rng):
    k = rng.randint(2, 5)
    n = rng.randint(2, 8)
    b = rng.randint(n + 2, n + 20)
    a = k * (b - n) + n
    s = a + b
    stem = (f"The sum of the present ages of A and B is {s} years. {n} years ago, A was {k} times as old as B. "
            f"What is A's present age (in years)?")
    wrongs = [b, a + n, a - n, k * b]
    opts, ans = options_around(rng, a, wrongs, 2)
    expl = (f"{n} years ago: A - {n} = {k}(B - {n}) and A + B = {s}. Solving gives B = {b}, so A = {a}.")
    return [_q("age_problem", "numeracy", stem, opts, ans, expl, {"k": k, "n": n, "b": b, "a": a, "s": s})]


def profit_loss(rng):
    x = rng.choice([20, 25, 30, 40, 50, 60])
    y = rng.choice([5, 10, 15, 20, 25])
    profit = (100 + Fraction(x)) * (100 - Fraction(y)) / 100 - 100
    stem = (f"A shopkeeper marks his goods {x}% above cost price and allows a discount of {y}% on the marked price. "
            f"What is his profit or loss percentage?")
    word = "profit" if profit > 0 else "loss"
    wrongs = [x - y, profit + 5, abs(profit) + 2, Fraction(x * y, 100) + x - y]
    opts, ans = options_around(rng, profit, wrongs, 2, lambda v: fmt_pct(abs(v)) + (" profit" if v > 0 else " loss"))
    expl = f"Take cost = 100. Marked = {100 + x}. Selling = {100 + x} * (100 - {y})/100 = {fmt_num(100 + profit)}. So {fmt_num(abs(profit))}% {word}."
    return [_q("profit_loss", "numeracy", stem, opts, ans, expl, {"x": x, "y": y})]


def average_removed(rng):
    n = rng.randint(5, 9)
    for _ in range(200):
        a = rng.randint(20, 90)
        b = a + rng.randint(-8, 8)
        removed = n * a - (n - 1) * b
        if b != a and removed > 0:
            break
    else:
        a, b = 40, 36
        removed = n * a - (n - 1) * b
    stem = (f"The average of {n} numbers is {a}. When one number is removed, the average of the remaining "
            f"{n - 1} numbers becomes {b}. What is the number that was removed?")
    wrongs = [removed + (a - b), removed - (a - b), a, n * (a - b) + b]
    opts, ans = options_around(rng, removed, wrongs, 3)
    expl = f"Sum before = {n}*{a} = {n * a}. Sum after = {n - 1}*{b} = {(n - 1) * b}. Removed = {removed}."
    return [_q("average_removed", "numeracy", stem, opts, ans, expl, {"n": n, "a": a, "b": b})]


def calendar_day(rng):
    start = dt.date(2020, 1, 1)
    d1 = start + dt.timedelta(days=rng.randint(0, 3650))
    delta = rng.randint(2, 400)
    d2 = d1 + dt.timedelta(days=delta)
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    correct = days[d2.weekday()]
    stem = (f"If {d1:%d %B %Y} is a {days[d1.weekday()]}, what day of the week is {d2:%d %B %Y}?")
    opts, ans = options_from_text(rng, correct, [d for d in days if d != correct])
    expl = f"The two dates are {delta} days apart. {delta} mod 7 = {delta % 7}, so move {delta % 7} day(s) forward from {days[d1.weekday()]}."
    return [_q("calendar_day", "numeracy", stem, opts, ans, expl, {"d1": d1.isoformat(), "d2": d2.isoformat()})]


def clock_angle(rng):
    h = rng.randint(1, 12)
    m = rng.randrange(0, 60, 2)
    a = abs(30 * (h % 12) + Fraction(m, 2) - 6 * m)
    a = min(a, 360 - a)
    stem = f"What is the smaller angle between the hour and minute hands of a clock at {h}:{m:02d}?"
    wrongs = [abs(30 * h - 6 * m) % 180, 360 - a, a + 15, abs(a - 15)]
    opts, ans = options_around(rng, a, wrongs, 10, lambda v: fmt_num(v) + "°")
    expl = f"Hour hand: 30*{h % 12} + {m}/2 degrees. Minute hand: 6*{m} degrees. Difference = {fmt_num(a)}°."
    return [_q("clock_angle", "numeracy", stem, opts, ans, expl, {"h": h, "m": m})]


def probability(rng):
    if rng.random() < 0.5:
        k = rng.randint(3, 11)
        ways = 6 - abs(k - 7)
        p = Fraction(ways, 36)
        stem = f"Two fair dice are thrown together. What is the probability that the sum of the numbers is {k}?"
        wrongs = [Fraction(ways + 1, 36), Fraction(ways, 12), Fraction(max(ways - 1, 1), 36), Fraction(1, k)]
        params = {"kind": "dice", "k": k}
        expl = f"There are {ways} ways to get a sum of {k} out of 36 equally likely outcomes: {fmt_frac(p)}."
    else:
        r, b, g = rng.randint(2, 9), rng.randint(2, 9), rng.randint(1, 8)
        colour, n_c = rng.choice([("red", r), ("blue", b), ("green", g)])
        total = r + b + g
        p = Fraction(total - n_c, total)
        stem = (f"A bag contains {r} red, {b} blue and {g} green balls. One ball is drawn at random. "
                f"What is the probability that it is not {colour}?")
        wrongs = [Fraction(n_c, total), Fraction(total - n_c, total + 1), Fraction(total - n_c, total - 1), Fraction(1, 2)]
        params = {"kind": "balls", "r": r, "b": b, "g": g, "colour": colour}
        expl = f"{total - n_c} of the {total} balls are not {colour}: {fmt_frac(p)}."
    opts, ans = options_around(rng, p, wrongs, Fraction(1, 36), fmt_frac)
    return [_q("probability", "numeracy", stem, opts, ans, expl, params)]


# ---------------------------------------------------------------------------------------------------------------
# logical
# ---------------------------------------------------------------------------------------------------------------
def number_series(rng):
    kind = rng.choice(["arithmetic", "geometric", "squares", "alt_add", "growing_gap", "fibonacci", "double_add"])
    if kind == "arithmetic":
        a, d = rng.randint(-5, 30), rng.choice([3, 4, 5, 6, 7, 8, 9, 11, 13])
        terms = [a + d * i for i in range(7)]
        params = {"kind": kind, "a": a, "d": d}
        rule = f"add {d} each time"
    elif kind == "geometric":
        a, r = rng.randint(1, 6), rng.choice([2, 3])
        terms = [a * r ** i for i in range(6)]
        params = {"kind": kind, "a": a, "r": r}
        rule = f"multiply by {r} each time"
    elif kind == "squares":
        c, s = rng.randint(-3, 9), rng.randint(1, 4)
        terms = [(s + i) ** 2 + c for i in range(6)]
        params = {"kind": kind, "c": c, "s": s}
        rule = f"terms are n^2 {'+' if c >= 0 else '-'} {abs(c)} for n = {s}, {s + 1}, ..."
    elif kind == "alt_add":
        a, p, q = rng.randint(1, 20), rng.randint(5, 14), rng.randint(1, 4)
        terms = [a]
        for i in range(6):
            terms.append(terms[-1] + (p if i % 2 == 0 else -q))
        params = {"kind": kind, "a": a, "p": p, "q": q}
        rule = f"alternately add {p} and subtract {q}"
    elif kind == "growing_gap":
        a, d, e = rng.randint(1, 15), rng.randint(1, 5), rng.randint(1, 4)
        terms = [a]
        for i in range(6):
            terms.append(terms[-1] + d + e * i)
        params = {"kind": kind, "a": a, "d": d, "e": e}
        rule = f"the gaps are {d}, {d + e}, {d + 2 * e}, ... (increasing by {e})"
    elif kind == "fibonacci":
        a, b = rng.randint(1, 6), rng.randint(1, 8)
        terms = [a, b]
        while len(terms) < 7:
            terms.append(terms[-1] + terms[-2])
        params = {"kind": kind, "a": a, "b": b}
        rule = "each term is the sum of the previous two"
    else:
        a, k = rng.randint(1, 5), rng.choice([-2, -1, 1, 2, 3])
        terms = [a]
        for _ in range(5):
            terms.append(2 * terms[-1] + k)
        params = {"kind": kind, "a": a, "k": k}
        rule = f"double the previous term and {'add' if k > 0 else 'subtract'} {abs(k)}"
    shown, nxt = terms[:-1], terms[-1]
    last_gap = abs(shown[-1] - shown[-2]) or 1
    wrongs = [nxt + 1, nxt - 1, nxt + last_gap, nxt - last_gap, shown[-1] + last_gap, nxt + 2]
    opts, ans = options_around(rng, nxt, wrongs, max(1, last_gap // 2))
    stem = "Find the next number in the series: " + ", ".join(str(t) for t in shown) + ", ?"
    expl = f"Rule: {rule}. The next term is {nxt}."
    params["terms"] = terms
    return [_q("number_series", "logical", stem, opts, ans, expl, params)]


_NOUNS = ["pens", "books", "tables", "chairs", "doctors", "teachers", "engineers", "artists", "mangoes", "apples",
          "roses", "tulips", "birds", "cats", "dogs", "fish", "clocks", "lamps", "bridges", "rivers"]
_SYLLOGISM_OPTIONS = ["Only conclusion I follows", "Only conclusion II follows", "Either I or II follows",
                      "Neither I nor II follows", "Both I and II follow"]


def _sentence(kind, p, q):
    if kind == "all":
        return f"All {p} are {q}."
    if kind == "no":
        return f"No {p} are {q}."
    if kind == "some":
        return f"Some {p} are {q}."
    return f"Some {p} are not {q}."


def syllogism_models(premises, terms):
    """All models (sets of populated Venn regions, bit i of the region = membership of term i) of the premises
    in which every term is non-empty. Statement = (kind, p_index, q_index)."""
    regions = range(1, 8)

    def holds(stmt, populated):
        kind, p, q = stmt
        for r in populated:
            in_p, in_q = bool(r >> p & 1), bool(r >> q & 1)
            if kind == "all" and in_p and not in_q:
                return False
            if kind == "no" and in_p and in_q:
                return False
        if kind == "some":
            return any((r >> p & 1) and (r >> q & 1) for r in populated)
        if kind == "some_not":
            return any((r >> p & 1) and not (r >> q & 1) for r in populated)
        return True

    models = []
    for mask in range(1, 128):
        populated = [r for r in regions if mask >> (r - 1) & 1]
        if not all(any(r >> t & 1 for r in populated) for t in range(terms)):
            continue
        if all(holds(s, populated) for s in premises):
            models.append(populated)
    return models, holds


def syllogism_verdict(premises, c1, c2):
    models, holds = syllogism_models(premises, 3)
    f1 = all(holds(c1, m) for m in models)
    f2 = all(holds(c2, m) for m in models)
    if f1 and f2:
        return 4
    if f1:
        return 0
    if f2:
        return 1
    if all(holds(c1, m) or holds(c2, m) for m in models):
        return 2
    return 3


def syllogism(rng):
    for _ in range(100):
        terms = rng.sample(_NOUNS, 3)             # indices 0, 1, 2; index 1 is the middle term
        kinds = rng.choices(["all", "no", "some", "some_not"], weights=[4, 2, 4, 1], k=2)
        p1 = (kinds[0],) + tuple(rng.sample([0, 1], 2))
        p2 = (kinds[1],) + tuple(rng.sample([1, 2], 2))
        premises = [p1, p2]
        ck = rng.sample(["all", "no", "some", "some_not"], 2)
        c1 = (ck[0],) + tuple(rng.sample([0, 2], 2))
        c2 = (ck[1],) + tuple(rng.sample([0, 2], 2))
        if c1 == c2:
            continue
        models, _ = syllogism_models(premises, 3)
        if not models:
            continue
        answer = syllogism_verdict(premises, c1, c2)
        break
    else:
        return syllogism(rng)
    stem = ("Statements:\n"
            f"  1. {_sentence(p1[0], terms[p1[1]], terms[p1[2]])}\n"
            f"  2. {_sentence(p2[0], terms[p2[1]], terms[p2[2]])}\n"
            "Conclusions:\n"
            f"  I. {_sentence(c1[0], terms[c1[1]], terms[c1[2]])}\n"
            f"  II. {_sentence(c2[0], terms[c2[1]], terms[c2[2]])}\n"
            "Which of the following follows?")
    expl = ("A conclusion follows only if it is true in every situation consistent with both statements "
            "(each group is taken to have at least one member). Answer: " + _SYLLOGISM_OPTIONS[answer] + ".")
    params = {"terms": terms, "premises": [list(p1), list(p2)], "conclusions": [list(c1), list(c2)]}
    return [_q("syllogism", "logical", stem, _SYLLOGISM_OPTIONS, answer, expl, params)]


_WORDS = ["PLAN", "STUDY", "FOCUS", "BRAIN", "WORLD", "LOGIC", "POINT", "MATHS", "VAULT", "LEARN", "SOLVE",
          "TABLE", "CLOCK", "RIVER", "SKILL", "THINK", "DRIVE", "WRITE", "MIND", "GOAL"]


def _shift(word, k):
    return "".join(chr((ord(c) - 65 + k) % 26 + 65) for c in word)


def letter_code(rng):
    k = rng.choice([-3, -2, -1, 1, 2, 3, 4, 5])
    ex, target = rng.sample(_WORDS, 2)
    correct = _shift(target, k)
    wrongs = [_shift(target, k + 1), _shift(target, k - 1), _shift(target, -k), correct[::-1]]
    opts, ans = options_from_text(rng, correct, [w for w in wrongs if w != correct])
    stem = (f"In a certain code language, {ex} is written as {_shift(ex, k)}. "
            f"How is {target} written in that code?")
    expl = f"Each letter moves {abs(k)} place(s) {'forward' if k > 0 else 'backward'}. {target} becomes {correct}."
    return [_q("letter_code", "logical", stem, opts, ans, expl, {"k": k, "example": ex, "target": target})]


_COMPASS = {(0, 1): "North", (1, 1): "North-East", (1, 0): "East", (1, -1): "South-East", (0, -1): "South",
            (-1, -1): "South-West", (-1, 0): "West", (-1, 1): "North-West"}


def direction(rng):
    triples = [(3, 4), (6, 8), (5, 12), (8, 15), (9, 12), (12, 5), (4, 3), (15, 8)]
    if rng.random() < 0.5:
        dx, dy = rng.choice(triples)
        c, d = rng.randint(1, 9), rng.randint(1, 9)
        a, b = dy + c, dx + d
        dist = Fraction(round((dx * dx + dy * dy) ** 0.5))
        stem = (f"A man walks {a} km North, then {b} km East, then {c} km South, then {d} km West. "
                f"How far is he from his starting point (in km)?")
        wrongs = [Fraction(a + b + c + d), Fraction(abs(a - c) + abs(b - d)), dist + 2, dist - 1]
        opts, ans = options_around(rng, dist, wrongs, 1)
        expl = f"Net North = {a} - {c} = {dy}, net East = {b} - {d} = {dx}. Distance = sqrt({dx}^2 + {dy}^2) = {dist}."
        params = {"kind": "distance", "legs": [a, b, c, d]}
    else:
        # Net displacement is an exact compass point (an axis or an exact diagonal), so the answer is unambiguous.
        k = rng.randint(1, 8)
        dx, dy = rng.choice([(0, 1), (1, 0), (0, -1), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1)])
        dx, dy = dx * k, dy * k

        def split(net):
            """Two positive legs p, q with p - q = net."""
            q = rng.randint(1, 8) + max(0, -net)
            return q + net, q

        a, c = split(dy)
        b, d = split(dx)
        sign = lambda v: (v > 0) - (v < 0)
        correct = _COMPASS[(sign(dx), sign(dy))]
        stem = (f"A man walks {a} km North, then {b} km East, then {c} km South, then {d} km West. "
                f"In which direction is he from his starting point?")
        pool = [v for v in _COMPASS.values() if v != correct]
        opts, ans = options_from_text(rng, correct, pool)
        expl = f"Net North-South = {dy:+d}, net East-West = {dx:+d}, so he is to the {correct} of the start."
        params = {"kind": "bearing", "legs": [a, b, c, d]}
    return [_q("direction", "logical", stem, opts, ans, expl, params)]


_KIN_SWAP = {"father": "mother", "brother": "sister", "son": "daughter", "uncle": "aunt", "wife": "husband",
             "daughter-in-law": "son-in-law", "father-in-law": "mother-in-law", "nephew": "niece",
             "grandfather": "grandmother", "grandson": "granddaughter"}
_KIN_SWAP.update({v: k for k, v in list(_KIN_SWAP.items())})
MALE_NAMES = ["Arjun", "Rohan", "Vikram", "Sameer", "Nikhil", "Aarav", "Kabir", "Dev", "Rahul", "Karan"]
FEMALE_NAMES = ["Meera", "Kavya", "Anika", "Tara", "Isha", "Riya", "Sneha", "Neha", "Priya", "Divya"]
MALE_RELATIONS = frozenset(r for r in ("father", "brother", "son", "uncle", "husband", "nephew", "grandfather",
                                       "grandson", "son-in-law", "father-in-law", "cousin-brother"))
FEMALE_RELATIONS = frozenset(r for r in ("mother", "sister", "daughter", "aunt", "wife", "niece", "grandmother",
                                         "granddaughter", "daughter-in-law", "mother-in-law"))
# (statements, (ask_x, ask_y), answer): X, Y, Z are placeholders for people.
_KIN_TEMPLATES = [
    (["A is the father of B.", "C is the brother of B."], ("C", "A"), "son"),
    (["A is the mother of B.", "B is the father of C."], ("A", "C"), "grandmother"),
    (["A is the brother of B.", "B is the mother of C."], ("A", "C"), "uncle"),
    (["A is the sister of B.", "B is the father of C."], ("A", "C"), "aunt"),
    (["A is the son of B.", "B is the sister of C."], ("A", "C"), "nephew"),
    (["A is the wife of B.", "B is the son of C."], ("A", "C"), "daughter-in-law"),
    (["A is the father of B.", "B is the wife of C."], ("A", "C"), "father-in-law"),
    (["A is the son of B.", "B is the brother of C.", "C is the father of D."], ("A", "D"), "cousin"),
]
_KIN_POOL = ["son", "daughter", "uncle", "aunt", "nephew", "niece", "cousin", "brother", "sister", "grandfather",
             "grandmother", "father-in-law", "mother-in-law", "daughter-in-law", "son-in-law", "father", "mother"]


def _swap_words(text):
    words = text.replace(".", " . ").split()
    return " ".join(_KIN_SWAP.get(w, w) for w in words).replace(" .", ".")


def blood_relation(rng):
    statements, (ask_x, ask_y), answer = rng.choice(_KIN_TEMPLATES)
    swap = rng.random() < 0.5
    if swap:
        statements = [_swap_words(s) for s in statements]
        answer = _KIN_SWAP.get(answer, answer)
    # A person described as a father or a sister has a fixed sex; names must agree with that.
    sex = {}
    for statement in statements:
        who, rel = re.match(r"(\w) is the ([\w-]+) of", statement).groups()
        sex[who] = "m" if rel in MALE_RELATIONS else "f"
    male, female = rng.sample(MALE_NAMES, 4), rng.sample(FEMALE_NAMES, 4)
    people = {}
    for slot, letter in enumerate("ABCD"):
        pool = female if sex.get(letter) == "f" else male if sex.get(letter) == "m" else (male, female)[rng.randrange(2)]
        people[letter] = pool[slot]

    def sub(sentence):
        # Replace whole placeholder words only ("A", "B." ...), never letters inside other words.
        return " ".join(people.get(w.rstrip("."), w.rstrip(".")) + ("." if w.endswith(".") else "")
                        for w in sentence.split())

    stmts = [sub(s) for s in statements]
    opts, ans = options_from_text(rng, answer, [t for t in _KIN_POOL if t != answer])
    stem = ("Read the statements and answer.\n  " + "\n  ".join(stmts) +
            f"\nHow is {people[ask_x]} related to {people[ask_y]}?")
    opts = [o.capitalize() for o in opts]
    expl = f"Following the chain of statements, {people[ask_x]} is {people[ask_y]}'s {answer}."
    params = {"statements": statements, "ask": [ask_x, ask_y], "answer": answer, "swap": swap}
    return [_q("blood_relation", "logical", stem, opts, ans, expl, params)]


# ---------------------------------------------------------------------------------------------------------------
# data interpretation (a table with three questions, drawn together)
# ---------------------------------------------------------------------------------------------------------------
_COMPANIES = ["Alpha", "Beta", "Gamma", "Delta"]
_YEARS = [2022, 2023, 2024]


def table_group(rng):
    table = {}
    for year in _YEARS:
        total = rng.choice([100, 200, 500])
        cuts = sorted(rng.sample(range(total // 20, total - total // 20, max(1, total // 20)), 3))
        parts = [cuts[0], cuts[1] - cuts[0], cuts[2] - cuts[1], total - cuts[2]]
        table[year] = dict(zip(_COMPANIES, parts))
    rows = [f"{'Year':<6}" + "".join(f"{c:>8}" for c in _COMPANIES)]
    rows += [f"{y:<6}" + "".join(f"{table[y][c]:>8}" for c in _COMPANIES) for y in _YEARS]
    passage = "Production of four companies (in thousand units):\n" + "\n".join(rows)
    group = "tbl-" + hashlib.sha1(passage.encode()).hexdigest()[:12]
    out = []

    year, comp = rng.choice(_YEARS), rng.choice(_COMPANIES)
    total = sum(table[year].values())
    share = Fraction(table[year][comp] * 100, total)
    wrongs = [share + 5, share - 5 if share > 5 else share + 10, Fraction(table[year][comp], 10), share * 2 if share < 30 else share / 2]
    opts, ans = options_around(rng, share, wrongs, 5, fmt_pct)
    out.append(_q("table_group", "data_interp",
                  f"What percentage of the total production in {year} was produced by {comp}?", opts, ans,
                  f"{comp} produced {table[year][comp]} out of {total}: {fmt_num(share)}%.",
                  {"table": {str(y): v for y, v in table.items()}, "q": "share", "year": year, "comp": comp},
                  group=group, passage=passage))

    comp = rng.choice(_COMPANIES)
    y1, y2 = rng.sample(_YEARS, 2)
    diff = table[y2][comp] - table[y1][comp]
    wrongs = [-diff, abs(diff) + 10, abs(diff) - 5, table[y2][comp] + table[y1][comp]]
    opts, ans = options_around(rng, diff, wrongs, 10, lambda v: ("+" if v > 0 else "") + fmt_num(v))
    out.append(_q("table_group", "data_interp",
                  f"By how many thousand units did {comp}'s production change from {y1} to {y2}? (a negative number is a fall)",
                  opts, ans, f"{table[y2][comp]} - {table[y1][comp]} = {diff:+d}.",
                  {"table": {str(y): v for y, v in table.items()}, "q": "change", "comp": comp, "y1": y1, "y2": y2},
                  group=group, passage=passage))

    year = rng.choice(_YEARS)
    row = table[year]
    best = max(row.values())
    if list(row.values()).count(best) == 1:
        top = max(row, key=row.get)
        opts, ans = options_from_text(rng, top, [c for c in _COMPANIES if c != top])
        out.append(_q("table_group", "data_interp", f"Which company produced the most in {year}?", opts, ans,
                      f"{top} produced {best}, the highest of the four in {year}.",
                      {"table": {str(y): v for y, v in table.items()}, "q": "top", "year": year},
                      group=group, passage=passage))
    return out


GENERATORS = {
    "percent_change": percent_change,
    "successive_change": successive_change,
    "ratio_share": ratio_share,
    "average_speed": average_speed,
    "train_cross": train_cross,
    "work_rate": work_rate,
    "age_problem": age_problem,
    "profit_loss": profit_loss,
    "average_removed": average_removed,
    "calendar_day": calendar_day,
    "clock_angle": clock_angle,
    "probability": probability,
    "number_series": number_series,
    "syllogism": syllogism,
    "letter_code": letter_code,
    "direction": direction,
    "blood_relation": blood_relation,
    "table_group": table_group,
}


def generate(rng, name):
    """One draw from generator `name`. Retries until every question passes schema validation."""
    for _ in range(MAX_TRIES):
        qs = GENERATORS[name](rng)
        if qs and all(not questions.validate(q) for q in qs):
            return qs
    raise RuntimeError(f"generator {name} kept producing invalid questions")


def build(rng, per_generator):
    """A deduplicated list of questions, about `per_generator` from each generator."""
    seen, out = set(), []
    for name in GENERATORS:
        made = 0
        attempts = 0
        while made < per_generator and attempts < per_generator * 20:
            attempts += 1
            for q in generate(rng, name):
                if q["id"] not in seen:
                    seen.add(q["id"])
                    out.append(q)
                    made += 1
    return out

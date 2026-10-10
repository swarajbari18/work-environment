#!/usr/bin/env python3
"""Turn a downloaded public dataset into vault bank JSONL.

  import_dataset.py KIND --in FILE [FILE ...] --out FILE.jsonl [--limit N] [--seed N]

KIND is one of: aqua_rat, arc, mmlu, sciq, logiqa2, race. Parquet inputs need pyarrow (build time only; the running
vault reads plain JSONL). Downloaded files are untrusted data: put each dataset in its own directory and run this
with `python3 -I` so nothing next to the data can be imported.

Rows that need a picture, a table or a figure we do not have are dropped, as are rows whose own rationale
contradicts their answer key (AQuA-RAT is noisy).
"""
import argparse
import hashlib
import json
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import questions  # noqa: E402

NEEDS_PICTURE = re.compile(
    r"\b(figure|diagram|picture|image|graph|chart|map|illustration|photograph|table below|shown below|shown above|"
    r"given below|given above|the following table|data table)\b", re.IGNORECASE)
LETTERS = "ABCDE"
MMLU_CATEGORY = {
    "high_school_mathematics": "hs_math", "elementary_mathematics": "hs_math", "high_school_statistics": "hs_math",
    "high_school_physics": "hs_science", "high_school_chemistry": "hs_science", "high_school_biology": "hs_science",
    "conceptual_physics": "hs_science", "astronomy": "hs_science",
}


def _clean(text):
    return re.sub(r"[ \t]+", " ", str(text)).strip()


def _seeded_order(key, n):
    order = list(range(n))
    random.Random(hashlib.sha1(key.encode()).hexdigest()).shuffle(order)
    return order


def from_aqua_rat(row):
    stem = _clean(row.get("question", ""))
    raw = [_clean(o) for o in row.get("options", [])]
    options = [re.sub(r"^[A-E]\s*[).:]\s*", "", o) for o in raw]
    correct = str(row.get("correct", "")).strip().upper()
    rationale = str(row.get("rationale", "")).strip()
    if len(options) != 5 or correct not in LETTERS or not rationale or len(stem) > 450 or NEEDS_PICTURE.search(stem):
        return None
    stated = re.findall(r"answer\s*(?:is|:|=)?\s*\(?([A-E])\)?\b", rationale, re.IGNORECASE)
    if stated and stated[-1].upper() != correct:
        return None
    if any(not o for o in options) or len(set(options)) != 5:
        return None
    rationale = re.sub(r"\n+", " ", rationale)
    if len(rationale) > 700:
        rationale = rationale[:700].rsplit(" ", 1)[0] + " ..."
    return questions.make("aqua_rat", "numeracy", stem, options, LETTERS.index(correct), rationale,
                          license="Apache-2.0")


def from_arc(row):
    stem = _clean(row.get("question", ""))
    choices = row.get("choices") or {}
    texts, labels = [_clean(t) for t in choices.get("text", [])], [str(l) for l in choices.get("label", [])]
    key = str(row.get("answerKey", ""))
    if key not in labels or len(texts) < 3 or NEEDS_PICTURE.search(stem) or len(set(texts)) != len(texts):
        return None
    return questions.make("arc", "hs_science", stem, texts, labels.index(key), "", license="CC-BY-SA-4.0")


def from_mmlu(row):
    category = MMLU_CATEGORY.get(row.get("subject", ""))
    stem = _clean(row.get("question", ""))
    options = [_clean(c) for c in row.get("choices", [])]
    answer = row.get("answer")
    if not category or len(options) != 4 or not isinstance(answer, int) or NEEDS_PICTURE.search(stem):
        return None
    if len(set(options)) != 4 or not all(options):
        return None
    return questions.make(f"mmlu:{row['subject']}", category, stem, options, answer, "", license="MIT")


def from_sciq(row):
    stem = _clean(row.get("question", ""))
    correct = _clean(row.get("correct_answer", ""))
    wrong = [_clean(row.get(f"distractor{i}", "")) for i in (1, 2, 3)]
    options = [correct] + wrong
    if not stem or not all(options) or len(set(options)) != 4 or NEEDS_PICTURE.search(stem):
        return None
    order = _seeded_order(stem, 4)
    shuffled = [options[i] for i in order]
    support = _clean(row.get("support", ""))
    if len(support) > 350:
        support = support[:350].rsplit(" ", 1)[0] + " ..."
    return questions.make("sciq", "hs_science", stem, shuffled, shuffled.index(correct), support,
                          license="CC-BY-NC-3.0")


def from_logiqa2(row):
    passage = _clean(row.get("text", ""))
    stem = _clean(row.get("question", ""))
    options = [_clean(o) for o in row.get("options", [])]
    answer = row.get("answer")
    if len(options) != 4 or not isinstance(answer, int) or not 0 <= answer < 4 or not passage or not stem:
        return None
    if len(set(options)) != 4 or len(passage) > 1500 or NEEDS_PICTURE.search(passage + " " + stem):
        return None
    return questions.make("logiqa2", "logical", stem, options, answer, "", passage=passage,
                          license="CC-BY-NC-SA-4.0")


def from_race(rows):
    """RACE questions share an article, so they are grouped: one article's questions are drawn together."""
    by_article = {}
    for row in rows:
        by_article.setdefault(row.get("article", ""), []).append(row)
    out = []
    for article, items in by_article.items():
        article = re.sub(r"\s+", " ", article).strip()
        if not article or len(article) > 2200:
            continue
        group = "race-" + hashlib.sha1(article.encode()).hexdigest()[:12]
        made = []
        for row in items:
            stem = _clean(row.get("question", ""))
            options = [_clean(o) for o in row.get("options", [])]
            answer = LETTERS.find(str(row.get("answer", "")).strip().upper())
            if len(options) != 4 or answer < 0 or len(set(options)) != 4 or not stem or "_" in stem or NEEDS_PICTURE.search(stem):
                continue                                        # cloze ("_") items depend on a blank we do not show
            made.append(questions.make("race", "verbal", stem, options, answer, "", group=group, passage=article,
                                       license="RACE (non-commercial research use)"))
        if len(made) >= 2:                                      # keep only articles that still give a real group
            out.extend(made)
    return out


ROW_MAPPERS = {"aqua_rat": from_aqua_rat, "arc": from_arc, "mmlu": from_mmlu, "sciq": from_sciq,
               "logiqa2": from_logiqa2}


def read_rows(kind, path):
    if kind == "logiqa2":
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)
        return
    import pyarrow.parquet as pq
    table = pq.read_table(path)
    yield from table.to_pylist()


def convert(kind, paths, limit=None, seed=0):
    rows = [row for path in paths for row in read_rows(kind, path)]
    if kind == "race":
        made = from_race(rows)
        rng = random.Random(seed)
        if limit and len(made) > limit:
            groups = sorted({q["group"] for q in made})
            rng.shuffle(groups)
            keep, count = set(), 0
            for g in groups:
                size = sum(1 for q in made if q["group"] == g)
                if count + size > limit:
                    continue
                keep.add(g)
                count += size
            made = [q for q in made if q["group"] in keep]
        return made
    mapper = ROW_MAPPERS[kind]
    made, seen = [], set()
    for row in rows:
        q = mapper(row)
        if q and not questions.validate(q) and q["id"] not in seen:
            seen.add(q["id"])
            made.append(q)
    if limit and len(made) > limit:
        random.Random(seed).shuffle(made)
        made = made[:limit]
    return made


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("kind", choices=sorted([*ROW_MAPPERS, "race"]))
    parser.add_argument("--in", dest="inputs", nargs="+", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    made = convert(args.kind, args.inputs, args.limit, args.seed)
    questions.write_jsonl(args.out, made)
    print(f"{args.kind}: wrote {len(made)} questions to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

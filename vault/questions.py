"""Question schema, bank loading and set selection.

A question is a plain dict:
  id, source, category, stem, options (2-5 strings), answer (index), explanation,
  optional: group (passage group id), passage, license, meta (generator name and params).
"""
import hashlib
import json
import os
import random

CATEGORIES = ("numeracy", "logical", "verbal", "hs_math", "hs_science", "emotional", "data_interp")
# data_interp is 10% so a 30-question set has room for one 3-question table group.
QUOTAS = {"numeracy": 20, "logical": 25, "verbal": 15, "hs_math": 10, "hs_science": 10, "emotional": 10, "data_interp": 10}


class BankTooSmall(Exception):
    pass


def make_id(source, stem, correct):
    """Identity of a question: where it came from, what it asks, and the right answer. Not the distractors or
    their order, so the same question redrawn with other wrong options still counts as used."""
    digest = hashlib.sha1("\x1f".join([source, stem, correct]).encode("utf-8")).hexdigest()[:16]
    return f"{source.split(':')[0]}-{digest}"


def make(source, category, stem, options, answer, explanation="", *, group=None, passage=None, license="", meta=None):
    options = [str(o) for o in options]
    correct = options[answer] if isinstance(answer, int) and 0 <= answer < len(options) else ""
    q = {
        "id": make_id(source, (passage or "") + "\x1e" + stem, correct),
        "source": source,
        "category": category,
        "stem": stem,
        "options": options,
        "answer": answer,
        "explanation": explanation,
    }
    if group is not None:
        q["group"] = group
    if passage is not None:
        q["passage"] = passage
    if license:
        q["license"] = license
    if meta is not None:
        q["meta"] = meta
    return q


def validate(q):
    """Return a list of problems; empty means the question is usable."""
    errors = []
    for key in ("id", "source", "category", "stem", "options", "answer"):
        if key not in q:
            errors.append(f"missing {key}")
    if errors:
        return errors
    if q["category"] not in CATEGORIES:
        errors.append(f"unknown category {q['category']!r}")
    if not isinstance(q["stem"], str) or not q["stem"].strip():
        errors.append("empty stem")
    opts = q["options"]
    if not isinstance(opts, list) or not 2 <= len(opts) <= 5:
        errors.append("options must be a list of 2-5")
    else:
        if any(not isinstance(o, str) or not o.strip() for o in opts):
            errors.append("empty option")
        if len(set(opts)) != len(opts):
            errors.append("duplicate options")
        if not isinstance(q["answer"], int) or isinstance(q["answer"], bool) or not 0 <= q["answer"] < len(opts):
            errors.append("answer index out of range")
    return errors


def write_jsonl(path, questions):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for q in questions:
            fh.write(json.dumps(q, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp, path)


def load_bank(directory):
    """Read every *.jsonl in `directory`. Returns (bank {id: question}, problems [str])."""
    bank, problems = {}, []
    if not os.path.isdir(directory):
        return bank, [f"bank directory missing: {directory}"]
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".jsonl"):
            continue
        with open(os.path.join(directory, name), encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    q = json.loads(line)
                except json.JSONDecodeError as exc:
                    problems.append(f"{name}:{lineno}: {exc}")
                    continue
                errs = validate(q)
                if errs:
                    problems.append(f"{name}:{lineno}: {', '.join(errs)}")
                elif q["id"] not in bank:
                    bank[q["id"]] = q
    return bank, problems


def allocate(n, weights, capacity):
    """Split n over categories by weight (largest remainder), never above each category's capacity.

    Categories with no capacity are dropped and their share is redistributed. Returns {category: count}.
    """
    total_cap = sum(capacity.get(c, 0) for c in weights)
    if total_cap < n:
        raise BankTooSmall(f"need {n}, have {total_cap}")
    counts = {c: 0 for c in weights}
    remaining = n
    live = {c: w for c, w in weights.items() if capacity.get(c, 0) > 0}
    while remaining > 0 and live:
        weight_sum = sum(live.values())
        shares = {c: remaining * w / weight_sum for c, w in live.items()}
        floors = {c: min(int(s), capacity[c] - counts[c]) for c, s in shares.items()}
        for c, f in floors.items():
            counts[c] += f
        remaining = n - sum(counts.values())
        if remaining > 0:
            order = sorted(live, key=lambda c: (-(shares[c] - int(shares[c])), c))
            for c in order:
                if remaining == 0:
                    break
                if counts[c] < capacity[c]:
                    counts[c] += 1
                    remaining -= 1
        live = {c: w for c, w in live.items() if counts[c] < capacity[c]}
    return counts


def _units(ids, bank):
    """Group ids into passage units (a group is drawn together) and singletons."""
    grouped, units = {}, []
    for qid in ids:
        g = bank[qid].get("group")
        if g is None:
            units.append([qid])
        else:
            grouped.setdefault(g, []).append(qid)
    units.extend(grouped.values())
    return units


def _take(units, k, taken):
    """Take whole units into `taken` while they fit within k questions."""
    for unit in units:
        if k <= 0:
            break
        if len(unit) <= k:
            taken.append(unit)
            k -= len(unit)
    return k


def _draw(bank, pools, counts, rng, lru=None):
    """Take `counts[cat]` questions from each category pool as whole units. Returns a list of units."""
    taken = []
    for cat, k in counts.items():
        if k <= 0:
            continue
        units = _units(sorted(pools[cat]), bank)
        if lru is None:
            rng.shuffle(units)
        else:
            units.sort(key=lambda u: (min(lru[i] for i in u), u[0]))
        _take(units, k, taken)
    return taken


def _top_up(bank, candidates, short, rng, lru=None):
    """`short` more ids from `candidates`: whole questions before passage members (splitting a group is the
    last resort); shuffled when fresh, oldest first when recycling."""
    singles = [i for i in candidates if "group" not in bank[i]]
    grouped = [i for i in candidates if "group" in bank[i]]
    out = []
    for bucket in (singles, grouped):
        if lru is None:
            bucket = sorted(bucket)
            rng.shuffle(bucket)
        else:
            bucket = sorted(bucket, key=lambda i: (lru[i], i))
        out.extend(bucket)
    return out[:short]


def select(bank, n, used_seq, rng=None, weights=None):
    """Pick n question ids. Unused questions are always preferred: nothing used is repeated while enough unused
    ones remain anywhere in the bank. Once they run out, the least-recently-used come back first.

    `used_seq` maps id to the sequence number of the pass that used it (higher is more recent). Category quotas
    apply within the unused pool and again within the recycled pool. Passage groups stay together where they fit.
    Raises BankTooSmall if the bank holds fewer than n questions.
    """
    rng = rng or random.Random()
    weights = weights or QUOTAS
    if len(bank) < n:
        raise BankTooSmall(f"need {n}, have {len(bank)}")

    def pools(pred):
        by_cat = {c: [] for c in CATEGORIES}
        for qid, q in bank.items():
            if pred(qid):
                by_cat[q["category"]].append(qid)
        return by_cat

    def flat(taken):
        return [i for unit in taken for i in unit]

    chosen = []
    fresh = pools(lambda i: i not in used_seq)
    n_fresh = min(n, sum(len(v) for v in fresh.values()))
    if n_fresh:
        caps = {c: len(v) for c, v in fresh.items()}
        taken = _draw(bank, fresh, allocate(n_fresh, weights, caps), rng)
        chosen = flat(taken)
        have = set(chosen)
        rest = [i for v in fresh.values() for i in v if i not in have]
        chosen += _top_up(bank, rest, n_fresh - len(chosen), rng)
    remaining = n - len(chosen)
    if remaining > 0:
        have = set(chosen)
        stale = pools(lambda i: i in used_seq and i not in have)
        caps = {c: len(v) for c, v in stale.items()}
        taken = _draw(bank, stale, allocate(remaining, weights, caps), rng, lru=used_seq)
        chosen += flat(taken)
        have = set(chosen)
        rest = [i for v in stale.values() for i in v if i not in have]
        chosen += _top_up(bank, rest, n - len(chosen), rng, lru=used_seq)

    # Present passage groups together, in a random order of units.
    order, seen = [], set()
    for qid in chosen:
        g = bank[qid].get("group")
        if g is None:
            order.append([qid])
        elif g not in seen:
            seen.add(g)
            order.append([i for i in chosen if bank[i].get("group") == g])
    rng.shuffle(order)
    return [i for unit in order for i in unit]

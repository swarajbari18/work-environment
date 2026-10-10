"""Quiz engine: attempts, grading, the day token and tickets.

The engine owns the answer key. `present` never returns answers or explanations. A failed attempt reveals only the
score and the same set comes back on retry with the options reshuffled. A pass reveals the review, marks the set as
used, and earns either the day token (launch) or a short-lived ticket (remove_app, change_settings).
"""
import datetime as dt
import json
import os
import random
import shutil
from collections import namedtuple

import policy
import questions

Result = namedtuple("Result", "score total needed passed review")

HISTORY_LIMIT = 200


def _empty_state():
    return {"seq": 0, "used": {}, "attempts": {}, "day_token": None, "tickets": {}, "history": []}


def _perm(attempt, qid, n_options):
    """Option order shown for this question on this try. Deterministic, so grading needs no stored shuffle."""
    rng = random.Random(f"{attempt['created']}|{attempt['tier']}|{attempt['tries']}|{qid}")
    order = list(range(n_options))
    rng.shuffle(order)
    return order  # shown option j is options[order[j]]


class Vault:
    def __init__(self, bank, state_path, rng=None):
        self.bank = bank
        self.state_path = state_path
        self.rng = rng or random.Random()
        self.state = self._load()

    # --- persistence -------------------------------------------------------------------------------------------
    def _load(self):
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                state = json.load(fh)
            if not isinstance(state, dict):
                raise ValueError("state is not an object")
        except FileNotFoundError:
            return _empty_state()
        except (ValueError, OSError):
            aside = f"{self.state_path}.corrupt-{dt.datetime.now():%Y%m%d-%H%M%S}"
            try:
                shutil.copy2(self.state_path, aside)   # never overwrite an unreadable file
            except OSError:
                pass
            return _empty_state()
        merged = _empty_state()
        merged.update(state)
        return merged

    def _save(self):
        os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
        tmp = f"{self.state_path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, indent=1, sort_keys=True)
        os.replace(tmp, self.state_path)

    # --- attempts ----------------------------------------------------------------------------------------------
    def _attempt(self, tier, when):
        if tier not in policy.TIERS:
            raise KeyError(f"unknown tier: {tier}")
        attempt = self.state["attempts"].get(tier)
        if attempt is not None and all(i in self.bank for i in attempt["ids"]):
            return attempt
        used = {i: s for i, s in self.state["used"].items() if i in self.bank}
        ids = questions.select(self.bank, policy.TIERS[tier], used, self.rng)
        attempt = {"tier": tier, "ids": ids, "tries": 0, "created": when.isoformat(timespec="seconds"), "best": 0}
        self.state["attempts"][tier] = attempt
        self._save()
        return attempt

    def present(self, tier, when=None):
        """The open set for `tier` (created on first call), without answer keys. Same set until it is passed."""
        when = when or policy.now()
        attempt = self._attempt(tier, when)
        shown = []
        for qid in attempt["ids"]:
            q = self.bank[qid]
            order = _perm(attempt, qid, len(q["options"]))
            item = {"id": qid, "stem": q["stem"], "options": [q["options"][k] for k in order]}
            if "passage" in q:
                item["passage"] = q["passage"]
            shown.append(item)
        return {"tier": tier, "tries": attempt["tries"], "needed": policy.required_correct(len(shown)), "questions": shown}

    def submit(self, tier, answers, when=None):
        """Grade `answers`: one shown-option index (or None) per question, in the order `present` returned."""
        when = when or policy.now()
        attempt = self._attempt(tier, when)
        ids = attempt["ids"]
        if len(answers) != len(ids):
            raise ValueError(f"expected {len(ids)} answers, got {len(answers)}")
        correct, review = 0, []
        for qid, given in zip(ids, answers):
            q = self.bank[qid]
            order = _perm(attempt, qid, len(q["options"]))
            right_shown = order.index(q["answer"])
            ok = given == right_shown
            correct += ok
            review.append({
                "id": qid,
                "stem": q["stem"],
                "correct": q["options"][q["answer"]],
                "given": q["options"][order[given]] if isinstance(given, int) and 0 <= given < len(order) else None,
                "ok": ok,
                "explanation": q.get("explanation", ""),
            })
        needed = policy.required_correct(len(ids))
        passed = correct >= needed
        attempt["best"] = max(attempt.get("best", 0), correct)
        self._log(when, tier, correct, len(ids), passed, attempt["tries"] + 1)
        if passed:
            self._grant(tier, ids, when)
            return Result(correct, len(ids), needed, True, review)
        attempt["tries"] += 1
        self._save()
        return Result(correct, len(ids), needed, False, None)

    def _log(self, when, tier, correct, total, passed, try_no):
        self.state["history"].append({"at": when.isoformat(timespec="seconds"), "tier": tier, "score": correct,
                                      "total": total, "passed": passed, "try": try_no})
        del self.state["history"][:-HISTORY_LIMIT]

    def _grant(self, tier, ids, when):
        for qid in ids:
            self.state["seq"] += 1
            self.state["used"][qid] = self.state["seq"]
        del self.state["attempts"][tier]
        if tier == "launch":
            self.state["day_token"] = when.date().isoformat()
        else:
            self.state["tickets"][tier] = (when + policy.TICKET_TTL).isoformat(timespec="seconds")
        self._save()

    def mark_used(self, ids):
        """Count questions as used without a pass (practice runs that revealed their answers)."""
        for qid in ids:
            if qid in self.bank:
                self.state["seq"] += 1
                self.state["used"][qid] = self.state["seq"]
        self._save()

    # --- what a pass bought ------------------------------------------------------------------------------------
    def launch_allowed(self, when=None):
        when = when or policy.now()
        return policy.launch_allowed(when, self.state["day_token"])

    def has_ticket(self, tier, when=None):
        when = when or policy.now()
        return self._ticket_key(tier, when) is not None

    def _ticket_key(self, tier, when):
        live = [t for t, exp in self.state["tickets"].items()
                if dt.datetime.fromisoformat(exp) > when and policy.satisfies(t, tier)]
        if not live:
            return None
        return min(live, key=policy.TIER_ORDER.index)   # spend the smallest ticket that is enough

    def consume_ticket(self, tier, when=None):
        """Spend a ticket for `tier`. Returns True if one was held."""
        when = when or policy.now()
        key = self._ticket_key(tier, when)
        if key is None:
            return False
        del self.state["tickets"][key]
        self._save()
        return True

    def authorize(self, operation, when=None):
        """Is `operation` allowed right now without a quiz? Free operations and held tickets say yes."""
        when = when or policy.now()
        tier = policy.tier_for(operation)
        if tier is None:
            return True
        if tier == "launch":
            return self.launch_allowed(when)
        return self.has_ticket(tier, when)

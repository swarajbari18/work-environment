"""The vault daemon's request handlers. Pure logic: no sockets, no signals, no real clock.

Every request is a dict with an "op"; every reply is a dict with "ok". A reply never contains answer keys unless
the attempt was just passed (the review). Costs follow policy.OPERATIONS: free, or a ticket earned by a quiz.
"""
import datetime as dt
import json
import os
import re
import threading
import time

import config
import engine
import policy as rules
import questions
import settings

APP_NAME_RE = config.NAME_RE
LOG_TAIL_LIMIT = 500


class Service:
    def __init__(self, paths, bank, policy, owner_uid, clock=rules.now, rng=None, emit=None, load_error=None,
                 dconf_update=None):
        self.paths = paths
        self.bank = bank
        self.policy = policy                      # dict, or None when the file was unusable (fail open)
        self.owner_uid = owner_uid
        self.clock = clock
        self.emit = emit or (lambda event: None)
        self.load_error = load_error
        self.lock = threading.RLock()
        self.vault = engine.Vault(bank, paths.state_file, rng)
        self.practice = engine.Vault(bank, paths.practice_state, rng)
        self.settings = settings.Guard(paths.dconf_dir, paths.settings_file, run_update=dconf_update,
                                       clock=lambda: self.clock())
        self._reset_practice()

    # --- bookkeeping --------------------------------------------------------------------------------------------
    def _reset_practice(self):
        try:
            os.unlink(self.paths.practice_state)
        except OSError:
            pass
        self.practice.state = engine._empty_state()

    def log(self, kind, **fields):
        record = {"ts": self.clock().isoformat(timespec="seconds"), "kind": kind, **fields}
        try:
            os.makedirs(os.path.dirname(self.paths.log_file), exist_ok=True)
            with open(self.paths.log_file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, sort_keys=True) + "\n")
        except OSError:
            pass

    @property
    def enforce(self):
        """Enforcement is only ever active with a usable policy and a bank that can serve a quiz."""
        return bool(self.policy and self.policy["enforce"] and self.bank)

    def apps(self):
        return list(self.policy["apps"]) if self.policy else []

    def session(self, when=None):
        when = when or self.clock()
        return rules.session(when, self.vault.state["day_token"])

    def fail_open_reason(self):
        if self.policy is None:
            return self.load_error or "policy unusable"
        if not self.bank:
            return "question bank empty or unreadable"
        return None

    # --- dispatch -----------------------------------------------------------------------------------------------
    def handle(self, request, peer_uid):
        if peer_uid not in (0, self.owner_uid):
            return _err("forbidden", "only the owner may talk to the vault")
        if not isinstance(request, dict) or not isinstance(request.get("op"), str):
            return _err("bad_request", "request must be an object with an op")
        op = request["op"]
        method = getattr(self, f"op_{op}", None) if re.fullmatch(r"[a-z_]+", op) else None
        if method is None:
            return _err("unknown_op", f"unknown op {op!r}")
        with self.lock:
            try:
                return method(request)
            except KeyError as exc:
                return _err("bad_request", f"unknown value {exc}")
            except (TypeError, ValueError) as exc:
                return _err("bad_request", str(exc))
            except questions.BankTooSmall as exc:
                return _err("bank_too_small", str(exc))
            except settings.SettingsError as exc:
                return _err("settings_error", str(exc))

    # --- read-only ----------------------------------------------------------------------------------------------
    def op_status(self, req):
        now = self.clock()
        s = self.session(now)
        tickets = {t: exp for t, exp in self.vault.state["tickets"].items()
                   if dt.datetime.fromisoformat(exp) > now}
        return {
            "ok": True,
            "now": now.isoformat(timespec="seconds"),
            "state": s.state,
            "allowed": s.allowed,
            "warn": s.warn,
            "soft_end": s.soft_end.isoformat(timespec="seconds") if s.soft_end else None,
            "hard_end": s.hard_end.isoformat(timespec="seconds") if s.hard_end else None,
            "enforce": self.enforce,
            "policy_enforce": bool(self.policy and self.policy["enforce"]),
            "gate_required": self.enforce and not s.allowed,
            "fail_open": self.fail_open_reason(),
            "apps": [a["name"] for a in self.apps()],
            "tickets": tickets,
            "bank_size": len(self.bank),
            "used": len(self.vault.state["used"]),
            "tiers": dict(rules.TIERS),
        }

    def op_apps(self, req):
        return {"ok": True, "apps": self.apps()}

    def op_log(self, req):
        n = max(1, min(int(req.get("n", 50)), LOG_TAIL_LIMIT))
        try:
            with open(self.paths.log_file, encoding="utf-8") as fh:
                lines = fh.readlines()[-n:]
        except OSError:
            lines = []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return {"ok": True, "entries": out}

    # --- the quiz -----------------------------------------------------------------------------------------------
    def _check_tier(self, req):
        tier = req.get("tier")
        if tier not in rules.TIERS:
            raise ValueError(f"tier must be one of {', '.join(rules.TIERS)}")
        return tier

    def op_present(self, req):
        tier = self._check_tier(req)
        if not self.bank:
            return _err("bank_unavailable", "no questions available")
        shown = self.vault.present(tier, self.clock())
        return {"ok": True, **shown}

    def op_submit(self, req):
        tier = self._check_tier(req)
        answers = req.get("answers")
        if not isinstance(answers, list) or any(a is not None and (not isinstance(a, int) or isinstance(a, bool))
                                                for a in answers):
            raise ValueError("answers must be a list of option numbers or null")
        now = self.clock()
        result = self.vault.submit(tier, answers, now)
        self.log("quiz", tier=tier, score=result.score, total=result.total, passed=result.passed)
        if result.passed and tier == "launch":
            self.emit({"event": "unlocked"})
        return {"ok": True, "tier": tier, "score": result.score, "total": result.total, "needed": result.needed,
                "passed": result.passed, "review": result.review}

    def op_practice_present(self, req):
        tier = self._check_tier(req)
        if not self.bank:
            return _err("bank_unavailable", "no questions available")
        self.practice.state["used"] = dict(self.vault.state["used"])      # never repeat what real gates used
        shown = self.practice.present(tier, self.clock())
        return {"ok": True, "practice": True, **shown}

    def op_practice_submit(self, req):
        tier = self._check_tier(req)
        answers = req.get("answers")
        if not isinstance(answers, list):
            raise ValueError("answers must be a list")
        attempt = self.practice.state["attempts"].get(tier)
        ids = list(attempt["ids"]) if attempt else []
        result = self.practice.submit(tier, answers, self.clock())
        if result.passed:
            self.vault.mark_used(ids)             # its answers were revealed, so real gates must not reuse them
        return {"ok": True, "practice": True, "tier": tier, "score": result.score, "total": result.total,
                "needed": result.needed, "passed": result.passed, "review": result.review}

    # --- changing the vault -------------------------------------------------------------------------------------
    def _need_ticket(self, operation):
        """None when the operation may go ahead (and a ticket was spent if one was needed); else an error reply."""
        tier = rules.tier_for(operation)
        if tier is None:
            return None
        if not self.vault.consume_ticket(tier, self.clock()):
            return {"ok": False, "error": "ticket_required", "tier": tier, "needed": rules.TIERS[tier],
                    "message": f"pass the {rules.TIERS[tier]}-question quiz first"}
        return None

    def _require_policy(self):
        if self.policy is None:
            return _err("no_policy", self.load_error or "policy unusable")
        return None

    def op_add_app(self, req):
        problem = self._require_policy()
        if problem:
            return problem
        app = config.normalize_app(req.get("app"))
        if any(a["name"] == app["name"] for a in self.policy["apps"]):
            return _err("exists", f"{app['name']} is already in the vault; changing its rules is a removal")
        updated = {**self.policy, "apps": [*self.policy["apps"], app]}
        config.save_policy(self.paths.policy_file, updated)
        self.policy = updated
        self.log("add_app", name=app["name"])
        return {"ok": True, "added": app["name"]}

    def op_remove_app(self, req):
        problem = self._require_policy()
        if problem:
            return problem
        name = req.get("name")
        if not any(a["name"] == name for a in self.policy["apps"]):
            return _err("not_found", f"{name!r} is not in the vault")     # checked first: no ticket spent
        denied = self._need_ticket("remove_app")
        if denied:
            return denied
        updated = {**self.policy, "apps": [a for a in self.policy["apps"] if a["name"] != name]}
        config.save_policy(self.paths.policy_file, updated)
        self.policy = updated
        self.log("remove_app", name=name)
        return {"ok": True, "removed": name}

    def op_set_enforce(self, req):
        problem = self._require_policy()
        if problem:
            return problem
        value = req.get("value")
        if not isinstance(value, bool):
            raise ValueError("value must be true or false")
        if value == self.policy["enforce"]:
            return {"ok": True, "enforce": value, "changed": False}
        if not value:                                       # loosening costs the big quiz; tightening is free
            denied = self._need_ticket("change_settings")
            if denied:
                return denied
        updated = {**self.policy, "enforce": value}
        config.save_policy(self.paths.policy_file, updated)
        self.policy = updated
        self.log("set_enforce", value=value)
        return {"ok": True, "enforce": value, "changed": True}

    # --- the environment's settings -----------------------------------------------------------------------------
    def settings_tick(self):
        """Close an expired unlock window. Called by the enforcement loop; never raises."""
        with self.lock:
            try:
                if self.settings.tick():
                    self.log("settings_relock", reason="window expired uncommitted")
            except settings.SettingsError as exc:
                self.log("settings_error", error=str(exc))

    def op_settings_status(self, req):
        st = self.settings.status()
        return {"ok": True, **st, "window_minutes": int(settings.WINDOW.total_seconds() // 60)}

    def op_settings_init(self, req):
        count = self.settings.init(req.get("entries"))
        self.log("settings_init", keys=count)
        return {"ok": True, "locked": count}

    def op_settings_lock(self, req):
        count = self.settings.commit(req.get("entries"))
        self.log("settings_lock", keys=count)
        return {"ok": True, "locked": count}

    def op_settings_unlock(self, req):
        st = self.settings.status()
        if not st["initialized"]:
            return _err("not_initialized", "nothing is locked yet; run `wenv-vault settings init`")
        if not st["locked"]:
            return {"ok": True, "unlocked_until": st["unlocked_until"], "changed": False}
        denied = self._need_ticket("change_settings")
        if denied:
            return denied
        until = self.settings.unlock()
        self.log("settings_unlock", until=until)
        return {"ok": True, "unlocked_until": until, "changed": True}

    def op_prepare_uninstall(self, req):
        denied = self._need_ticket("delete_vault")
        if denied:
            return denied
        expires = self.clock() + rules.TICKET_TTL
        os.makedirs(self.paths.run_dir, exist_ok=True)
        tmp = f"{self.paths.uninstall_token}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"expires": expires.isoformat(timespec="seconds"), "epoch": time.time() + 600}, fh)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.paths.uninstall_token)
        self.log("prepare_uninstall")
        return {"ok": True, "expires": expires.isoformat(timespec="seconds"),
                "next": "sudo /opt/wenv-vault/uninstall.sh"}


def _err(code, message):
    return {"ok": False, "error": code, "message": message}

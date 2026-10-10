"""The enforcement loop: notice vault apps that are running without permission, and (when enforcing) stop them.

One `tick` is a pure function of the process table, the service state and two clocks, so it is tested with a fake
table and a fake kill. Dry-run logs what it would have done and acts on nothing. Anything unusable fails open.
"""
import os
import signal
import threading
import time

import matcher

TERM_GRACE = 8.0        # seconds between SIGTERM and SIGKILL (Steam and Firefox need time to shut down)
INTERVAL = 2.0


class Enforcer:
    def __init__(self, service, kill=os.kill, snapshot=matcher.snapshot, mono=time.monotonic,
                 term_grace=TERM_GRACE, self_pid=None):
        self.service = service
        self.kill = kill
        self.snapshot = snapshot
        self.mono = mono
        self.term_grace = term_grace
        self.self_pid = self_pid if self_pid is not None else os.getpid()
        self.env_cache = {}
        self.termed = {}              # (pid, start) -> monotonic time SIGTERM was sent
        self.killed = set()           # (pid, start) that already got SIGKILL
        self.announced = set()        # app names already reported blocked / would-block in this run
        self.warned_for = None        # soft_end value a warning was already emitted for
        self.fail_open_logged = None

    # --- helpers ------------------------------------------------------------------------------------------------
    def _send(self, pid, sig):
        try:
            self.kill(pid, sig)
            return True
        except (ProcessLookupError, PermissionError):
            return False

    def _ignored(self, procs):
        """Never a target: init, our parent, ourselves and everything we started."""
        ignored = {self.self_pid, os.getppid(), 0, 1}
        tree = {self.self_pid}
        changed = True
        while changed:
            changed = False
            for p in procs.values():
                if p["ppid"] in tree and p["pid"] not in tree:
                    tree.add(p["pid"])
                    changed = True
        return ignored | tree

    # --- one pass -----------------------------------------------------------------------------------------------
    def tick(self):
        svc = self.service
        when = svc.clock()
        now = self.mono()
        events = []

        svc.settings_tick()                       # independent of enforcement: a forgotten unlock window closes

        reason = svc.fail_open_reason()
        if reason:
            if reason != self.fail_open_logged:
                self.fail_open_logged = reason
                svc.log("fail_open", reason=reason)
            return events
        self.fail_open_logged = None

        session = svc.session(when)
        apps = svc.apps()
        procs = self.snapshot(env_keys=matcher.env_keys_of(apps), cache=self.env_cache)
        matched = matcher.classify(procs, apps, ignore_pids=self._ignored(procs))

        # A warning that the permission is about to run out, once per window. Only when enforcing: in
        # dry-run nothing will happen at midnight, so a warning would be a lie.
        if svc.enforce and session.warn and session.soft_end != self.warned_for:
            self.warned_for = session.soft_end
            event = {"event": "ending", "hard_end": session.hard_end.isoformat(timespec="seconds")}
            svc.emit(event)
            events.append(event)

        live = set()
        for name, pids in matched.items():
            if not pids:
                self.announced.discard(name)
                continue
            if session.allowed:
                continue
            keys = [(pid, procs[pid]["start"]) for pid in pids]
            live.update(keys)
            if name not in self.announced:
                self.announced.add(name)
                if svc.enforce:
                    svc.log("blocked", app=name, pids=len(pids))
                    event = {"event": "blocked", "app": name, "pids": len(pids)}
                    svc.emit(event)
                else:
                    svc.log("would_block", app=name, pids=len(pids))
                    event = {"event": "would_block", "app": name, "pids": len(pids)}
                events.append(event)
            if svc.enforce:
                self._ladder(keys, now)

        # Forget bookkeeping for processes that are gone, so a recycled pid starts clean.
        for table in (self.termed,):
            for key in [k for k in table if k not in live]:
                del table[key]
        self.killed &= live
        return events

    def _ladder(self, keys, now):
        for pid, start in keys:
            key = (pid, start)
            if pid <= 1 or pid == self.self_pid:
                continue
            if key not in self.termed:
                self.termed[key] = now
                self._send(pid, signal.SIGTERM)
            elif key not in self.killed and now - self.termed[key] >= self.term_grace:
                self.killed.add(key)
                self._send(pid, signal.SIGKILL)

    # --- loop ---------------------------------------------------------------------------------------------------
    def run(self, stop, interval=INTERVAL):
        """Tick until the `stop` threading.Event is set. A failing tick is logged and the loop carries on."""
        while not stop.is_set():
            try:
                self.tick()
            except Exception as exc:                      # noqa: BLE001 - the watchdog must not die
                self.service.log("enforce_error", error=f"{type(exc).__name__}: {exc}")
            stop.wait(interval)


def start_thread(enforcer, interval=INTERVAL):
    stop = threading.Event()
    thread = threading.Thread(target=enforcer.run, args=(stop, interval), name="enforcer", daemon=True)
    thread.start()
    return stop, thread

#!/usr/bin/env python3
"""wenv-vault-agent: the part of the vault that lives in your desktop session.

It subscribes to the daemon and turns events into windows (notifications are off in this environment):
  blocked  an app was stopped while locked: open the quiz, and start the blocked apps again after a pass
  ending   the free window or today's pass ends soon: show a warning
Runs as a systemd --user service. Reconnects when the daemon restarts.
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import client as vclient  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BACKOFF = (2, 5, 10, 30)


def gate_process(app_label):
    """Run the quiz in its own process so a toolkit crash can never take the agent down. True if passed."""
    cmd = [sys.executable, "-I", os.path.join(HERE, "gate.py"), "--tier", "launch", "--app", app_label]
    return subprocess.run(cmd).returncode == 0


def notice_process(title, message, seconds=None):
    cmd = [sys.executable, "-I", os.path.join(HERE, "gate.py"), "--notice", title, "--message", message]
    if seconds:
        cmd += ["--seconds", str(seconds)]
    subprocess.run(cmd)


def start_app(argv):
    subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                     start_new_session=True)


class Agent:
    def __init__(self, vault, gate=gate_process, notice=notice_process, start=start_app, sleep=time.sleep,
                 log=lambda m: print(m, file=sys.stderr, flush=True)):
        self.vault, self.gate, self.notice, self.start, self.sleep, self.log = vault, gate, notice, start, sleep, log
        self.gate_open = False
        self.waiting = []                      # app names blocked while the quiz was open or opening

    def launch_command(self, name):
        try:
            for app in self.vault.call("apps").get("apps", []):
                if app["name"] == name and app.get("launch"):
                    return list(app["launch"])
        except vclient.VaultUnavailable:
            pass
        return None

    def on_event(self, event):
        kind = event.get("event")
        if kind == "blocked":
            app = event.get("app", "app")
            if app not in self.waiting:
                self.waiting.append(app)
            if self.gate_open:
                return
            self.gate_open = True
            try:
                passed = self.gate(app)
            finally:
                self.gate_open = False
            pending, self.waiting = self.waiting, []
            if passed:
                for name in pending:
                    cmd = self.launch_command(name)
                    if cmd:
                        self.start(cmd)
                    else:
                        self.log(f"no launch command for {name}; start it yourself")
        elif kind == "ending":
            when = event.get("hard_end", "")[11:16]
            self.notice("Vault: free time is ending",
                        f"Access to the apps in your vault ends at {when}. Save your work. "
                        "After that they need the quiz again.", 60)

    def run_once(self):
        """One connection's lifetime. Returns when the daemon goes away."""
        for event in self.vault.subscribe():
            try:
                self.on_event(event)
            except Exception as exc:                        # noqa: BLE001 - one bad event must not end the agent
                self.log(f"agent: event {event!r} failed: {exc}")

    def run(self, max_cycles=None):
        attempt, cycles = 0, 0
        while max_cycles is None or cycles < max_cycles:
            cycles += 1
            try:
                self.run_once()
                attempt = 0
            except vclient.VaultUnavailable:
                pass
            self.sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
            attempt += 1


def main():
    Agent(vclient.VaultClient()).run()


if __name__ == "__main__":
    main()

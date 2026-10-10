#!/usr/bin/env python3
"""Log-only vault watcher (P1). Observes vault apps and records what enforcement *would* have done. Kills nothing.

  watch.py run [--interval 3]   follow processes and append JSONL events to the data directory
  watch.py once                 print which vault apps are running right now, and why
  watch.py report [--days N]    launches, minutes and would-have-been-blocked launches per day

Data: $WENV_VAULT_DATA or ~/.local/share/wenv-vault (watch.jsonl, optional apps.json overriding the default rules).
"""
import argparse
import datetime as dt
import json
import os
import signal
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import matcher  # noqa: E402
import policy  # noqa: E402

TICK_SECONDS = 60


def data_dir():
    return os.environ.get("WENV_VAULT_DATA") or os.path.expanduser("~/.local/share/wenv-vault")


def load_apps(directory):
    path = os.path.join(directory, "apps.json")
    try:
        with open(path, encoding="utf-8") as fh:
            apps = json.load(fh)
        if isinstance(apps, list) and all("name" in a for a in apps):
            return apps
    except (OSError, ValueError):
        pass
    return matcher.DEFAULT_APPS


class Tracker:
    """Turns successive 'which pids match each app' observations into start and stop events."""

    def __init__(self):
        self.running = {}
        self.first = True

    def update(self, when, matched):
        events = []
        for app, pids in matched.items():
            if pids and app not in self.running:
                self.running[app] = when
                events.append({"event": "start", "app": app, "pids": len(pids), "already_running": self.first})
        for app in list(self.running):
            if not matched.get(app):
                since = self.running.pop(app)
                events.append({"event": "stop", "app": app, "duration_s": round((when - since).total_seconds())})
        self.first = False
        return events


def stamp(event, when):
    ph = policy.phase(when)
    event.update({
        "ts": when.isoformat(timespec="seconds"),
        "weekday": when.strftime("%a"),
        "phase": ph,
        # Log-only mode has no day token, so any launch on a locked day is one enforcement would have stopped.
        "would_block": ph == "locked",
    })
    return event


def observe(apps, ignore=()):
    procs = matcher.snapshot(env_keys=matcher.env_keys_of(apps))
    return matcher.classify(procs, apps, ignore_pids=ignore), procs


def cmd_run(args):
    directory = data_dir()
    os.makedirs(directory, exist_ok=True)
    apps = load_apps(directory)
    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(now=True))
    tracker = Tracker()
    last_tick = 0.0
    me = {os.getpid(), os.getppid()}
    with open(os.path.join(directory, "watch.jsonl"), "a", encoding="utf-8", buffering=1) as log:
        while not stop["now"]:
            when = policy.now()
            matched, _ = observe(apps, ignore=me)
            for event in tracker.update(when, matched):
                log.write(json.dumps(stamp(event, when)) + "\n")
            if time.monotonic() - last_tick >= TICK_SECONDS:
                last_tick = time.monotonic()
                running = sorted(a for a, p in matched.items() if p)
                if running:
                    log.write(json.dumps(stamp({"event": "tick", "apps": running}, when)) + "\n")
            time.sleep(args.interval)
    return 0


def cmd_once(args):
    apps = load_apps(data_dir())
    matched, procs = observe(apps, ignore={os.getpid(), os.getppid()})
    when = policy.now()
    print(f"now {when:%a %Y-%m-%d %H:%M}  phase={policy.phase(when)}")
    for name, pids in matched.items():
        print(f"{name}: {'RUNNING' if pids else 'not running'} ({len(pids)} processes)")
        for pid in pids[:12]:
            p = procs[pid]
            print(f"    {pid:>7}  {p['comm']:<18} {p['exe'][:70]}")
        if len(pids) > 12:
            print(f"    ... and {len(pids) - 12} more")
    return 0


def summarize(lines, since=None):
    """Aggregate JSONL records into {(date, app): {"launches", "minutes", "would_block", "weekday", "phase"}}."""
    rows = defaultdict(lambda: {"launches": 0, "minutes": 0, "would_block": 0, "weekday": "", "phase": ""})
    for line in lines:
        try:
            e = json.loads(line)
            day = e["ts"][:10]
        except (ValueError, KeyError, TypeError):
            continue
        if since and day < since:
            continue
        apps = e.get("apps") if e.get("event") == "tick" else [e.get("app")]
        for app in apps or []:
            row = rows[(day, app)]
            row["weekday"], row["phase"] = e.get("weekday", ""), e.get("phase", "")
            if e["event"] == "tick":
                row["minutes"] += 1
            elif e["event"] == "start":
                row["launches"] += 1
                if e.get("would_block"):
                    row["would_block"] += 1
    return dict(rows)


def cmd_report(args):
    path = os.path.join(data_dir(), "watch.jsonl")
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        print(f"no log yet at {path}; start the watcher with: watch.py run")
        return 1
    since = None
    if args.days:
        since = (policy.now().date() - dt.timedelta(days=args.days - 1)).isoformat()
    rows = summarize(lines, since)
    if not rows:
        print("no vault activity recorded")
        return 0
    print(f"{'date':<11}{'day':<5}{'phase':<8}{'app':<10}{'launches':>9}{'minutes':>9}{'would block':>13}")
    for (day, app), r in sorted(rows.items()):
        print(f"{day:<11}{r['weekday']:<5}{r['phase']:<8}{app:<10}{r['launches']:>9}{r['minutes']:>9}{r['would_block']:>13}")
    blocked = sum(r["would_block"] for r in rows.values())
    print(f"\nlaunches enforcement would have stopped: {blocked}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--interval", type=float, default=3.0)
    sub.add_parser("once")
    rep = sub.add_parser("report")
    rep.add_argument("--days", type=int, default=0)
    args = parser.parse_args(argv)
    return {"run": cmd_run, "once": cmd_once, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())

"""Schedule and tier rules for the vault.

Design: see DESIGN.md. All datetimes are naive local time, in whatever timezone the machine is set to.
`WENV_VAULT_NOW` (ISO 8601) replaces the clock, so weekday behaviour can be tested on a weekend.
"""
import datetime as dt
import math
import os
from collections import namedtuple
from fractions import Fraction

FREE_WEEKDAYS = (5, 6)                      # Monday = 0, so Saturday and Sunday
PASS_RATE = Fraction(4, 5)                  # 80 %
TIERS = {"launch": 30, "remove_app": 60, "change_settings": 120}
TIER_ORDER = ("launch", "remove_app", "change_settings")
GRACE = dt.timedelta(minutes=10)            # tolerance after midnight, before a window really ends
WARN_BEFORE = dt.timedelta(minutes=10)      # the warning window opens this long before midnight
LOOKAHEAD_DAYS = 9
TICKET_TTL = dt.timedelta(minutes=10)

# What each user action costs. None means free.
OPERATIONS = {
    "launch": "launch",
    "add_app": None,
    "remove_app": "remove_app",
    "change_settings": "change_settings",
    "delete_vault": "change_settings",
    "repair": None,
}


def now():
    raw = os.environ.get("WENV_VAULT_NOW")
    return dt.datetime.fromisoformat(raw) if raw else dt.datetime.now()


def is_free_day(when, free_days=FREE_WEEKDAYS):
    return when.weekday() in free_days


def phase(when, free_days=FREE_WEEKDAYS, grace=GRACE):
    """'free' | 'ending' (free, last `grace` before a locked day) | 'grace' (just after) | 'locked'."""
    if is_free_day(when, free_days):
        return "free" if is_free_day(when + grace, free_days) else "ending"
    return "grace" if is_free_day(when - grace, free_days) else "locked"


def required_correct(total, rate=PASS_RATE):
    return math.ceil(Fraction(total) * rate)


def token_valid(token_date, when):
    """A launch pass is good for the local calendar day it was earned on."""
    return token_date == when.date().isoformat()


def launch_allowed(when, token_date, free_days=FREE_WEEKDAYS, grace=GRACE):
    """Free day or its grace, today's launch pass, or (for `grace` after midnight) yesterday's pass."""
    return (phase(when, free_days, grace) != "locked"
            or token_valid(token_date, when)
            or token_valid(token_date, when - grace))


Session = namedtuple("Session", "state allowed soft_end hard_end warn")


def session(when, token_date, free_days=FREE_WEEKDAYS, grace=GRACE):
    """Where `when` stands, and when the current permission runs out if nothing new is earned.

    state: 'free' (weekend or its grace), 'unlocked' (a pass was earned), 'locked'.
    soft_end: the midnight the permission ends at; hard_end: soft_end + grace, when enforcement acts.
    warn: inside the last WARN_BEFORE before soft_end (and before hard_end), so a warning should be on screen.
    """
    if not launch_allowed(when, token_date, free_days, grace):
        return Session("locked", False, None, None, False)
    state = "free" if phase(when, free_days, grace) != "locked" else "unlocked"
    midnight = dt.datetime.combine(when.date(), dt.time.min)
    for i in range(LOOKAHEAD_DAYS + 1):
        edge = midnight + dt.timedelta(days=i) + grace
        if edge > when and not launch_allowed(edge, token_date, free_days, grace):
            soft = edge - grace
            return Session(state, True, soft, edge, soft - WARN_BEFORE <= when < edge)
    return Session(state, True, None, None, False)


def tier_for(operation):
    if operation not in OPERATIONS:
        raise KeyError(f"unknown operation: {operation}")
    return OPERATIONS[operation]


def satisfies(held_tier, needed_tier):
    """A held ticket covers its own tier and every smaller one (120 covers 60). A launch pass is not a ticket."""
    if "launch" in (held_tier, needed_tier):
        return held_tier == needed_tier
    return TIER_ORDER.index(held_tier) >= TIER_ORDER.index(needed_tier)

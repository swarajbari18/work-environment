import datetime as dt
import os
import unittest
from unittest import mock

import policy

SAT = dt.datetime(2026, 10, 10, 12, 0)
SUN = dt.datetime(2026, 10, 11, 12, 0)
MON = dt.datetime(2026, 10, 12, 10, 0)
TUE = dt.datetime(2026, 10, 13, 10, 0)


class FreeDays(unittest.TestCase):
    def test_weekend_is_free_and_weekdays_are_not(self):
        self.assertTrue(policy.is_free_day(SAT))
        self.assertTrue(policy.is_free_day(SUN))
        for day in range(12, 17):
            self.assertFalse(policy.is_free_day(dt.datetime(2026, 10, day, 12, 0)))

    def test_phase_boundaries(self):
        cases = [
            (dt.datetime(2026, 10, 9, 23, 59), "locked"),          # Friday night
            (dt.datetime(2026, 10, 10, 0, 0), "free"),             # Saturday starts
            (dt.datetime(2026, 10, 11, 23, 49, 59), "free"),
            (dt.datetime(2026, 10, 11, 23, 50), "ending"),         # warning window
            (dt.datetime(2026, 10, 12, 0, 0), "grace"),
            (dt.datetime(2026, 10, 12, 0, 9, 59), "grace"),
            (dt.datetime(2026, 10, 12, 0, 10), "locked"),
        ]
        for when, expected in cases:
            self.assertEqual(policy.phase(when), expected, when)

    def test_saturday_start_has_no_grace_or_warning(self):
        self.assertEqual(policy.phase(dt.datetime(2026, 10, 10, 0, 5)), "free")
        self.assertEqual(policy.phase(dt.datetime(2026, 10, 9, 23, 55)), "locked")


class PassMarks(unittest.TestCase):
    def test_eighty_percent_of_each_tier(self):
        self.assertEqual(policy.required_correct(30), 24)
        self.assertEqual(policy.required_correct(60), 48)
        self.assertEqual(policy.required_correct(120), 96)

    def test_rounds_up(self):
        self.assertEqual(policy.required_correct(7), 6)
        self.assertEqual(policy.required_correct(1), 1)


class DayToken(unittest.TestCase):
    def test_valid_only_on_the_day_it_was_earned(self):
        self.assertTrue(policy.token_valid("2026-10-12", MON))
        self.assertFalse(policy.token_valid("2026-10-12", TUE))
        self.assertFalse(policy.token_valid(None, MON))

    def test_launch_rules(self):
        self.assertTrue(policy.launch_allowed(SAT, None))
        self.assertTrue(policy.launch_allowed(SUN, None))
        self.assertFalse(policy.launch_allowed(MON, None))
        self.assertTrue(policy.launch_allowed(MON, "2026-10-12"))
        self.assertFalse(policy.launch_allowed(TUE, "2026-10-12"))

    def test_grace_after_the_weekend_allows_launch(self):
        self.assertTrue(policy.launch_allowed(dt.datetime(2026, 10, 12, 0, 5), None))


class SessionWindow(unittest.TestCase):
    def at(self, day, hh, mm=0, ss=0):
        return dt.datetime(2026, 10, day, hh, mm, ss)

    def test_weekend_runs_out_at_monday_midnight(self):
        s = policy.session(self.at(11, 12), None)
        self.assertEqual((s.state, s.allowed, s.warn), ("free", True, False))
        self.assertEqual(s.soft_end, self.at(12, 0))
        self.assertEqual(s.hard_end, self.at(12, 0, 10))

    def test_warning_opens_ten_minutes_before_midnight(self):
        self.assertFalse(policy.session(self.at(11, 23, 49, 59), None).warn)
        self.assertTrue(policy.session(self.at(11, 23, 50), None).warn)
        self.assertTrue(policy.session(self.at(12, 0, 5), None).warn)          # inside the grace
        self.assertEqual(policy.session(self.at(12, 0, 5), None).state, "free")

    def test_locked_after_the_grace(self):
        s = policy.session(self.at(12, 0, 10), None)
        self.assertEqual((s.state, s.allowed, s.hard_end, s.warn), ("locked", False, None, False))

    def test_weekday_pass_lasts_until_midnight(self):
        s = policy.session(self.at(12, 10), "2026-10-12")
        self.assertEqual((s.state, s.allowed), ("unlocked", True))
        self.assertEqual((s.soft_end, s.hard_end), (self.at(13, 0), self.at(13, 0, 10)))
        self.assertFalse(s.warn)
        self.assertTrue(policy.session(self.at(12, 23, 55), "2026-10-12").warn)

    def test_yesterdays_pass_covers_the_first_ten_minutes(self):
        s = policy.session(self.at(13, 0, 5), "2026-10-12")
        self.assertEqual((s.state, s.allowed, s.warn), ("unlocked", True, True))
        self.assertFalse(policy.session(self.at(13, 0, 10), "2026-10-12").allowed)

    def test_friday_pass_runs_into_the_free_weekend(self):
        s = policy.session(self.at(16, 15), "2026-10-16")
        self.assertEqual(s.state, "unlocked")
        self.assertEqual(s.soft_end, self.at(19, 0))                            # the Monday after

    def test_locked_weekday_without_pass(self):
        s = policy.session(self.at(13, 10), "2026-10-12")
        self.assertEqual((s.state, s.allowed), ("locked", False))


class Operations(unittest.TestCase):
    def test_costs(self):
        self.assertEqual(policy.tier_for("launch"), "launch")
        self.assertIsNone(policy.tier_for("add_app"))
        self.assertIsNone(policy.tier_for("repair"))
        self.assertEqual(policy.tier_for("remove_app"), "remove_app")
        self.assertEqual(policy.tier_for("change_settings"), "change_settings")
        self.assertEqual(policy.tier_for("delete_vault"), "change_settings")
        with self.assertRaises(KeyError):
            policy.tier_for("something_else")

    def test_ticket_hierarchy(self):
        self.assertTrue(policy.satisfies("change_settings", "remove_app"))
        self.assertTrue(policy.satisfies("remove_app", "remove_app"))
        self.assertFalse(policy.satisfies("remove_app", "change_settings"))
        self.assertTrue(policy.satisfies("launch", "launch"))
        self.assertFalse(policy.satisfies("launch", "remove_app"))
        self.assertFalse(policy.satisfies("change_settings", "launch"))


class Clock(unittest.TestCase):
    def test_env_override(self):
        with mock.patch.dict(os.environ, {"WENV_VAULT_NOW": "2026-10-13T09:30:00"}):
            self.assertEqual(policy.now(), dt.datetime(2026, 10, 13, 9, 30))
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WENV_VAULT_NOW", None)
            self.assertLess(abs((policy.now() - dt.datetime.now()).total_seconds()), 5)


if __name__ == "__main__":
    unittest.main()

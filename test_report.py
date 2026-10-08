"""Tests for report.py. Expected values were worked out by hand from the metric definitions.

Run: python3 -m unittest -v test_report.py
"""
import unittest

import report

BASE = 1_700_000_000  # arbitrary epoch origin


def ev(offset, app, title):
    return {"timestamp": report.iso(BASE + offset), "duration": 0.0, "data": {"app": app, "title": title}}


def afk(offset, status, duration):
    return {"timestamp": report.iso(BASE + offset), "duration": float(duration), "data": {"status": status}}


WINDOW = [
    ev(0, "brave-browser", "YouTube - Home"),
    ev(100, "cursor", "main.py"),
    ev(160, "brave-browser", "Docs - Google"),
    ev(200, "gnome-terminal-server", "shell"),
]


class ComputeTests(unittest.TestCase):
    def test_short_afk_inside_work_is_subtracted_but_not_a_period(self):
        m = report.compute(WINDOW, [afk(130, "afk", 20)], BASE, BASE + 260)
        self.assertAlmostEqual(m["active_seconds"], 240)          # 260 window seconds minus 20 AFK
        self.assertAlmostEqual(m["browser_seconds"], 140)         # YouTube 100 + Docs 40
        self.assertAlmostEqual(m["entertainment_seconds"], 100)   # only the YouTube title matches
        self.assertAlmostEqual(m["work_app_seconds"], 100)        # cursor 40 + terminal 60
        self.assertEqual(m["browser_launches"], 2)                # first browser focus, and the return after cursor
        self.assertEqual(m["task_switches"], 3)                   # brave -> cursor -> brave -> terminal
        self.assertEqual(len(m["work_sessions_seconds"]), 2)
        self.assertAlmostEqual(m["work_sessions_seconds"][0], 40)
        self.assertAlmostEqual(m["work_sessions_seconds"][1], 60)
        self.assertEqual(m["afk_periods_count"], 0)               # 20 s is under the 60 s threshold
        self.assertAlmostEqual(m["afk_seconds_total"], 20)

    def test_long_afk_counts_as_a_period_and_breaks_the_session(self):
        m = report.compute(WINDOW, [afk(130, "afk", 200)], BASE, BASE + 260)  # AFK from 130 to end of window
        self.assertAlmostEqual(m["active_seconds"], 130)          # cursor 30 (until 130) + YouTube 100
        self.assertEqual(m["afk_periods_count"], 1)
        self.assertAlmostEqual(m["afk_seconds_total"], 130)
        self.assertEqual(m["work_sessions_seconds"], [30])        # the cursor stretch before the AFK
        self.assertAlmostEqual(m["work_app_seconds"], 30)         # terminal time was all AFK

    def test_empty_input_gives_zeros(self):
        m = report.compute([], [], BASE, BASE + 3600)
        self.assertEqual(m["active_seconds"], 0)
        self.assertEqual(m["browser_launches"], 0)
        self.assertEqual(m["task_switches"], 0)
        self.assertEqual(m["work_sessions_seconds"], [])
        self.assertEqual(m["afk_periods_count"], 0)

    def test_window_is_clipped_to_requested_range(self):
        # Only 50 s of the YouTube interval fall inside [BASE+50, BASE+100].
        m = report.compute(WINDOW, [], BASE + 50, BASE + 100)
        self.assertAlmostEqual(m["active_seconds"], 50)
        self.assertAlmostEqual(m["browser_seconds"], 50)
        self.assertAlmostEqual(m["entertainment_seconds"], 50)

    def test_last_heartbeat_is_capped(self):
        # A single window event with no follower must not count for more than MAX_EVENT_SECONDS.
        m = report.compute([ev(0, "cursor", "x")], [], BASE, BASE + 10_000)
        self.assertAlmostEqual(m["active_seconds"], report.MAX_EVENT_SECONDS)

    def test_heartbeats_of_one_app_merge_into_one_run(self):
        beats = [ev(s, "cursor", "spinner %d" % s) for s in range(0, 30)]  # one event per second
        m = report.compute(beats, [], BASE, BASE + 30)
        self.assertAlmostEqual(m["active_seconds"], 30)
        self.assertEqual(m["task_switches"], 0)
        self.assertEqual(m["work_sessions_seconds"], [30])

    def test_entertainment_app_counts_even_without_browser(self):
        m = report.compute([ev(0, "steam", "Library"), ev(60, "cursor", "x")], [], BASE, BASE + 120)
        self.assertAlmostEqual(m["entertainment_seconds"], 60)


class RenderTests(unittest.TestCase):
    def test_render_contains_every_protocol_metric(self):
        m = report.compute(WINDOW, [afk(130, "afk", 20)], BASE, BASE + 260)
        text = report.render(m, "t")
        for label in ["Browser time", "Entertainment time", "Work-app time", "Browser launches",
                      "Task switches", "Work sessions", "AFK periods"]:
            self.assertIn(label, text)


if __name__ == "__main__":
    unittest.main()

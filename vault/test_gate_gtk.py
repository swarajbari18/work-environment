import json
import os
import shutil
import subprocess
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BROADWAYD = shutil.which("gtk4-broadwayd")


def gtk_available():
    r = subprocess.run([sys.executable, "-I", "-c",
                        "import gi; gi.require_version('Gtk','4.0'); from gi.repository import Gtk"],
                       capture_output=True)
    return r.returncode == 0


@unittest.skipUnless(BROADWAYD and gtk_available(), "needs gtk4-broadwayd and PyGObject with GTK 4")
class GateWindow(unittest.TestCase):
    """Drives the real GTK window headlessly through the Broadway backend."""

    @classmethod
    def setUpClass(cls):
        cls.display = f":{40 + os.getpid() % 50}"
        cls.broadway = subprocess.Popen([BROADWAYD, cls.display], stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL)
        time.sleep(1.0)

    @classmethod
    def tearDownClass(cls):
        cls.broadway.terminate()
        cls.broadway.wait(timeout=10)

    def run_scenario(self, name):
        env = {**os.environ, "GDK_BACKEND": "broadway", "BROADWAY_DISPLAY": self.display}
        r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "gate_smoke.py"), name], env=env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertNotIn("error", out, out)
        return out, {s["step"]: s for s in out["steps"]}

    def test_pass_flow(self):
        out, s = self.run_scenario("pass")
        self.assertEqual(s["loaded"]["options"], ["A.  a", "B.  b", "C.  c", "D.  d"])
        self.assertIn("1 / 3", s["loaded"]["progress"])
        self.assertIn("2 correct to pass", s["loaded"]["progress"])
        self.assertFalse(s["loaded"]["passage_visible"])
        self.assertFalse(s["loaded"]["back_visible"])
        self.assertEqual(s["clicked"]["answers"], [1, None, None])
        self.assertEqual((s["next"]["index"], s["next"]["stem"]), (1, "Question 1?"))
        self.assertEqual(s["keys"]["answers"], [1, 1, 1])
        self.assertEqual(s["result"]["result"], "Passed: 3 / 3")
        self.assertEqual(s["result"]["note"], "Unlocked until midnight.")
        self.assertEqual(s["result"]["review_rows"], 3)
        self.assertFalse(s["result"]["retry_visible"])
        self.assertTrue(out["passed_flag"])
        self.assertEqual(out["close_label"], "Open Firefox")
        self.assertEqual(out["calls"], ["present", "submit"])

    def test_fail_then_retry_flow(self):
        out, s = self.run_scenario("fail")
        self.assertEqual(s["keys"]["answers"], [0, 0, 0])
        self.assertEqual(s["result"]["result"], "0 / 3")
        self.assertTrue(s["result"]["retry_visible"])
        self.assertEqual(s["result"]["review_rows"], 0)
        self.assertIn("answers are not shown", s["result"]["note"])
        self.assertEqual(s["retried"]["tries"], 1)
        self.assertEqual(s["retried"]["answers"], [None, None, None])
        self.assertEqual(out["calls"].count("present"), 2)
        self.assertFalse(out["passed_flag"])
        self.assertEqual(out["close_label"], "Close")


if __name__ == "__main__":
    unittest.main()

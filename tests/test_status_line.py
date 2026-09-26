"""Sample-JSON tests for the rate-limit segment of status_line.py."""

import json
import os
import re
import subprocess
import sys
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "status_line.py")
ANSI = re.compile(r"\033\[[0-9;]*m")
sys.path.insert(0, os.path.dirname(SCRIPT))
import status_line as sl  # noqa: E402


def render(data):
    proc = subprocess.run([sys.executable, SCRIPT], input=json.dumps(data), capture_output=True,
                          text=True, encoding="utf-8", timeout=20)
    assert proc.returncode == 0 and not proc.stderr, proc.stderr
    return proc.stdout.strip()


BASE = {"model": {"display_name": "Opus 5.5"}, "workspace": {"current_dir": os.path.expanduser("~")}}


class LimitsTest(unittest.TestCase):
    def test_both_windows(self):
        out = sl.limits_section({"five_hour": {"used_percentage": 42.4, "resets_at": 1},
                                 "seven_day": {"used_percentage": 71.0, "resets_at": 2}})
        self.assertEqual(ANSI.sub("", out), "5h 42% · 7d 71%")

    def test_hides_absent_window(self):
        out = sl.limits_section({"seven_day": {"used_percentage": 12.6}})
        self.assertEqual(ANSI.sub("", out), "7d 13%")

    def test_hidden_when_absent(self):
        self.assertIsNone(sl.limits_section(None))
        self.assertIsNone(sl.limits_section({}))
        self.assertIsNone(sl.limits_section({"five_hour": {}, "seven_day": None}))

    def test_skips_malformed_windows(self):
        out = sl.limits_section({"five_hour": 42, "seven_day": {"used_percentage": "71"}})
        self.assertIsNone(out)
        out = sl.limits_section({"five_hour": {"used_percentage": True}, "seven_day": {"used_percentage": 71}})
        self.assertEqual(ANSI.sub("", out), "7d 71%")
        self.assertIsNone(sl.limits_section("garbage"))

    def test_zero_percent_is_shown(self):
        self.assertEqual(ANSI.sub("", sl.limits_section({"five_hour": {"used_percentage": 0}})), "5h 0%")

    def test_colors_by_percentage(self):
        def color(pct):
            return sl.limits_section({"five_hour": {"used_percentage": pct}}).split(f"5h{sl.RESET} ")[1]
        self.assertTrue(color(10).startswith(sl.DIM))
        self.assertTrue(color(55).startswith(sl.YELLOW))
        self.assertTrue(color(80).startswith(sl.ORANGE))
        self.assertTrue(color(95).startswith(sl.RED))

    def test_full_line_includes_limits_last(self):
        data = dict(BASE, rate_limits={"five_hour": {"used_percentage": 42}, "seven_day": {"used_percentage": 71}})
        self.assertTrue(ANSI.sub("", render(data)).endswith("| 5h 42% · 7d 71%"))

    def test_full_line_without_limits_unchanged(self):
        self.assertNotIn("5h", ANSI.sub("", render(BASE)))


if __name__ == "__main__":
    unittest.main(verbosity=2)

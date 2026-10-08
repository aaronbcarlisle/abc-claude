"""Sample-JSON tests for the segments of status_line.py."""

import json
import os
import re
import subprocess
import sys
import tempfile
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


class ContextTest(unittest.TestCase):
    def test_tokens_and_size(self):
        out = sl.context_section({"total_input_tokens": 312_400, "context_window_size": 1_000_000})
        self.assertEqual(ANSI.sub("", out), "312k/1M")

    def test_zero_tokens_is_shown(self):
        out = sl.context_section({"total_input_tokens": 0, "context_window_size": 1_000_000})
        self.assertEqual(ANSI.sub("", out), "0/1M")

    def test_percentage_fallback_shows_zero(self):
        self.assertEqual(ANSI.sub("", sl.context_section({"used_percentage": 0})), "0%")
        self.assertEqual(ANSI.sub("", sl.context_section({"used_percentage": 37.6})), "37%")

    def test_hidden_when_absent(self):
        self.assertIsNone(sl.context_section({}))


BRIDGE_LINE = '{"type":"bridge-session","sessionId":"s","bridgeSessionId":"cse_1","lastSequenceNum":1}\n'
DISCONNECT_LINE = '{"type":"bridge-session","sessionId":"s","bridgeSessionId":"","lastSequenceNum":0}\n'
OTHER_LINE = '{"type":"assistant","message":{"content":"hi"}}\n'


class RemoteTest(unittest.TestCase):
    def transcript(self, text):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        self.addCleanup(os.remove, path)
        return path

    def test_badge_when_bridge_record_in_tail(self):
        path = self.transcript(OTHER_LINE + BRIDGE_LINE + OTHER_LINE)
        self.assertEqual(ANSI.sub("", sl.remote_section(path)), "RC")

    def test_bridge_record_on_first_line(self):
        self.assertTrue(sl.remote_connected(self.transcript(BRIDGE_LINE)))

    def test_hidden_without_bridge_record(self):
        self.assertIsNone(sl.remote_section(self.transcript(OTHER_LINE * 3)))

    def test_hidden_after_disconnect(self):
        path = self.transcript(BRIDGE_LINE + OTHER_LINE + DISCONNECT_LINE + OTHER_LINE)
        self.assertFalse(sl.remote_connected(path))

    def test_reconnect_after_disconnect(self):
        self.assertTrue(sl.remote_connected(self.transcript(DISCONNECT_LINE + OTHER_LINE + BRIDGE_LINE)))

    def test_ignores_escaped_mention_in_tool_output(self):
        quoted = json.dumps({"type": "user", "content": BRIDGE_LINE}) + "\n"
        self.assertFalse(sl.remote_connected(self.transcript(quoted)))

    def test_ignores_record_outside_tail(self):
        path = self.transcript(BRIDGE_LINE + "x" * sl.REMOTE_TAIL_BYTES + "\n")
        self.assertFalse(sl.remote_connected(path))

    def test_record_beyond_scan_window_but_inside_tail(self):
        path = self.transcript(BRIDGE_LINE + "x" * (sl.REMOTE_SCAN_BYTES * 2) + "\n")
        self.assertTrue(sl.remote_connected(path))

    def test_record_starting_at_tail_edge(self):
        path = self.transcript("x" * 10 + "\n" + BRIDGE_LINE + "y" * (sl.REMOTE_TAIL_BYTES - len(BRIDGE_LINE)))
        self.assertTrue(sl.remote_connected(path))

    def test_partial_record_mid_append_falls_back(self):
        path = self.transcript(BRIDGE_LINE + OTHER_LINE + DISCONNECT_LINE[:30])
        self.assertTrue(sl.remote_connected(path))

    def test_missing_or_absent_path(self):
        self.assertFalse(sl.remote_connected(None))
        self.assertFalse(sl.remote_connected(os.path.join(tempfile.gettempdir(), "no-such-transcript.jsonl")))

    def test_full_line_puts_badge_first(self):
        out = ANSI.sub("", render(dict(BASE, transcript_path=self.transcript(BRIDGE_LINE))))
        self.assertTrue(out.startswith("RC | Opus 5.5"), out)


if __name__ == "__main__":
    unittest.main(verbosity=2)

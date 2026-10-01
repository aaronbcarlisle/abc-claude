#!/usr/bin/env python3
"""
Tests for session_start.py: it prints the head of SESSION_HANDOFF.md and the
newest Progress Log entries, and stays silent without one.

    python -m unittest discover -s tests
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "session_start.py")


def run_hook(project, payload_cwd=None):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=project) if project else {
        k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    payload = json.dumps({"hook_event_name": "SessionStart", "cwd": payload_cwd or project})
    proc = subprocess.run([sys.executable, HOOK], input=payload.encode("utf-8"), capture_output=True,
                          timeout=30, env=env)
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


class SessionStartTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text):
        with open(os.path.join(self.dir, "SESSION_HANDOFF.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    def test_silent_without_a_handoff(self):
        self.assertEqual(run_hook(self.dir), (0, "", ""))

    def test_prints_the_head_and_newest_log_entries(self):
        body = ["# Session Handoff — test", ""] + ["line %d" % i for i in range(40)]
        log = ["## Progress Log", ""] + ["- entry %d" % i for i in range(8)]
        self.write("\n".join(body + log) + "\n")
        code, out, _ = run_hook(self.dir)
        self.assertEqual(code, 0)
        self.assertIn("SESSION_HANDOFF.md (updated", out)
        self.assertIn("# Session Handoff — test", out)
        self.assertIn("line 22", out)
        self.assertNotIn("line 30", out)
        self.assertIn("more lines", out)
        self.assertIn("- entry 7", out)
        self.assertIn("- entry 3", out)
        self.assertNotIn("- entry 2", out)

    def test_short_handoff_has_no_tail_section(self):
        self.write("# Handoff\n\nAll done.\n")
        code, out, _ = run_hook(self.dir)
        self.assertEqual(code, 0)
        self.assertIn("All done.", out)
        self.assertNotIn("Progress Log", out)
        self.assertNotIn("more lines", out)

    def test_falls_back_to_the_payload_cwd(self):
        self.write("# Handoff from cwd\n")
        code, out, _ = run_hook(None, payload_cwd=self.dir)
        self.assertEqual(code, 0)
        self.assertIn("Handoff from cwd", out)

    def test_bad_input_exits_zero(self):
        proc = subprocess.run([sys.executable, HOOK], input=b"not json", capture_output=True,
                              timeout=30, env={k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"})
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

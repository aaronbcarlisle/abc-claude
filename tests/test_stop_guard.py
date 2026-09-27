#!/usr/bin/env python3
"""
Tests for hooks/stop_guard.py. Runs the hook as a subprocess with synthetic
Stop input.

    python -m unittest discover -s tests
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "stop_guard.py")


class StopGuardCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.env = dict(os.environ, STOP_GUARD_STATE_DIR=os.path.join(self.tmp.name, "state"))
        for key in ("CLAUDE_AUTOPILOT", "CLAUDE_AUTOPILOT_HANDOFF"):
            self.env.pop(key, None)

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, message="Done.", active=False, **extra):
        payload = {"session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": active,
                   "last_assistant_message": message, **extra}
        proc = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                              encoding="utf-8", env=self.env, timeout=20)
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))
        out = proc.stdout.strip()
        return json.loads(out) if out else None


class NoResponseTest(StopGuardCase):
    def test_blocks_no_response_requested(self):
        for message in ("No response requested.", "no response requested", "  No response requested.\n"):
            out = self.run_hook(message)
            self.assertEqual(out["decision"], "block", message)
            self.assertIn("continue the skill or task", out["reason"])

    def test_allows_a_second_stop_in_the_chain(self):
        self.assertIsNone(self.run_hook("No response requested.", active=True))

    def test_allows_normal_messages(self):
        for message in ("Done.", "No response requested. Waiting on CI.", "", None):
            self.assertIsNone(self.run_hook(message), message)

    def test_silent_in_a_subagent(self):
        self.assertIsNone(self.run_hook("No response requested.", agent_id="a1"))

    def test_garbage_stdin_is_silent(self):
        proc = subprocess.run([sys.executable, HOOK], input="not json", capture_output=True,
                              encoding="utf-8", env=self.env, timeout=20)
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, "", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)

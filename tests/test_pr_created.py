#!/usr/bin/env python3
"""
Tests for hooks/pr_created.py. Runs the hook as a subprocess with synthetic
PostToolUse input, the way Claude Code does.

    python -m unittest discover -s tests
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "pr_created.py")
URL = "https://github.com/owner/repo-name/pull/42"


class PrCreatedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.state = os.path.join(self.tmp.name, "ship")

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, command="gh pr create --title T --body B", stdout=URL + "\n", tool="Bash",
                 env=None, **extra):
        payload = {"session_id": "s1", "hook_event_name": "PostToolUse", "tool_name": tool,
                   "tool_input": {"command": command},
                   "tool_response": {"stdout": stdout, "stderr": "", "interrupted": False}, **extra}
        full_env = dict(os.environ, CLAUDE_SHIP_STATE_DIR=self.state)
        full_env.pop("CLAUDE_SHIP_DISABLE", None)
        full_env.update(env or {})
        proc = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                              encoding="utf-8", env=full_env, timeout=20)
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))
        out = proc.stdout.strip()
        return json.loads(out)["hookSpecificOutput"] if out else None

    def assertShip(self, out, number="42"):
        self.assertIsNotNone(out, "expected a ship nudge")
        self.assertEqual(out["hookEventName"], "PostToolUse")
        self.assertIn("PR #%s opened" % number, out["additionalContext"])
        self.assertIn("`ship #%s`" % number, out["additionalContext"])
        self.assertIn("pr-review-loop", out["additionalContext"])

    def test_nudges_after_a_pr_opens(self):
        self.assertShip(self.run_hook())

    def test_powershell_tool(self):
        self.assertShip(self.run_hook(tool="PowerShell"))

    def test_string_tool_response(self):
        payload_out = self.run_hook(stdout=None, tool_response=URL)  # response given as a bare string
        self.assertShip(payload_out)

    def test_first_url_wins(self):
        # gh pr create prints the new PR's URL; a later URL comes from something else in the command.
        out = self.run_hook(stdout="https://github.com/o/r/pull/8\nsee https://github.com/o/r/pull/7\n")
        self.assertShip(out, "8")

    def test_a_later_command_does_not_redirect_the_hand_off(self):
        out = self.run_hook(command="gh pr create --title T --body B; gh pr view 7 --json url",
                            stdout='https://github.com/o/r/pull/8\n{"url":"https://github.com/o/r/pull/7"}\n')
        self.assertShip(out, "8")

    def test_a_url_only_on_stderr_is_not_a_created_pr(self):
        # "a pull request for branch ... already exists: <url>" goes to stderr; nothing was created.
        self.assertIsNone(self.run_hook(stdout="", tool_response={
            "stdout": "", "stderr": "a pull request already exists:\nhttps://github.com/o/r/pull/9\n",
            "interrupted": False}))

    def test_silent_without_a_url(self):
        self.assertIsNone(self.run_hook(stdout="error: no commits\n"))

    def test_silent_for_a_draft(self):
        for flag in ("--draft", "-d", "--draft=true"):
            self.assertIsNone(self.run_hook(command="gh pr create %s --title T" % flag), flag)

    def test_draft_word_inside_quoted_body_is_not_a_draft(self):
        self.assertShip(self.run_hook(command='gh pr create --title T --body "use -d or --draft next time"'))

    def test_compound_command(self):
        self.assertShip(self.run_hook(command="git push -u origin HEAD && gh pr create --title T --body B"))

    def test_silent_when_the_command_is_not_gh_pr_create(self):
        self.assertIsNone(self.run_hook(command="gh pr view 42"))

    def test_silent_in_a_subagent(self):
        self.assertIsNone(self.run_hook(agent_id="a1", agent_type="implementer"))

    def test_silent_when_disabled(self):
        self.assertIsNone(self.run_hook(env={"CLAUDE_SHIP_DISABLE": "1"}))

    def test_silent_inside_ship(self):
        os.makedirs(self.state)
        open(os.path.join(self.state, "s1"), "w").close()
        self.assertIsNone(self.run_hook())

    def test_stale_ship_marker_is_ignored(self):
        os.makedirs(self.state)
        marker = os.path.join(self.state, "s1")
        open(marker, "w").close()
        old = time.time() - 2 * 3600
        os.utime(marker, (old, old))
        self.assertShip(self.run_hook())

    def test_garbage_stdin_is_silent(self):
        proc = subprocess.run([sys.executable, HOOK], input="not json", capture_output=True,
                              encoding="utf-8", timeout=20)
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, "", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)

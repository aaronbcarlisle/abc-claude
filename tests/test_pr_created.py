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
        for flag in ("--draft", "-d", "--draft=true", "--draft=1", "--draft=t", "--draft=T",
                     "--draft=TRUE", "--draft=True", '"--draft"', "--draft='true'"):
            self.assertIsNone(self.run_hook(command="gh pr create %s --title T" % flag), flag)

    def test_a_false_draft_value_is_not_a_draft(self):
        # gh parses --draft=<v> with strconv.ParseBool, so these open a ready PR.
        for flag in ("--draft=false", "--draft=0", "--draft=f", "--draft=F", "--draft=FALSE", "--draft=False"):
            self.assertShip(self.run_hook(command="gh pr create %s --title T" % flag))

    def test_a_quoted_separator_does_not_end_the_create_args(self):
        for body in ('"&&"', "';'", '"a | b"', '"(x)"'):
            self.assertIsNone(self.run_hook(command="gh pr create --body %s -d --title T" % body), body)
            self.assertShip(self.run_hook(command="gh pr create --body %s --title T" % body))
        self.assertShip(self.run_hook(command="gh pr create --title T; git branch -d old"))

    def test_draft_flag_of_another_command_is_not_a_draft(self):
        self.assertShip(self.run_hook(command="git branch -d old && gh pr create --title T --body B"))
        self.assertIsNone(self.run_hook(command="git push && gh pr create -d --title T"))

    def test_an_unquoted_newline_ends_the_create_args(self):
        # A newline separates commands like `;`, so the next line's `-d` isn't gh pr create's.
        for nl in ("\n", "\r\n"):
            with self.subTest(nl=nl):
                self.assertShip(self.run_hook(command="gh pr create --title T%sgit branch -d old" % nl))
                self.assertShip(self.run_hook(command="git branch -d old%sgh pr create --title T" % nl))
                self.assertIsNone(self.run_hook(command="git push%sgh pr create -d --title T" % nl))
        # A quoted newline stays part of its word.
        self.assertIsNone(self.run_hook(command='gh pr create --body "line 1\nline 2" -d --title T'))

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

    def mark(self, session="s1"):
        os.makedirs(self.state, exist_ok=True)
        marker = os.path.join(self.state, session)
        open(marker, "w").close()
        return marker

    def test_silent_inside_ship(self):
        self.mark()
        self.assertIsNone(self.run_hook())

    def test_consumes_the_ship_marker_when_the_pr_opens(self):
        # /ship can't pre-approve an rm of its own marker, so the hook deletes it once the PR exists.
        marker = self.mark()
        self.assertIsNone(self.run_hook())
        self.assertFalse(os.path.exists(marker))
        self.assertShip(self.run_hook())  # the next PR in this session hands off again

    def test_keeps_the_marker_when_no_pr_opened(self):
        marker = self.mark()
        for kwargs in ({"command": "gh pr create --draft --title T"},
                       {"stdout": "error: no commits\n"},
                       {"stdout": "", "tool_response": {"stdout": "", "stderr": "https://github.com/o/r/pull/9\n"}},
                       {"command": "gh pr view 42"},
                       {"agent_id": "a1"},
                       {"env": {"CLAUDE_SHIP_DISABLE": "1"}}):
            self.assertIsNone(self.run_hook(**kwargs), kwargs)
            self.assertTrue(os.path.exists(marker), kwargs)

    def test_keeps_other_sessions_markers(self):
        other = self.mark("s2")
        self.assertShip(self.run_hook())
        self.assertTrue(os.path.exists(other))

    def test_stale_ship_marker_is_ignored(self):
        marker = self.mark()
        old = time.time() - 2 * 3600
        os.utime(marker, (old, old))
        self.assertShip(self.run_hook())

    def test_a_malformed_session_id_never_deletes_anything(self):
        os.makedirs(self.state)
        outside = os.path.join(self.tmp.name, "x")
        open(outside, "w").close()
        sibling = self.mark("s1")
        for session in ("../x", "..\\x", os.path.join(self.state, "s1"), "s1\n", "s1/.", ""):
            with self.subTest(session=session):
                self.assertShip(self.run_hook(session_id=session))
                self.assertTrue(os.path.exists(outside))
                self.assertTrue(os.path.exists(sibling))

    def test_garbage_stdin_is_silent(self):
        proc = subprocess.run([sys.executable, HOOK], input="not json", capture_output=True,
                              encoding="utf-8", timeout=20)
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, "", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)

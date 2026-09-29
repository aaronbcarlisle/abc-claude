#!/usr/bin/env python3
"""
Tests for scripts/autopilot.ps1, run under pwsh with a scripted fake `claude`
first on PATH. Skipped where PowerShell 7 isn't installed.

    python -m unittest discover -s tests
"""

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

# `python -m unittest tests.test_autopilot` from the repo root doesn't put tests/ on sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stubs import git, git_env, install_stub  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "scripts", "autopilot.ps1")
FAKE = os.path.join(REPO, "tests", "fake_autopilot_claude.py")
PWSH = shutil.which("pwsh")
SUMMARY = r"^- \d{4}-\d{2}-\d{2} \d{2}:\d{2} \| autopilot \| %s \| runs %d \| \$%s \| [0-9a-f]{7}$"


def result(cost=0.5, error=False, subtype="success", text="ok"):
    return {"type": "result", "subtype": subtype, "is_error": error, "total_cost_usd": cost,
            "result": text, "num_turns": 3}


def run(events=None, commit=True, progress=None, autopilot=None, exit=0, rewrite=None):
    return {"events": [result()] if events is None else events, "commit": commit, "progress": progress,
            "rewrite": rewrite, "autopilot": autopilot, "exit": exit}


@unittest.skipUnless(PWSH, "needs PowerShell 7 (pwsh)")
class AutopilotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        t = self.tmp.name
        bin_dir = os.path.join(t, "bin")
        install_stub(bin_dir, "claude", FAKE)
        self.state = os.path.join(t, "state")
        self.sessions = os.path.join(t, "sessions")
        os.makedirs(self.sessions)
        self.runs_file = os.path.join(t, "runs.json")
        self.calls_file = os.path.join(t, "calls.jsonl")
        path = os.pathsep.join([bin_dir, os.path.dirname(sys.executable), os.environ.get("PATH", "")])
        self.env = git_env(t, PATH=path, FAKE_AUTOPILOT_RUNS=self.runs_file,
                           FAKE_AUTOPILOT_COUNT=os.path.join(t, "count"), FAKE_AUTOPILOT_LOG=self.calls_file,
                           AUTOPILOT_STATE_DIR=self.state, AUTOPILOT_MAX_SLEEP_SECONDS="0",
                           CLAUDE_SESSIONS_DIR=self.sessions)
        for key in ("CLAUDE_AUTOPILOT", "CLAUDE_AUTOPILOT_HANDOFF"):
            self.env.pop(key, None)
        self.repo = os.path.join(t, "repo")
        os.makedirs(self.repo)
        git(self.repo, self.env, "init", "-q", "-b", "main")
        with open(os.path.join(self.repo, "README.md"), "w", encoding="utf-8") as f:
            f.write("x\n")
        git(self.repo, self.env, "add", "README.md")
        git(self.repo, self.env, "commit", "-q", "-m", "init")
        self.handoff = os.path.join(self.repo, "SESSION_HANDOFF.md")
        self.write_handoff("continue")

    def tearDown(self):
        self.tmp.cleanup()

    def write_handoff(self, state, newline="\n"):
        text = "# Session Handoff — test\n%s\n## Summary\nx\n\n## Progress Log\n" % (
            "Autopilot: %s\n" % state if state else "")
        with open(self.handoff, "wb") as f:
            f.write(text.replace("\n", newline).encode("utf-8"))

    def script(self, *runs):
        with open(self.runs_file, "w", encoding="utf-8") as f:
            json.dump(list(runs), f)

    def autopilot(self, *args):
        return subprocess.run([PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", SCRIPT,
                               "-Repo", self.repo, *args], cwd=self.tmp.name, env=self.env,
                              capture_output=True, encoding="utf-8", errors="replace", timeout=300)

    def calls(self):
        if not os.path.exists(self.calls_file):
            return []
        with open(self.calls_file, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def last_line(self):
        with open(self.handoff, encoding="utf-8") as f:
            return f.read().splitlines()[-1]

    def log_text(self):
        logs = glob.glob(os.path.join(self.state, "*.log"))
        self.assertEqual(len(logs), 1, logs)
        with open(logs[0], encoding="utf-8") as f:
            return f.read()

    def test_stops_when_the_handoff_says_done(self):
        self.script(run(autopilot="done"))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 1)
        self.assertRegex(self.last_line(), SUMMARY % ("done", 1, r"0\.50"))

    def test_passes_the_headless_flags_and_environment(self):
        self.script(run(autopilot="done"))
        self.autopilot()
        call = self.calls()[0]
        self.assertEqual(call["argv"], ["-p", "/resume autopilot", "--permission-mode", "auto",
                                        "--model", "opus", "--max-turns", "200", "--max-budget-usd", "25.00",
                                        "--output-format", "stream-json", "--verbose"])
        self.assertEqual(call["autopilot"], "1")
        self.assertTrue(os.path.samefile(call["handoff"], self.handoff))
        self.assertTrue(os.path.samefile(call["cwd"], self.repo))

    def test_owner_or_missing_line_starts_no_run(self):
        for state, status in (("owner", "owner"), (None, "no-autopilot-line")):
            self.write_handoff(state)
            self.script(run())
            proc = self.autopilot()
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(self.calls(), [])
            self.assertRegex(self.last_line(), SUMMARY % (status, 0, r"0\.00"))

    def test_max_runs(self):
        self.script(run(), run(), run())
        proc = self.autopilot("-MaxRuns", "2")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 2)
        self.assertRegex(self.last_line(), SUMMARY % ("max-runs", 2, r"1\.00"))

    def test_stalled_after_two_idle_runs(self):
        self.script(run(commit=False), run(commit=False), run(commit=False))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 2)
        self.assertRegex(self.last_line(), SUMMARY % ("stalled", 2, r"1\.00"))

    def test_a_progress_line_counts_as_progress(self):
        self.script(run(commit=False, progress="- one"), run(commit=False, progress="- two"),
                    run(commit=False, autopilot="done"))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 3)

    def test_a_rewritten_entry_counts_as_progress(self):
        # Same entry count before and after each run, different content: not stalled.
        with open(self.handoff, "a", encoding="utf-8") as f:
            f.write("- one\n")
        self.script(run(commit=False, rewrite=["- one", "- one (fixed)"]),
                    run(commit=False, rewrite=["- one (fixed)", "- one (fixed again)"]),
                    run(commit=False, autopilot="done"))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 3)
        self.assertRegex(self.last_line(), SUMMARY % ("done", 3, r"1\.50"))

    def test_budget_is_cumulative(self):
        self.script(run(events=[result(cost=20)]), run(events=[result(cost=6)]), run())
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        calls = self.calls()
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["argv"][calls[1]["argv"].index("--max-budget-usd") + 1], "5.00")
        self.assertRegex(self.last_line(), SUMMARY % ("budget", 2, r"26\.00"))

    def test_usage_limit_sleeps_until_the_reset(self):
        limit = {"type": "rate_limit_event",
                 "rate_limit_info": {"status": "rejected", "resetsAt": int(time.time()) + 3600}}
        self.script(run(events=[limit, result(cost=0, error=True, subtype="error_during_execution",
                                              text="usage limit reached")], commit=False),
                    run(autopilot="done"))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 2)
        self.assertRegex(self.log_text(), r"usage limit: reset in 36\d\d s, sleeping 0 s")

    def test_usage_limit_without_reset_time_uses_default(self):
        self.script(run(events=[result(cost=0, error=True, subtype="error_during_execution",
                                       text="Claude AI usage limit reached")], commit=False),
                    run(autopilot="done"))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("usage limit: reset in 1800 s, sleeping 0 s", self.log_text())

    def test_crash_without_result_stops_with_error(self):
        self.script(run(events=[], commit=False, exit=1))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 1)
        self.assertIn("| autopilot | error: claude exited 1 with no result |", self.last_line())

    def test_error_result_stops(self):
        self.script(run(events=[result(error=True, subtype="error_during_execution", text="boom")]))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("| autopilot | error: error_during_execution |", self.last_line())

    def test_max_turns_is_not_fatal(self):
        self.script(run(events=[result(error=True, subtype="error_max_turns")]), run(autopilot="done"))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 2)

    def test_live_session_refuses(self):
        with open(os.path.join(self.sessions, "1.json"), "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "cwd": self.repo}, f)
        self.script(run())
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertEqual(self.calls(), [])

    def test_missing_repo_refuses(self):
        proc = subprocess.run([PWSH, "-NoProfile", "-File", SCRIPT, "-Repo", os.path.join(self.tmp.name, "nope")],
                              env=self.env, capture_output=True, encoding="utf-8", timeout=120)
        self.assertEqual(proc.returncode, 2)

    def test_keeps_a_crlf_handoff(self):
        self.write_handoff("continue", newline="\r\n")
        self.script(run(autopilot="done"))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        with open(self.handoff, "rb") as f:
            data = f.read()
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        self.assertTrue(data.endswith(b"\r\n"), data[-80:])
        self.assertRegex(self.last_line(), SUMMARY % ("done", 1, r"0\.50"))

    def test_log_file_holds_the_stream(self):
        self.script(run(autopilot="done"))
        self.autopilot()
        text = self.log_text()
        self.assertIn('"type": "result"', text)
        self.assertIn("autopilot end:", text)

    # == Hostile cases ==========================================================

    # -- helpers ---------------------------------------------------------------

    def stdout_summary(self, proc, status, runs, cost):
        match = re.search(SUMMARY % (status, runs, cost), proc.stdout, re.M)
        self.assertIsNotNone(match, "no %s summary on stdout:\n%s\n%s" % (status, proc.stdout, proc.stderr))
        return match.group(0)

    def handoff_bytes(self):
        with open(self.handoff, "rb") as f:
            return f.read()

    def live_session(self, cwd):
        with open(os.path.join(self.sessions, "%d.json" % os.getpid()), "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "cwd": cwd}, f)

    def make_repo(self, path):
        os.makedirs(path)
        git(path, self.env, "init", "-q", "-b", "main")
        with open(os.path.join(path, "README.md"), "w", encoding="utf-8") as f:
            f.write("x\n")
        git(path, self.env, "add", "README.md")
        git(path, self.env, "commit", "-q", "-m", "init")
        self.repo = path
        self.handoff = os.path.join(path, "SESSION_HANDOFF.md")
        self.write_handoff("continue")

    def limit_event(self, status="rejected", resets_at=None):
        info = {"status": status}
        if resets_at is not None:
            info["resetsAt"] = resets_at
        return {"type": "rate_limit_event", "rate_limit_info": info}

    # -- G2: the handoff state wins over the idle count --------------------------

    def test_owner_after_an_idle_run_is_not_stalled(self):
        self.script(run(commit=False), run(commit=False, autopilot="owner"), run())
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 2)
        self.assertRegex(self.last_line(), SUMMARY % ("owner", 2, r"1\.00"))

    def test_idle_count_resets_after_progress(self):
        # idle, progress, idle, idle: stalled only after the 4th call.
        self.script(run(commit=False), run(commit=False, progress="- p"), run(commit=False),
                    run(commit=False), run(commit=False))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 4)
        self.assertRegex(self.last_line(), SUMMARY % ("stalled", 4, r"2\.00"))

    # -- G3: live sessions --------------------------------------------------------

    def test_live_session_beats_a_done_handoff_and_leaves_it_alone(self):
        self.write_handoff("done")
        before = self.handoff_bytes()
        self.live_session(self.repo)
        self.script(run())
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertEqual(self.calls(), [])
        self.stdout_summary(proc, "live-session", 0, r"0\.00")
        self.assertEqual(self.handoff_bytes(), before)

    def test_live_session_refusal_leaves_a_continue_handoff_alone(self):
        before = self.handoff_bytes()
        os.makedirs(os.path.join(self.repo, "sub"))
        self.live_session(os.path.join(self.repo, "sub"))
        self.script(run())
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.handoff_bytes(), before)
        self.assertIn("autopilot end:", self.log_text())

    def test_live_session_outside_the_repo_does_not_block(self):
        # A sibling sharing the repo's name as a prefix is still outside it.
        sibling = self.repo + "-other"
        os.makedirs(sibling)
        self.live_session(sibling)
        self.script(run(autopilot="done"))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 1)
        self.assertRegex(self.last_line(), SUMMARY % ("done", 1, r"0\.50"))

    # -- G1 and handoff error paths ---------------------------------------------

    def test_handoff_that_is_a_directory_is_a_status_failure(self):
        os.remove(self.handoff)
        os.makedirs(self.handoff)
        self.script(run())
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(self.calls(), [])
        self.assertIn("| autopilot | error: handoff status failed |", proc.stdout)
        self.assertTrue(os.path.isdir(self.handoff))

    def test_missing_handoff_is_not_created(self):
        os.remove(self.handoff)
        self.script(run())
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.calls(), [])
        self.stdout_summary(proc, "no-autopilot-line", 0, r"0\.00")
        self.assertFalse(os.path.exists(self.handoff))

    def test_non_utf8_handoff_starts_no_run_and_is_unchanged(self):
        data = b"# Session Handoff \xe9t\xe9\r\nAutopilot: continue\r\n\r\n## Progress Log\r\n- \xff old\r\n"
        with open(self.handoff, "wb") as f:
            f.write(data)
        self.script(run())
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.calls(), [])
        self.stdout_summary(proc, "no-autopilot-line", 0, r"0\.00")
        self.assertEqual(self.handoff_bytes(), data)

    # -- repo argument -------------------------------------------------------------

    def test_not_a_git_repo_refuses(self):
        plain = os.path.join(self.tmp.name, "plain")
        os.makedirs(plain)
        self.repo = plain
        self.handoff = os.path.join(plain, "SESSION_HANDOFF.md")
        self.write_handoff("continue")
        self.script(run())
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertEqual(self.calls(), [])

    def test_relative_repo_resolves_against_the_pwsh_cwd(self):
        self.script(run(autopilot="done"))
        proc = subprocess.run([PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", SCRIPT,
                               "-Repo", "repo"], cwd=self.tmp.name, env=self.env,
                              capture_output=True, encoding="utf-8", errors="replace", timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        calls = self.calls()
        self.assertEqual(len(calls), 1)
        self.assertTrue(os.path.samefile(calls[0]["cwd"], self.repo))
        self.assertTrue(os.path.isabs(calls[0]["handoff"]), calls[0]["handoff"])
        self.assertTrue(os.path.samefile(calls[0]["handoff"], self.handoff))

    def test_repo_path_with_space_and_brackets(self):
        self.make_repo(os.path.join(self.tmp.name, "my repo [x]"))
        self.script(run(autopilot="done"))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        calls = self.calls()
        self.assertEqual(len(calls), 1)
        self.assertTrue(os.path.samefile(calls[0]["cwd"], self.repo))
        self.assertTrue(os.path.samefile(calls[0]["handoff"], self.handoff))
        self.assertRegex(self.last_line(), SUMMARY % ("done", 1, r"0\.50"))
        self.assertIn("autopilot end:", self.log_text())

    def test_parent_autopilot_environment_is_overridden(self):
        self.env["CLAUDE_AUTOPILOT"] = "0"
        self.env["CLAUDE_AUTOPILOT_HANDOFF"] = os.path.join(self.tmp.name, "other", "SESSION_HANDOFF.md")
        self.script(run(autopilot="done"))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        call = self.calls()[0]
        self.assertEqual(call["autopilot"], "1")
        self.assertTrue(os.path.samefile(call["handoff"], self.handoff))

    # -- budget and run limits -------------------------------------------------------

    def test_zero_budget_starts_no_run(self):
        self.script(run())
        proc = self.autopilot("-BudgetUsd", "0")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(self.calls(), [])
        self.assertRegex(self.last_line(), SUMMARY % ("budget", 0, r"0\.00"))

    def test_fractional_budget_is_passed_with_two_decimals(self):
        self.script(run(autopilot="done"))
        proc = self.autopilot("-BudgetUsd", "12.5")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        argv = self.calls()[0]["argv"]
        self.assertEqual(argv[argv.index("--max-budget-usd") + 1], "12.50")

    def test_result_without_cost_adds_nothing(self):
        no_cost = result()
        del no_cost["total_cost_usd"]
        self.script(run(events=[no_cost]), run(autopilot="done"))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        calls = self.calls()
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["argv"][calls[1]["argv"].index("--max-budget-usd") + 1], "25.00")
        self.assertRegex(self.last_line(), SUMMARY % ("done", 2, r"0\.50"))

    def test_max_runs_zero_starts_no_run(self):
        self.script(run())
        proc = self.autopilot("-MaxRuns", "0")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.calls(), [])
        self.assertRegex(self.last_line(), SUMMARY % ("max-runs", 0, r"0\.00"))

    def test_max_budget_subtype_is_not_fatal(self):
        self.script(run(events=[result(error=True, subtype="error_max_budget_usd")]), run(autopilot="done"))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 2)
        self.assertRegex(self.last_line(), SUMMARY % ("done", 2, r"1\.00"))

    # -- usage limits ------------------------------------------------------------------

    def test_usage_limit_reset_in_the_past_waits_a_minute(self):
        limit = self.limit_event(resets_at=int(time.time()) - 3600)
        self.script(run(events=[limit, result(cost=0, error=True, subtype="error_during_execution",
                                              text="boom")], commit=False),
                    run(autopilot="done"))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 2)
        self.assertIn("usage limit: reset in 60 s, sleeping 0 s", self.log_text())

    def test_limit_event_with_no_result_continues(self):
        self.script(run(events=[self.limit_event()], commit=False, exit=1), run(autopilot="done"))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 2)
        self.assertIn("usage limit: reset in 1800 s, sleeping 0 s", self.log_text())
        self.assertRegex(self.last_line(), SUMMARY % ("done", 2, r"0\.50"))

    def test_repeated_limits_stop_at_max_runs_not_stalled(self):
        limited = run(events=[self.limit_event(resets_at=int(time.time()) + 100),
                              result(cost=0, error=True, subtype="error_during_execution",
                                     text="usage limit reached")], commit=False)
        self.script(limited, limited, limited, limited)
        proc = self.autopilot("-MaxRuns", "3")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 3)
        self.assertEqual(len(re.findall(r"usage limit: reset in \d+ s, sleeping 0 s", self.log_text())), 3)
        self.assertRegex(self.last_line(), SUMMARY % ("max-runs", 3, r"0\.00"))

    def test_allowed_limit_events_are_not_a_limit(self):
        self.script(run(events=[self.limit_event("allowed"), self.limit_event("allowed_warning"),
                                result()], autopilot="done"))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 1)
        self.assertNotIn("usage limit:", self.log_text())
        self.assertRegex(self.last_line(), SUMMARY % ("done", 1, r"0\.50"))

    def test_successful_result_mentioning_a_limit_is_not_a_limit(self):
        # Even alongside a rejected event: only an error (or missing) result can be a limit.
        self.script(run(events=[self.limit_event("rejected"),
                                result(text="Claude AI usage limit reached; rate limit hit")],
                        autopilot="done"))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 1)
        self.assertNotIn("usage limit:", self.log_text())
        self.assertRegex(self.last_line(), SUMMARY % ("done", 1, r"0\.50"))

    # -- stream robustness -------------------------------------------------------------

    def test_noise_and_stderr_are_logged_and_the_result_still_found(self):
        literal = """noise $HOME %PATH% \\u0041 `n "dq" 'sq' ... {not json"""
        huge = "H" * 100000
        noisy = run(events=[{"type": "system", "subtype": "init"}, result(cost=1.25)], autopilot="done")
        noisy["raw"] = ["plain text, not json", "", "{broken", literal, huge]
        noisy["stderr"] = ["warning: something on stderr", "error: another stderr line"]
        self.script(noisy)
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertRegex(self.last_line(), SUMMARY % ("done", 1, r"1\.25"))
        text = self.log_text()
        for line in ("plain text, not json", "{broken", literal, huge,
                     "warning: something on stderr", "error: another stderr line"):
            self.assertIn(line, text)

    def test_empty_stream_with_exit_zero_is_an_error(self):
        self.script(run(events=[], commit=False, exit=0))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 1)
        self.assertIn("| autopilot | error: claude exited 0 with no result |", self.last_line())

    def test_crashing_claude_traceback_is_logged(self):
        self.env["FAKE_AUTOPILOT_RUNS"] = os.path.join(self.tmp.name, "missing-runs.json")
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("| autopilot | error: claude exited 1 with no result |", self.last_line())
        self.assertRegex(self.last_line(), SUMMARY % (r"error: claude exited 1 with no result", 1, r"0\.00"))
        self.assertIn("Traceback", self.log_text())

    # -- G4 ----------------------------------------------------------------------------

    def test_claude_not_on_path_is_an_error(self):
        dirs = [os.path.dirname(sys.executable)]
        git_exe = shutil.which("git")
        if git_exe:
            dirs.append(os.path.dirname(git_exe))
        if os.name == "nt":
            dirs.append(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"))
        path = os.pathsep.join(dirs)
        if shutil.which("claude", path=path) is not None:
            self.skipTest("a claude binary is on the minimal PATH %r" % path)
        self.env["PATH"] = path
        self.script(run(autopilot="done"))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(self.calls(), [])
        self.assertIn("| autopilot | error: ", self.last_line())
        self.assertIn("| runs 1 |", self.last_line())
        self.stdout_summary(proc, r"error: [^|\r\n]+", 1, r"0\.00")

    # -- the end summary ---------------------------------------------------------------

    def test_summary_is_the_same_on_stdout_in_the_log_and_in_the_handoff(self):
        self.script(run(autopilot="done"))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        summary = self.stdout_summary(proc, "done", 1, r"0\.50")
        self.assertEqual(self.last_line(), summary)
        head = git(self.repo, self.env, "rev-parse", "HEAD")
        self.assertTrue(summary.endswith("| " + head[:7]), (summary, head))
        logs = glob.glob(os.path.join(self.state, "*.log"))
        self.assertEqual(len(logs), 1, logs)
        self.assertRegex(os.path.basename(logs[0]), r"^repo-\d{8}-\d{4}\.log$")
        text = self.log_text()
        self.assertTrue(text.startswith("autopilot start"), text[:200])
        self.assertIn("autopilot end: " + summary, text)

    # == Final-review fixes: H1 (bounded sleeps, live re-check), H2 (dirty tree) ======

    # -- helpers ---------------------------------------------------------------

    def limited_run(self, reset_in=None, cost=0):
        resets_at = None if reset_in is None else int(time.time()) + reset_in
        return run(events=[self.limit_event(resets_at=resets_at),
                           result(cost=cost, error=True, subtype="error_during_execution",
                                  text="usage limit reached")], commit=False)

    def timed_autopilot(self, *args):
        start = time.monotonic()
        proc = self.autopilot(*args)
        return proc, time.monotonic() - start

    def modify_readme(self):
        with open(os.path.join(self.repo, "README.md"), "a", encoding="utf-8") as f:
            f.write("owner's uncommitted edit\n")

    def wait_for_log(self, proc, text, timeout=120):
        # Polls the log for `text` while the autopilot runs; None if it exits or times out first.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and proc.poll() is None:
            for path in glob.glob(os.path.join(self.state, "*.log")):
                try:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        if text in f.read():
                            return path
                except OSError:
                    pass
            time.sleep(0.05)
        return None

    # -- H1: no sleep when no run can follow ----------------------------------------

    def test_limit_on_the_last_allowed_run_does_not_sleep(self):
        self.env["AUTOPILOT_MAX_SLEEP_SECONDS"] = "120"
        self.script(self.limited_run(3600), run(autopilot="done"))
        proc, elapsed = self.timed_autopilot("-MaxRuns", "1")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 1)
        self.assertRegex(self.log_text(), r"usage limit: reset in 36\d\d s, sleeping 0 s")
        self.assertLess(elapsed, 60)
        self.assertRegex(self.last_line(), SUMMARY % ("max-runs", 1, r"0\.00"))

    def test_limit_that_spends_the_budget_does_not_sleep(self):
        self.env["AUTOPILOT_MAX_SLEEP_SECONDS"] = "120"
        self.script(self.limited_run(3600, cost=1), run(autopilot="done"))
        proc, elapsed = self.timed_autopilot("-BudgetUsd", "1")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 1)
        self.assertRegex(self.log_text(), r"usage limit: reset in 36\d\d s, sleeping 0 s")
        self.assertLess(elapsed, 60)
        self.assertRegex(self.last_line(), SUMMARY % ("budget", 1, r"1\.00"))

    # -- H1: the 8-hour cap ------------------------------------------------------------

    def test_reset_over_the_cap_ends_as_usage_limit(self):
        self.env["AUTOPILOT_MAX_SLEEP_SECONDS"] = "120"
        self.script(self.limited_run(40000), run(autopilot="done"))
        proc, elapsed = self.timed_autopilot()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 1)
        text = self.log_text()
        match = re.search(r"usage limit: reset in (\d+) s, over the 28800 s cap", text)
        self.assertIsNotNone(match, text[-2000:])
        self.assertTrue(39900 <= int(match.group(1)) <= 40060, match.group(0))
        self.assertNotRegex(text, r"usage limit: reset in \d+ s, sleeping")
        self.assertLess(elapsed, 60)
        summary = self.stdout_summary(proc, "usage-limit", 1, r"0\.00")
        self.assertEqual(self.last_line(), summary)
        self.assertIn("autopilot end: " + summary, text)

    def test_reset_under_the_cap_continues_and_a_later_one_over_it_stops(self):
        self.script(self.limited_run(28000), self.limited_run(40000), run(autopilot="done"))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 2)
        text = self.log_text()
        slept = re.findall(r"usage limit: reset in (\d+) s, sleeping 0 s", text)
        self.assertEqual(len(slept), 1, text[-2000:])
        self.assertTrue(27900 <= int(slept[0]) <= 28060, slept)
        self.assertRegex(text, r"usage limit: reset in \d+ s, over the 28800 s cap")
        self.assertRegex(self.last_line(), SUMMARY % ("usage-limit", 2, r"0\.00"))

    # -- H1: the live session is re-checked before the handoff is read ---------------

    def test_session_opened_during_the_sleep_refuses_before_the_handoff_is_read(self):
        # The owner opens a session and sets `Autopilot: owner` while the autopilot sleeps
        # off a limit: it must refuse as live-session without reading or touching the handoff.
        self.env["AUTOPILOT_MAX_SLEEP_SECONDS"] = "15"
        self.script(self.limited_run(3600), run(autopilot="done"))
        proc = subprocess.Popen([PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", SCRIPT,
                                 "-Repo", self.repo], cwd=self.tmp.name, env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                encoding="utf-8", errors="replace")
        try:
            seen = self.wait_for_log(proc, "usage limit: reset in")
            if seen:
                self.live_session(self.repo)
                self.write_handoff("owner")
            before = self.handoff_bytes()
            stdout, stderr = proc.communicate(timeout=300)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
        self.assertIsNotNone(seen, "no usage-limit line in the log while it ran:\n%s\n%s" % (stdout, stderr))
        self.assertEqual(proc.returncode, 2, stdout + stderr)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(self.handoff_bytes(), before)
        self.assertRegex(stdout, re.compile(SUMMARY % ("live-session", 1, r"0\.00"), re.M))

    # -- H2: refuse a dirty tree at start ---------------------------------------------

    def test_modified_tracked_file_refuses(self):
        self.modify_readme()
        before = self.handoff_bytes()
        self.script(run(autopilot="done"))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertEqual(self.calls(), [])
        summary = self.stdout_summary(proc, "dirty-tree", 0, r"0\.00")
        self.assertEqual(self.handoff_bytes(), before)
        self.assertIn("autopilot end: " + summary, self.log_text())

    def test_dirty_tree_beats_a_done_handoff(self):
        # Checked before the first handoff read, so `done` can't end it first.
        self.write_handoff("done")
        self.modify_readme()
        before = self.handoff_bytes()
        self.script(run())
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertEqual(self.calls(), [])
        self.stdout_summary(proc, "dirty-tree", 0, r"0\.00")
        self.assertEqual(self.handoff_bytes(), before)

    def test_dirty_tree_with_no_handoff_does_not_create_one(self):
        os.remove(self.handoff)
        self.modify_readme()
        self.script(run())
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertEqual(self.calls(), [])
        self.stdout_summary(proc, "dirty-tree", 0, r"0\.00")
        self.assertFalse(os.path.exists(self.handoff))

    def test_staged_changes_refuse(self):
        def modify_and_stage():
            self.modify_readme()
            git(self.repo, self.env, "add", "README.md")

        def add_new_file():
            # A staged new file with a unicode, spaced name shows even with --untracked-files=no.
            name = "névé … file.txt"
            with open(os.path.join(self.repo, name), "w", encoding="utf-8") as f:
                f.write("new\n")
            git(self.repo, self.env, "add", "--", name)

        for name, stage in (("staged modification", modify_and_stage), ("staged new file", add_new_file)):
            with self.subTest(name):
                # Each case starts from a clean tree, a `continue` handoff and no calls.
                git(self.repo, self.env, "reset", "-q", "--hard")
                for path in (self.calls_file, self.env["FAKE_AUTOPILOT_COUNT"]):
                    if os.path.exists(path):
                        os.remove(path)
                self.write_handoff("continue")
                before = self.handoff_bytes()
                stage()
                self.script(run(autopilot="done"))
                proc = self.autopilot()
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertEqual(self.calls(), [])
                self.stdout_summary(proc, "dirty-tree", 0, r"0\.00")
                self.assertEqual(self.handoff_bytes(), before)

    def test_untracked_files_do_not_count_but_a_tracked_change_does(self):
        os.makedirs(os.path.join(self.repo, ".superpowers", "sdd"))
        for rel in ("notes.txt", os.path.join(".superpowers", "sdd", "ledger.md"), "ünïcode $HOME %PATH%.txt"):
            with open(os.path.join(self.repo, rel), "w", encoding="utf-8") as f:
                f.write("untracked\n")
        self.modify_readme()
        self.script(run(autopilot="done"))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertEqual(self.calls(), [])
        self.stdout_summary(proc, "dirty-tree", 0, r"0\.00")
        # Only the untracked files (and the untracked handoff) left: it runs.
        git(self.repo, self.env, "checkout", "-q", "--", "README.md")
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 1)
        self.stdout_summary(proc, "done", 1, r"0\.50")

    def test_a_tree_left_dirty_by_a_run_is_checked_only_at_the_next_start(self):
        dirty = run()
        dirty["dirty"] = True
        self.script(dirty, run(commit=False, autopilot="done"), run(autopilot="done"))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 2)
        self.stdout_summary(proc, "done", 2, r"1\.00")
        self.assertNotEqual(git(self.repo, self.env, "status", "--porcelain", "--untracked-files=no"), "")
        # The next night starts on the tree the last run left dirty: refused.
        self.write_handoff("continue")
        before = self.handoff_bytes()
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 2)
        self.stdout_summary(proc, "dirty-tree", 0, r"0\.00")
        self.assertEqual(self.handoff_bytes(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)

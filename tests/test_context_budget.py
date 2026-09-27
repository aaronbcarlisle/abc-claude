#!/usr/bin/env python3
"""
Tests for context_budget.py. Runs the hook as a subprocess, the way Claude Code
does, against synthetic transcripts in a temp dir.

    python -m unittest discover -s tests
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "context_budget.py")


def assistant(tokens, sidechain=False):
    """An assistant transcript entry whose usage adds up to `tokens`."""
    return {
        "type": "assistant",
        "isSidechain": sidechain,
        "message": {
            "role": "assistant",
            "usage": {
                "input_tokens": 2,
                "cache_creation_input_tokens": 1_000,
                "cache_read_input_tokens": tokens - 1_002,
                "output_tokens": 500,
            },
        },
    }


def user():
    return {"type": "user", "isSidechain": False, "message": {"role": "user", "content": "x"}}


class ContextBudgetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.transcript = os.path.join(self.tmp.name, "session.jsonl")
        self.state_dir = os.path.join(self.tmp.name, "state")
        self.entries = []

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, *entries):
        self.entries.extend(entries)
        with open(self.transcript, "w", encoding="utf-8") as f:
            for e in self.entries:
                f.write(json.dumps(e) + "\n")

    def run_hook(self, session="s1", **extra):
        payload = {
            "session_id": session,
            "transcript_path": self.transcript,
            "cwd": self.tmp.name,
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            **extra,
        }
        env = dict(os.environ, CONTEXT_BUDGET_STATE_DIR=self.state_dir)
        proc = subprocess.run(
            [sys.executable, HOOK], input=json.dumps(payload, ensure_ascii=False), capture_output=True,
            text=True, encoding="utf-8", env=env, timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "")
        out = proc.stdout.strip()
        return json.loads(out)["hookSpecificOutput"] if out else None

    def assertNudge(self, out, tokens_text):
        self.assertIsNotNone(out, "expected a nudge")
        self.assertEqual(out["hookEventName"], "PostToolUse")
        text = out["additionalContext"]
        self.assertIn(f"Context is at {tokens_text} tokens", text)
        for phrase in ("/handoff", "/clear", "/resume", "subagents"):
            self.assertIn(phrase, text)

    def test_silent_below_250k(self):
        self.add(user(), assistant(249_000))
        self.assertIsNone(self.run_hook())

    def test_nudges_once_on_crossing_250k(self):
        self.add(user(), assistant(262_000))
        self.assertNudge(self.run_hook(), "262k")
        self.add(user(), assistant(270_000))
        self.assertIsNone(self.run_hook())

    def test_nudges_again_on_crossing_400k_then_stays_silent(self):
        self.add(assistant(260_000))
        self.assertNudge(self.run_hook(), "260k")
        self.add(assistant(401_000))
        self.assertNudge(self.run_hook(), "401k")
        self.add(assistant(450_000))
        self.assertIsNone(self.run_hook())

    def test_jump_past_both_levels_nudges_once(self):
        self.add(assistant(450_000))
        self.assertNudge(self.run_hook(), "450k")
        self.add(assistant(460_000))
        self.assertIsNone(self.run_hook())

    def test_levels_are_tracked_per_session(self):
        self.add(assistant(300_000))
        self.assertNudge(self.run_hook(session="a"), "300k")
        self.assertNudge(self.run_hook(session="b"), "300k")

    def test_silent_in_subagent_and_does_not_consume_the_level(self):
        self.add(assistant(300_000))
        self.assertIsNone(self.run_hook(agent_id="abc123", agent_type="implementer"))
        self.assertNudge(self.run_hook(), "300k")

    def test_ignores_sidechain_entries(self):
        self.add(assistant(100_000), assistant(300_000, sidechain=True))
        self.assertIsNone(self.run_hook())

    def test_skips_trailing_entries_without_usage(self):
        synthetic = assistant(0)
        synthetic["message"]["usage"] = {"input_tokens": 0, "output_tokens": 0}
        self.add(assistant(300_000), user(), synthetic, {"type": "system"})
        self.assertNudge(self.run_hook(), "300k")

    def test_rearms_after_context_drops_below_a_level(self):
        self.add(assistant(260_000))
        self.assertNudge(self.run_hook(), "260k")
        self.add(assistant(80_000))  # e.g. after /compact
        self.assertIsNone(self.run_hook())
        self.add(assistant(255_000))
        self.assertNudge(self.run_hook(), "255k")

    def test_parallel_calls_nudge_once(self):
        self.add(assistant(300_000))
        env = dict(os.environ, CONTEXT_BUDGET_STATE_DIR=self.state_dir)
        payload = json.dumps({"session_id": "par", "transcript_path": self.transcript})
        for trial in range(10):
            shutil.rmtree(self.state_dir, ignore_errors=True)
            procs = [subprocess.Popen([sys.executable, HOOK], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, text=True, encoding="utf-8", env=env)
                     for _ in range(12)]
            outs = [p.communicate(payload, timeout=20)[0] for p in procs]
            self.assertEqual(sum(bool(o.strip()) for o in outs), 1, f"trial {trial}")

    def test_prune_failure_does_not_block_the_nudge(self):
        stale = os.path.join(self.state_dir, "stale-dir")
        os.makedirs(stale)
        os.utime(stale, (0, 0))
        self.add(assistant(300_000))
        self.assertNudge(self.run_hook(), "300k")

    def test_skips_non_object_lines(self):
        self.add(assistant(300_000), [1, 2], 42)
        self.assertNudge(self.run_hook(), "300k")

    def test_ignores_a_partial_trailing_line(self):
        self.add(assistant(300_000))
        with open(self.transcript, "a", encoding="utf-8") as f:
            f.write('{"type": "assistant", "message": {"usage": {"input_tok')
        self.assertNudge(self.run_hook(), "300k")

    def test_non_ascii_transcript_path(self):
        self.transcript = os.path.join(self.tmp.name, "sesión-日本.jsonl")
        self.add(assistant(300_000))
        self.assertNudge(self.run_hook(), "300k")

    def test_large_transcript(self):
        filler = user()
        filler["message"]["content"] = "y" * 5_000
        self.add(assistant(500_000), *([filler] * 400), assistant(300_000))
        self.assertNudge(self.run_hook(), "300k")

    def test_widens_the_tail_past_a_huge_trailing_tool_result(self):
        huge = user()
        huge["message"]["content"] = "z" * 600_000
        self.add(assistant(300_000), huge)
        self.assertNudge(self.run_hook(), "300k")

    def test_missing_transcript_is_silent(self):
        self.transcript = os.path.join(self.tmp.name, "nope.jsonl")
        self.assertIsNone(self.run_hook())

    def test_garbage_stdin_is_silent(self):
        proc = subprocess.run(
            [sys.executable, HOOK], input="not json", capture_output=True, text=True, encoding="utf-8",
            env=dict(os.environ, CONTEXT_BUDGET_STATE_DIR=self.state_dir), timeout=20,
        )
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, "", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)

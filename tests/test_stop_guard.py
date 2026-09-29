#!/usr/bin/env python3
"""
Tests for hooks/stop_guard.py. Runs the hook as a subprocess with synthetic
Stop input.

    python -m unittest discover -s tests
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

# `python -m unittest tests.test_stop_guard` from the repo root doesn't put tests/ on sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stubs import git, git_env  # noqa: E402
from stubs import install_stub  # noqa: E402

HOOK =os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "stop_guard.py")


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


class AutopilotStopTest(StopGuardCase):
    def setUp(self):
        super().setUp()
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(self.repo)
        self.env = git_env(self.tmp.name, STOP_GUARD_STATE_DIR=self.env["STOP_GUARD_STATE_DIR"])
        for key in ("CLAUDE_AUTOPILOT", "CLAUDE_AUTOPILOT_HANDOFF"):
            self.env.pop(key, None)
        git(self.repo, self.env, "init", "-q", "-b", "main")
        self.commit("one")
        self.handoff = os.path.join(self.repo, "SESSION_HANDOFF.md")
        self.set_state("continue")
        self.env.update(CLAUDE_AUTOPILOT="1", CLAUDE_AUTOPILOT_HANDOFF=self.handoff)

    def commit(self, text):
        with open(os.path.join(self.repo, "work.txt"), "a", encoding="utf-8") as f:
            f.write(text + "\n")
        git(self.repo, self.env, "add", "work.txt")
        git(self.repo, self.env, "commit", "-q", "-m", text)

    def set_state(self, state):
        with open(self.handoff, "w", encoding="utf-8", newline="\n") as f:
            f.write("# Session Handoff\n%s\n## Progress Log\n" % ("Autopilot: %s\n" % state if state else ""))

    def assertBlocks(self, out):
        self.assertIsNotNone(out, "expected a block")
        self.assertEqual(out["decision"], "block")
        self.assertIn("Autopilot: the handoff says continue", out["reason"])

    def test_blocks_while_the_handoff_says_continue(self):
        self.assertBlocks(self.run_hook())

    def test_allows_the_stop_when_nothing_changed_since_the_last_block(self):
        self.assertBlocks(self.run_hook())
        self.assertIsNone(self.run_hook(active=True))

    def test_blocks_again_after_a_commit(self):
        self.assertBlocks(self.run_hook())
        self.commit("two")
        self.assertBlocks(self.run_hook(active=True))

    def test_a_handoff_edit_counts_as_progress(self):
        self.assertBlocks(self.run_hook())
        with open(self.handoff, "a", encoding="utf-8") as f:
            f.write("- progress\n")
        later = os.path.getmtime(self.handoff) + 5
        os.utime(self.handoff, (later, later))
        self.assertBlocks(self.run_hook(active=True))

    def test_state_is_per_session(self):
        self.assertBlocks(self.run_hook(session_id="a"))
        self.assertBlocks(self.run_hook(session_id="b"))

    def test_allows_on_owner_done_or_no_line(self):
        for state in ("owner", "done", None):
            self.set_state(state)
            self.assertIsNone(self.run_hook(), state)

    def test_needs_the_autopilot_environment(self):
        del self.env["CLAUDE_AUTOPILOT"]
        self.assertIsNone(self.run_hook())

    def test_missing_handoff_allows(self):
        self.env["CLAUDE_AUTOPILOT_HANDOFF"] = os.path.join(self.repo, "nope.md")
        self.assertIsNone(self.run_hook())

    def test_no_response_still_wins(self):
        out = self.run_hook("No response requested.")
        self.assertIn("continue the skill or task", out["reason"])


class AutopilotHostileTest(StopGuardCase):
    """Hostile inputs for the autopilot branch.

    Each test that checks a stop is allowed first checks that a control case
    blocks, so none of them can pass against a hook without the branch.
    """

    commit = AutopilotStopTest.commit
    set_state = AutopilotStopTest.set_state
    assertBlocks = AutopilotStopTest.assertBlocks

    def setUp(self):
        super().setUp()
        self.env = git_env(self.tmp.name, STOP_GUARD_STATE_DIR=self.env["STOP_GUARD_STATE_DIR"])
        for key in ("CLAUDE_AUTOPILOT", "CLAUDE_AUTOPILOT_HANDOFF"):
            self.env.pop(key, None)
        self.use_repo(os.path.join(self.tmp.name, "repo"))
        self.env["CLAUDE_AUTOPILOT"] = "1"

    # -- helpers -------------------------------------------------------------

    def use_repo(self, path, handoff_dir=None, commit=True):
        """git-init `path`, optionally commit, and point the handoff at a 'continue' file in it."""
        os.makedirs(path)
        git(path, self.env, "init", "-q", "-b", "main")
        self.repo = path
        if commit:
            self.commit("one")
        folder = handoff_dir or path
        os.makedirs(folder, exist_ok=True)
        self.use_handoff(os.path.join(folder, "SESSION_HANDOFF.md"))

    def use_handoff(self, path):
        self.handoff = path
        self.set_state("continue")
        self.env["CLAUDE_AUTOPILOT_HANDOFF"] = path

    def write_handoff(self, data):
        with open(self.handoff, "wb") as f:
            f.write(data)

    def touch_handoff(self, delta):
        when = os.path.getmtime(self.handoff) + delta
        os.utime(self.handoff, (when, when))

    def state_files(self):
        folder = self.env["STOP_GUARD_STATE_DIR"]
        return [os.path.join(folder, name) for name in os.listdir(folder)] if os.path.isdir(folder) else []

    def payload(self, message="Done.", active=False, **extra):
        return {"session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": active,
                "last_assistant_message": message, **extra}

    def run_payload(self, payload, env=None, hook=None, cwd=None):
        proc = subprocess.run([sys.executable, hook or HOOK], input=json.dumps(payload), capture_output=True,
                              encoding="utf-8", env=self.env if env is None else env, cwd=cwd, timeout=20)
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))
        out = proc.stdout.strip()
        return json.loads(out) if out else None

    def snapshot(self, *skip):
        """Every path under the temp dir, relative, leaving out `skip` and everything below them."""
        skip = {os.path.normcase(os.path.abspath(p)) for p in skip}
        seen = set()
        for root, dirs, files in os.walk(self.tmp.name):
            dirs[:] = [d for d in dirs if os.path.normcase(os.path.join(root, d)) not in skip]
            for name in dirs + files:
                seen.add(os.path.relpath(os.path.join(root, name), self.tmp.name))
        return seen

    def lone_hook(self, name, helper=None):
        """A copy of the hook in <tmp>/<name>/hooks/, with <tmp>/<name>/scripts/handoff_state.py only if `helper`."""
        root = os.path.join(self.tmp.name, name)
        os.makedirs(os.path.join(root, "hooks"))
        hook = shutil.copy(HOOK, os.path.join(root, "hooks", "stop_guard.py"))
        if helper is not None:
            os.makedirs(os.path.join(root, "scripts"))
            with open(os.path.join(root, "scripts", "handoff_state.py"), "w", encoding="utf-8", newline="\n") as f:
                f.write(helper)
        return hook

    def lone_env(self):
        """self.env without PYTHONPATH and with a temp home, so no installed helper can be found."""
        env = dict(self.env)
        env.pop("PYTHONPATH", None)
        home = os.path.join(self.tmp.name, "home")
        os.makedirs(home, exist_ok=True)
        env.update(HOME=home, USERPROFILE=home)
        return env

    def assertGuardsNoResponse(self, hook, env):
        cwd = self.tmp.name
        out = self.run_payload(self.payload("No response requested."), env=env, hook=hook, cwd=cwd)
        self.assertIsNotNone(out, "expected the 'No response requested' block")
        self.assertEqual(out["decision"], "block")
        self.assertIn("continue the skill or task", out["reason"])
        self.assertIsNone(self.run_payload(self.payload("No response requested.", active=True),
                                           env=env, hook=hook, cwd=cwd))
        self.assertIsNone(self.run_payload(self.payload("Done."), env=env, hook=hook, cwd=cwd))

    # -- CLAUDE_AUTOPILOT ----------------------------------------------------

    def test_autopilot_must_be_exactly_1(self):
        self.assertBlocks(self.run_hook(session_id="control"))
        for i, value in enumerate(("0", "true", "TRUE", "yes", " 1", "1 ", "01", "", "2", "1.0")):
            self.env["CLAUDE_AUTOPILOT"] = value
            self.assertIsNone(self.run_hook(session_id="v%d" % i), repr(value))

    # -- the handoff's Autopilot: line ---------------------------------------

    def test_header_value_is_case_insensitive(self):
        for i, value in enumerate(("CONTINUE", "Continue", "cOnTiNuE")):
            self.set_state(value)
            self.assertBlocks(self.run_hook(session_id="c%d" % i))

    def test_bom_crlf_and_unicode_handoffs_block(self):
        cases = (b"\xef\xbb\xbfAutopilot: continue\r\n## Progress Log\r\n",
                 b"\xef\xbb\xbf# Session Handoff\r\nAutopilot: continue\r\n\r\n## Progress Log\r\n",
                 b"# Session Handoff\r\nAutopilot: continue\r\n## Progress Log\r\n- a\r\n",
                 "# Session Handoff \u2014 \u2713 \u65e5\u672c\nAutopilot: continue\n## Progress Log\n- \u2713\n"
                 .encode("utf-8"))
        for i, data in enumerate(cases):
            self.write_handoff(data)
            self.assertBlocks(self.run_hook(session_id="b%d" % i))

    def test_non_utf8_handoff_allows(self):
        self.assertBlocks(self.run_hook(session_id="control"))
        cases = (b"# Session Handoff\nAutopilot: continue\n\n## Progress Log\n- caf\xe9\n",
                 b"# Session Handoff\nAutopilot: continue\n\xff\xfe\x00\n## Progress Log\n",
                 b"\xff\xfe" + "Autopilot: continue\r\n## Progress Log\r\n".encode("utf-16-le"))
        for i, data in enumerate(cases):
            self.write_handoff(data)
            self.assertIsNone(self.run_hook(session_id="n%d" % i), data[:40])

    def test_invalid_or_misplaced_values_allow(self):
        self.assertBlocks(self.run_hook(session_id="control"))
        cases = ("# Session Handoff\nAutopilot: continuee\n## Progress Log\n",
                 "# Session Handoff\nAutopilot: yes\n## Progress Log\n",
                 "# Session Handoff\nAutopilot: continue please\n## Progress Log\n",
                 "# Session Handoff\nAutopilot: \"continue\"\n## Progress Log\n",
                 "# Session Handoff\nAutopilot: \\continue\n## Progress Log\n",
                 "# Session Handoff\nAutopilot:\n## Progress Log\n",
                 "# Session Handoff\n## Progress Log\nAutopilot: continue\n",
                 "# Session Handoff\n## Notes\nAutopilot: continue\n## Progress Log\n",
                 "")
        for i, text in enumerate(cases):
            self.write_handoff(text.encode("utf-8"))
            self.assertIsNone(self.run_hook(session_id="i%d" % i), text)

    def test_huge_handoff_reads_the_header_only(self):
        body = "# Session Handoff\nAutopilot: continue\n\n## Progress Log\n" + "Autopilot: done\n" * 200000
        self.write_handoff(body.encode("utf-8"))
        self.assertBlocks(self.run_hook())

    def test_bad_handoff_paths_allow(self):
        self.assertBlocks(self.run_hook(session_id="control"))
        for i, path in enumerate(("", self.repo, os.path.join(self.repo, "missing", "SESSION_HANDOFF.md"))):
            self.env["CLAUDE_AUTOPILOT_HANDOFF"] = path
            self.assertIsNone(self.run_hook(session_id="p%d" % i), path)
        del self.env["CLAUDE_AUTOPILOT_HANDOFF"]
        self.assertIsNone(self.run_hook(session_id="unset"))

    def test_unicode_and_shell_characters_in_the_handoff_path(self):
        self.use_repo(os.path.join(self.tmp.name, "r\u00e9po \u2713 $HOME %PATH% 'q' \u65e5\u672c"))
        self.assertBlocks(self.run_hook())
        self.commit("two")
        self.assertBlocks(self.run_hook(active=True))
        self.assertIsNone(self.run_hook(active=True))

    # -- progress: HEAD and mtime --------------------------------------------

    def test_handoff_in_a_subdirectory_sees_the_repo_head(self):
        repo = os.path.join(self.tmp.name, "sub")
        self.use_repo(repo, handoff_dir=os.path.join(repo, "docs", "notes"))
        outside = self.tmp.name  # the hook's cwd is not the repo
        self.assertBlocks(self.run_payload(self.payload(), cwd=outside))
        self.commit("two")
        self.assertBlocks(self.run_payload(self.payload(active=True), cwd=outside))
        self.assertIsNone(self.run_payload(self.payload(active=True), cwd=outside))

    def test_handoff_outside_any_git_repo_uses_its_mtime(self):
        folder = os.path.join(self.tmp.name, "nogit")
        os.makedirs(folder)
        self.use_handoff(os.path.join(folder, "SESSION_HANDOFF.md"))
        self.assertBlocks(self.run_hook())
        self.assertIsNone(self.run_hook(active=True))
        self.assertBlocks(self.run_hook())
        self.touch_handoff(5)
        self.assertBlocks(self.run_hook(active=True))

    def test_unborn_head_then_a_first_commit_counts_as_progress(self):
        self.use_repo(os.path.join(self.tmp.name, "unborn"), commit=False)
        self.assertBlocks(self.run_hook())
        self.commit("first")
        self.assertBlocks(self.run_hook(active=True))

    def test_failing_git_falls_back_to_the_handoff_mtime(self):
        script = os.path.join(self.tmp.name, "failing_git.py")
        with open(script, "w", encoding="utf-8", newline="\n") as f:
            f.write("import sys\nsys.stderr.write('fatal: stub git failed\\n')\nsys.exit(128)\n")
        bin_dir = os.path.join(self.tmp.name, "bin")
        install_stub(bin_dir, "git", script)
        self.env["PATH"] = os.pathsep.join([bin_dir, os.path.dirname(sys.executable)])
        self.assertBlocks(self.run_hook())
        self.assertIsNone(self.run_hook(active=True))
        self.assertBlocks(self.run_hook())
        self.touch_handoff(5)
        self.assertBlocks(self.run_hook(active=True))

    def test_an_older_mtime_counts_as_a_change(self):
        self.assertBlocks(self.run_hook())
        self.touch_handoff(-100)
        self.assertBlocks(self.run_hook(active=True))

    # -- per-session state ---------------------------------------------------

    def test_unchanged_stop_clears_the_state_and_the_next_stop_blocks(self):
        self.assertBlocks(self.run_hook())
        self.assertIsNone(self.run_hook(active=True))
        self.assertBlocks(self.run_hook())
        self.assertIsNone(self.run_hook(active=True))
        self.assertBlocks(self.run_hook(active=True))

    def test_clearing_one_session_keeps_the_other(self):
        self.assertBlocks(self.run_hook(session_id="a"))
        self.assertBlocks(self.run_hook(session_id="b"))
        self.assertIsNone(self.run_hook(active=True, session_id="a"))
        self.assertIsNone(self.run_hook(active=True, session_id="b"))

    def test_progress_is_measured_from_each_sessions_own_block(self):
        self.assertBlocks(self.run_hook(session_id="a"))
        self.commit("two")
        self.assertBlocks(self.run_hook(session_id="b"))
        self.assertBlocks(self.run_hook(active=True, session_id="a"))
        self.assertIsNone(self.run_hook(active=True, session_id="b"))

    def test_state_file_is_named_from_the_sanitized_session_id(self):
        self.assertBlocks(self.run_hook(session_id="../x/ y"))
        names = os.listdir(self.env["STOP_GUARD_STATE_DIR"])
        self.assertEqual(len(names), 1, names)
        self.assertIn("___x__y", names[0])

    def test_hostile_session_ids_stay_in_the_state_dir(self):
        deep = os.path.join(self.tmp.name, "deep")
        os.makedirs(deep)
        state = os.path.join(deep, "state")
        self.env["STOP_GUARD_STATE_DIR"] = state
        git_dir = os.path.join(self.repo, ".git")
        before = self.snapshot(state, git_dir)
        for sid in ("../x", "..\\x", "a/b", "../../../../escape", "/abs", "C:\\abs", "a\u0000b",
                    "s\u00e9ance-\u65e5\u672c", ".", "..", "$HOME%PATH%"):
            self.assertBlocks(self.run_hook(session_id=sid))
            self.assertIsNone(self.run_hook(active=True, session_id=sid), repr(sid))
        for sid in ("", None, 7, ["x"], "x" * 5000):
            self.run_hook(session_id=sid)  # outcome unspecified; run_hook checks exit 0 and empty stderr
        self.run_payload({k: v for k, v in self.payload().items() if k != "session_id"})
        self.assertEqual(self.snapshot(state, git_dir), before)
        for name in os.listdir(state):
            self.assertTrue(os.path.isfile(os.path.join(state, name)), name)
            self.assertRegex(name, r"^[A-Za-z0-9_-]*(\.[A-Za-z0-9]+)*$")

    def test_corrupt_state_counts_as_no_previous_block(self):
        self.assertBlocks(self.run_hook())
        self.assertTrue(self.state_files(), "expected a state file after a block")
        for garbage in (b"", b"not json", b'{"head": ', b"\xff\xfe\x00\x01", b"[1, 2, 3]", b"null"):
            for path in self.state_files():
                with open(path, "wb") as f:
                    f.write(garbage)
            self.assertBlocks(self.run_hook(active=True))

    def test_unreadable_state_file_is_silent(self):
        self.assertBlocks(self.run_hook())
        files = self.state_files()
        self.assertTrue(files, "expected a state file after a block")
        for path in files:
            os.remove(path)
            os.makedirs(path)
        self.run_hook(active=True)  # outcome unspecified; run_hook checks exit 0 and empty stderr

    # -- subagents and the "No response requested" guard ---------------------

    def test_subagents_stay_silent_and_record_nothing(self):
        self.assertIsNone(self.run_hook(agent_id="a1"))
        self.assertIsNone(self.run_hook("No response requested.", agent_id="a1"))
        self.assertIsNone(self.run_hook(active=True, agent_id="a1"))
        self.assertBlocks(self.run_hook())

    def test_no_response_in_an_active_chain_gets_the_autopilot_block(self):
        self.assertBlocks(self.run_hook("No response requested.", active=True))

    def test_no_response_guard_survives_a_missing_helper(self):
        self.assertBlocks(self.run_hook(session_id="control"))
        self.assertGuardsNoResponse(self.lone_hook("lone"), self.lone_env())

    def test_no_response_guard_survives_a_broken_helper(self):
        self.assertBlocks(self.run_hook(session_id="control"))
        helpers = ("raise RuntimeError('boom')\n", "def (:\n")
        for i, helper in enumerate(helpers):
            with self.subTest(helper=helper):
                self.assertGuardsNoResponse(self.lone_hook("broken%d" % i, helper), self.lone_env())


if __name__ == "__main__":
    unittest.main(verbosity=2)

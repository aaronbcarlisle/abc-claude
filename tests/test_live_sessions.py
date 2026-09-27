#!/usr/bin/env python3
"""
Tests for scripts/live_sessions.py: PID liveness, session-file parsing, and
mapping sessions to the most specific checkout.

    python -m unittest discover -s tests
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "scripts", "live_sessions.py")
sys.path.insert(0, os.path.join(REPO, "scripts"))
import live_sessions as ls  # noqa: E402


def dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


class LiveSessionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = os.path.join(self.tmp.name, "sessions")
        self.root = os.path.join(self.tmp.name, "repo")
        self.nested = os.path.join(self.root, ".worktrees", "x")
        for d in (self.dir, self.nested):
            os.makedirs(d)
        self.count = 0

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, data):
        self.count += 1
        path = os.path.join(self.dir, "%d.json" % self.count)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))
        return os.path.basename(path)

    def test_own_process_is_alive(self):
        self.assertTrue(ls.pid_alive(os.getpid()))

    def test_exited_process_is_dead(self):
        self.assertFalse(ls.pid_alive(dead_pid()))

    def test_bad_pids_are_dead(self):
        for pid in (None, 0, -1, "12", True, 2 ** 40):
            self.assertFalse(ls.pid_alive(pid), pid)

    def test_windows_high_pids_reach_open_process(self):
        # Windows PIDs are DWORDs: 2**31 .. 2**32-1 are valid and must be checked, not
        # short-circuited as dead (cleanup would delete a live session's checkout).
        # Patched, so no real high PID is needed and nothing is signalled.
        seen = []
        with mock.patch.object(ls, "WINDOWS", True), \
                mock.patch.object(ls, "_windows_pid_alive", side_effect=lambda pid: seen.append(pid) or True), \
                mock.patch.object(ls.os, "kill") as kill:
            self.assertTrue(ls.pid_alive(2 ** 31))
            self.assertTrue(ls.pid_alive(2 ** 32 - 1))
            self.assertFalse(ls.pid_alive(2 ** 32))
            self.assertFalse(ls.pid_alive(2 ** 40))
        self.assertEqual(seen, [2 ** 31, 2 ** 32 - 1])
        kill.assert_not_called()

    def test_posix_rejects_pids_past_the_signed_bound(self):
        with mock.patch.object(ls, "WINDOWS", False), mock.patch.object(ls.os, "kill") as kill:
            self.assertFalse(ls.pid_alive(2 ** 31))
            self.assertFalse(ls.pid_alive(2 ** 32 - 1))
        kill.assert_not_called()

    def test_live_sessions_skips_unreadable_dead_and_malformed(self):
        good = self.write({"pid": os.getpid(), "cwd": self.root})
        self.write({"pid": dead_pid(), "cwd": self.root})
        self.write("{")
        self.write([1])
        self.write({"pid": os.getpid()})
        with open(os.path.join(self.dir, "x.key"), "w", encoding="utf-8") as f:
            f.write("not json")
        found = ls.live_sessions(self.dir)
        self.assertEqual([s["file"] for s in found], [good])
        self.assertEqual(found[0]["cwd"], self.root)

    def test_missing_directory_means_no_sessions(self):
        self.assertEqual(ls.live_sessions(os.path.join(self.tmp.name, "nope")), [])

    def test_owner_prefers_the_most_specific_checkout(self):
        checkouts = [self.root, self.nested]
        self.assertEqual(ls.owner(os.path.join(self.nested, "sub"), checkouts), self.nested)
        self.assertEqual(ls.owner(os.path.join(self.root, "src"), checkouts), self.root)
        self.assertIsNone(ls.owner(self.tmp.name, checkouts))

    def test_prefix_is_not_containment(self):
        self.assertFalse(ls.inside(self.root + "-other", self.root))
        self.assertTrue(ls.inside(self.root, self.root))

    @unittest.skipUnless(os.name == "nt", "Windows paths are case-insensitive")
    def test_windows_paths_compare_case_insensitively(self):
        self.assertTrue(ls.inside(self.root.upper(), self.root))

    def test_live_checkouts_maps_each_session_to_one_checkout(self):
        self.write({"pid": os.getpid(), "cwd": self.nested})
        self.write({"pid": os.getpid(), "cwd": os.path.join(self.root, "src")})
        live = ls.live_checkouts([self.root, self.nested], ls.live_sessions(self.dir))
        self.assertEqual(sorted(live), sorted([self.root, self.nested]))
        self.assertEqual(len(live[self.root]), 1)
        self.assertEqual(len(live[self.nested]), 1)

    def test_sessions_dir_falls_back_to_claude_config_dir(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": self.tmp.name}, clear=False):
            os.environ.pop("CLAUDE_SESSIONS_DIR", None)
            self.assertEqual(ls.sessions_dir(), os.path.join(self.tmp.name, "sessions"))

    def cli(self, *paths):
        env = dict(os.environ, CLAUDE_SESSIONS_DIR=self.dir)
        return subprocess.run([sys.executable, SCRIPT, *paths], capture_output=True, encoding="utf-8",
                              env=env, timeout=30)

    def test_cli_exit_codes(self):
        self.assertEqual(self.cli(self.root).returncode, 0)
        self.write({"pid": os.getpid(), "cwd": self.nested})
        proc = self.cli(self.root)
        self.assertEqual(proc.returncode, 1)
        self.assertIn(str(os.getpid()), proc.stdout)
        self.assertEqual(self.cli().returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

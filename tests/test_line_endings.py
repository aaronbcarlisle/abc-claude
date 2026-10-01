#!/usr/bin/env python3
"""
Tests for line_endings.py: after an Edit or Write it flags a file in an
`eol=lf` repo that now holds CRLF, and stays quiet otherwise.

    python -m unittest discover -s tests
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "line_endings.py")
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")


def git(*args, cwd):
    subprocess.run(["git"] + list(args), cwd=cwd, env=GIT_ENV, check=True, capture_output=True)


def run_hook(path, tool="Write"):
    payload = json.dumps({"tool_name": tool, "tool_input": {"file_path": path}})
    proc = subprocess.run([sys.executable, HOOK], input=payload.encode("utf-8"), capture_output=True,
                          timeout=30, env=GIT_ENV)
    return proc.returncode, proc.stderr.decode("utf-8")


class LineEndingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(self.repo)
        git("init", "-q", cwd=self.repo)
        self.put(".gitattributes", b"* text=auto eol=lf\n*.bat eol=crlf\n")

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, rel, data):
        path = os.path.join(self.repo, rel)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_new_crlf_file_in_an_lf_repo_is_flagged(self):
        code, err = run_hook(self.put("a.md", b"one\r\ntwo\r\n"))
        self.assertEqual(code, 2)
        self.assertIn("a.md", err)
        self.assertIn("CRLF", err)

    def test_mixed_endings_in_a_tracked_file_are_flagged(self):
        path = self.put("b.py", b"one\ntwo\n")
        git("add", "b.py", cwd=self.repo)
        self.put("b.py", b"one\ntwo\r\nthree\n")
        code, err = run_hook(path, tool="Edit")
        self.assertEqual(code, 2)
        self.assertIn("mixed", err)

    def test_all_crlf_tracked_file_is_left_alone(self):
        # A checkout from before .gitattributes: Edit keeps its endings and git normalizes on commit.
        path = self.put("c.txt", b"one\ntwo\n")
        git("add", "c.txt", cwd=self.repo)
        self.put("c.txt", b"one\r\ntwo\r\n")
        self.assertEqual(run_hook(path, tool="Edit"), (0, ""))

    def test_lf_files_and_crlf_attribute_files_pass(self):
        self.assertEqual(run_hook(self.put("d.md", b"one\ntwo\n")), (0, ""))
        self.assertEqual(run_hook(self.put("e.bat", b"one\r\ntwo\r\n")), (0, ""))

    def test_repo_without_an_eol_rule_is_not_checked(self):
        os.remove(os.path.join(self.repo, ".gitattributes"))
        self.assertEqual(run_hook(self.put("f.md", b"one\r\n")), (0, ""))

    def test_binary_and_outside_files_pass(self):
        self.assertEqual(run_hook(self.put("g.bin", b"\0\x01\r\n\0")), (0, ""))
        outside = os.path.join(self.tmp.name, "loose.md")
        with open(outside, "wb") as f:
            f.write(b"one\r\n")
        self.assertEqual(run_hook(outside), (0, ""))

    def test_bad_input_exits_zero(self):
        for payload in (b"not json", b"{}", json.dumps({"tool_input": {"file_path": "/no/such/file"}}).encode()):
            proc = subprocess.run([sys.executable, HOOK], input=payload, capture_output=True, timeout=30)
            self.assertEqual(proc.returncode, 0, payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)

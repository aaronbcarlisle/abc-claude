#!/usr/bin/env python3
"""
Tests for bash_guard.py. Runs the hook as a subprocess, the way Claude Code
does, against throwaway git repos in a temp dir.

    python -m unittest discover -s tests
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "bash_guard.py")
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")


def git(*args, cwd):
    subprocess.run(["git"] + list(args), cwd=cwd, env=GIT_ENV, check=True,
                   capture_output=True, encoding="utf-8")


def run_hook(command, cwd, tool="Bash"):
    payload = json.dumps({"tool_name": tool, "tool_input": {"command": command}, "cwd": cwd})
    proc = subprocess.run([sys.executable, HOOK], input=payload, capture_output=True,
                          encoding="utf-8", timeout=60)
    return proc.returncode, proc.stderr


def make_clone(root, name, default):
    """A clone of a one-commit repo whose default branch is `default`."""
    seed = os.path.join(root, name + "-seed")
    os.makedirs(seed)
    git("init", "-q", "-b", default, cwd=seed)
    git("commit", "-q", "--allow-empty", "-m", "init", cwd=seed)
    bare = os.path.join(root, name + ".git")
    git("clone", "-q", "--bare", seed, bare, cwd=root)
    clone = os.path.join(root, name)
    git("clone", "-q", bare, clone, cwd=root)
    return clone


class PushGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = cls.tmp.name
        cls.main_repo = make_clone(root, "mainrepo", "main")          # on main
        cls.feature_repo = make_clone(root, "featrepo", "main")
        git("checkout", "-q", "-b", "feat/x", cwd=cls.feature_repo)  # on a feature branch
        cls.master_repo = make_clone(root, "masterrepo", "master")    # default is master
        cls.other = os.path.join(root, "elsewhere")
        os.makedirs(cls.other)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def assertBlocked(self, command, cwd, tool="Bash"):
        code, err = run_hook(command, cwd, tool)
        self.assertEqual(code, 2, command)
        self.assertIn("default branch", err)

    def assertAllowed(self, command, cwd, tool="Bash"):
        code, err = run_hook(command, cwd, tool)
        self.assertEqual((code, err), (0, ""), command)

    def test_explicit_refspecs_to_default_are_blocked(self):
        for cmd in ("git push origin main", "git push origin HEAD:main", "git push origin +feat/x:main",
                    "git push origin :main", "git push origin feat/x:refs/heads/main", "git push --all origin",
                    "GIT_TRACE=1 git push origin main", "git add -A && git push origin main",
                    "git status\ngit push -u origin main"):
            self.assertBlocked(cmd, self.feature_repo)

    def test_feature_pushes_are_allowed(self):
        for cmd in ("git push -u origin feat/x", "git push", "git push origin HEAD",
                    "git push --force-with-lease origin feat/x",
                    'git push -u origin feat/x && gh pr create --base main --title "t"'):
            self.assertAllowed(cmd, self.feature_repo)

    def test_bare_push_while_on_default_is_blocked(self):
        for cmd in ("git push", "git push origin HEAD", "git push -u origin HEAD"):
            self.assertBlocked(cmd, self.main_repo)

    def test_directory_changes_are_followed(self):
        self.assertBlocked('cd "%s" && git push' % self.main_repo, self.other)
        self.assertBlocked('git -C "%s" push' % self.main_repo, self.other)
        self.assertBlocked('Set-Location "%s"; git push' % self.main_repo, self.other, tool="PowerShell")
        self.assertAllowed('cd "%s" && git push' % self.feature_repo, self.main_repo)

    def test_default_branch_comes_from_the_remote(self):
        self.assertBlocked("git push origin master", self.master_repo)
        self.assertAllowed("git push origin main", self.master_repo)

    def test_unknown_remote_head_falls_back_to_main_and_master(self):
        git("remote", "set-head", "origin", "-d", cwd=self.feature_repo)
        try:
            self.assertBlocked("git push origin master", self.feature_repo)
            self.assertBlocked("git push origin main", self.feature_repo)
            self.assertAllowed("git push origin feat/x", self.feature_repo)
        finally:
            git("remote", "set-head", "origin", "main", cwd=self.feature_repo)

    def test_quoted_text_never_trips_it(self):
        for cmd in ('echo "git push origin main"', 'git commit -m "never git push origin main"',
                    "python - <<'EOF'\nprint('git push origin main')\nEOF"):
            self.assertAllowed(cmd, self.main_repo)


class HeredocGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_heredoc_file_writes_are_blocked(self):
        for cmd in ("cat > notes.md <<'EOF'\n# Title\nbody\nEOF",
                    "cat <<EOF >> progress.md\n- Task 3: complete\nEOF",
                    "cat >>progress.md << 'EOF'\nline\nEOF",
                    "tee notes.md <<'EOF' >/dev/null\nbody\nEOF",
                    "cat <<'EOF' | tee -a log.md\nline\nEOF",
                    "mkdir -p d && cat > d/x.py <<-EOF\n\tprint(1)\n\tEOF"):
            code, err = run_hook(cmd, self.cwd)
            self.assertEqual(code, 2, cmd)
            self.assertIn("Write tool", err)

    def test_heredocs_that_feed_a_program_are_allowed(self):
        for cmd in ("python - <<'EOF'\nprint(1)\nEOF",
                    "python - <<'EOF' > out.txt\nprint(1)\nEOF",
                    "cat <<'EOF' | gh pr create --body-file -\nbody\nEOF",
                    "git commit -F - <<'EOF'\nsubject\nEOF",
                    "cat <<EOF\nshown only\nEOF",
                    "echo hi > out.txt",
                    "sort a.txt > b.txt"):
            self.assertEqual(run_hook(cmd, self.cwd), (0, ""), cmd)

    def test_powershell_herestring_file_writes_are_blocked(self):
        for cmd in ("@'\nline one\nline two\n'@ | Set-Content -Path notes.md",
                    "$t = @\"\nline\n\"@\nAdd-Content progress.md $t",
                    "@'\nline\n'@ > notes.md",
                    "[IO.File]::WriteAllText('x.md', @'\nline\n'@)"):
            code, err = run_hook(cmd, self.cwd, tool="PowerShell")
            self.assertEqual(code, 2, cmd)
            self.assertIn("Write tool", err)

    def test_powershell_herestrings_that_write_no_file_are_allowed(self):
        for cmd in ("$body = @'\nuse a > b here\n'@\ngh pr create --title t --body $body",
                    "git commit -m @'\nsubject\n'@",
                    "Set-Content -Path x.txt -Value 'one line'"):
            self.assertEqual(run_hook(cmd, self.cwd, tool="PowerShell"), (0, ""), cmd)


class FailOpenTest(unittest.TestCase):
    def test_bad_input_is_allowed(self):
        for payload in ("not json", "{}", json.dumps({"tool_input": {"command": ""}})):
            proc = subprocess.run([sys.executable, HOOK], input=payload, capture_output=True,
                                  encoding="utf-8", timeout=30)
            self.assertEqual(proc.returncode, 0, payload)

    def test_unbalanced_quotes_do_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run_hook('echo "unterminated', tmp)[0], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

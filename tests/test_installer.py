"""Installer scenario suite: every scenario runs against install.sh (Git Bash on
Windows, sh elsewhere) and against install.ps1 under Windows PowerShell 5.1 and
pwsh on Windows. Missing interpreters are skipped. No network, no real ~/.claude.
"""

import json
import re
import subprocess
import unittest

import installer_harness as h


class InstallerScenarios:
    INTERP = None

    def setUp(self):
        if h.interpreter_command(self.INTERP) is None:
            self.skipTest(self.INTERP + " is not available here")
        self.sb = h.Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.sb.make_remote()

    def install(self, *args, **kwargs):
        return self.sb.run(self.INTERP, *args, **kwargs)

    def assertOk(self, result):
        self.assertEqual(result.returncode, 0, result)

    def target_git(self, *args):
        return h.git("-C", str(self.sb.target), *args).strip()

    # --- task 4: skeleton ---------------------------------------------------
    def test_unknown_flag_exits_1(self):
        result = self.install("--bogus")
        self.assertEqual(result.returncode, 1, result)
        self.assertFalse(self.sb.target.exists())

    def test_help_flag_prints_usage_and_exits_0(self):
        result = self.install("--help")
        self.assertOk(result)
        self.assertIn("Usage: ", result.output)
        self.assertFalse(self.sb.target.exists())

    def test_reports_source_and_target(self):
        result = self.install("--yes")
        self.assertIn("[abc-claude] Installing from " + str(self.sb.remote) + ".", result.output)
        self.assertIn("[abc-claude] Target: ", result.output)
        self.assertNotIn("CLAUDE_CONFIG_DIR is", result.output)

    def test_warns_when_config_dir_is_not_home_claude(self):
        other = self.sb.root / "elsewhere"
        result = self.install("--yes", env=self.sb.env(CLAUDE_CONFIG_DIR=other))
        self.assertIn("the tracked settings.json commands assume ~/.claude", result.output)

    # --- task 5: bootstrap --------------------------------------------------
    def test_fresh_clone(self):
        result = self.install("--yes")
        self.assertOk(result)
        for rel in ("settings.json", "CLAUDE.md", "rules/dev-tooling.md", "install.sh", "templates/local.md"):
            self.assertTrue((self.sb.target / rel).is_file(), rel)
        self.assertEqual(self.target_git("config", "--get", "remote.origin.url"), str(self.sb.remote))
        self.assertEqual(self.target_git("config", "--get", "core.autocrlf"), "false")
        self.assertEqual(self.target_git("rev-parse", "--abbrev-ref", "HEAD"), "master")
        self.assertEqual(self.target_git("status", "--porcelain"), "")
        self.assertIn("Cloning into", result.output)

    def test_rerun_on_clean_repo_is_a_no_op(self):
        self.assertOk(self.install("--yes"))
        head = self.target_git("rev-parse", "HEAD")
        before = h.snapshot(self.sb.target)
        result = self.install("--yes")
        self.assertOk(result)
        self.assertEqual(self.target_git("rev-parse", "HEAD"), head)
        self.assertEqual(h.backups(self.sb.target), [])
        after = h.snapshot(self.sb.target)
        worktree = lambda snap: {k: v for k, v in snap.items() if not k.startswith(".git/")}
        self.assertEqual(worktree(after), worktree(before))
        self.assertIn("is an abc-claude checkout - updating it", result.output)

    def test_rerun_pulls_new_commits(self):
        self.assertOk(self.install("--yes"))
        self.sb.push_change({"CLAUDE.md": "# Updated rules\n"})
        self.assertOk(self.install("--yes"))
        self.assertEqual((self.sb.target / "CLAUDE.md").read_bytes(), b"# Updated rules\n")

    def test_rerun_with_local_edits_skips_pull(self):
        self.assertOk(self.install("--yes"))
        head = self.target_git("rev-parse", "HEAD")
        (self.sb.target / "CLAUDE.md").write_bytes(b"# My local edit\n")
        self.sb.push_change({"rules/dev-tooling.md": "# Upstream change\n"})
        result = self.install("--yes")
        self.assertOk(result)
        self.assertIn("have local changes - skipping the pull", result.output)
        self.assertEqual(self.target_git("rev-parse", "HEAD"), head)
        self.assertEqual((self.sb.target / "CLAUDE.md").read_bytes(), b"# My local edit\n")

    def test_missing_tracked_files_are_restored(self):
        self.assertOk(self.install("--yes"))
        (self.sb.target / "CLAUDE.md").unlink()
        (self.sb.target / "rules" / "dev-tooling.md").unlink()
        result = self.install("--yes")
        self.assertOk(result)
        self.assertIn("Restored 2 missing tracked file(s).", result.output)
        self.assertEqual((self.sb.target / "CLAUDE.md").read_bytes(), b"# Fixture global rules\n")
        self.assertTrue((self.sb.target / "rules" / "dev-tooling.md").is_file())

    def test_foreign_origin_exits_1_and_changes_nothing(self):
        self.sb.target.mkdir()
        h.git("init", "-q", "-b", "master", cwd=self.sb.target)
        h.git("remote", "add", "origin", "https://example.invalid/someone/other.git", cwd=self.sb.target)
        (self.sb.target / "settings.json").write_bytes(b'{"mine": true}\n')
        before = h.snapshot(self.sb.target)
        result = self.install("--yes")
        self.assertEqual(result.returncode, 1, result)
        self.assertIn("is a git repo with a different origin (https://example.invalid/someone/other.git)",
                      result.output)
        self.assertEqual(h.snapshot(self.sb.target), before)

    def test_repo_without_origin_exits_1(self):
        self.sb.target.mkdir()
        h.git("init", "-q", "-b", "master", cwd=self.sb.target)
        before = h.snapshot(self.sb.target)
        result = self.install("--yes")
        self.assertEqual(result.returncode, 1, result)
        self.assertIn("different origin (none)", result.output)
        self.assertEqual(h.snapshot(self.sb.target), before)

    def test_origin_spelled_differently_is_still_recognized(self):
        self.assertOk(self.install("--yes"))
        variant = str(self.sb.remote)[:-len(".git")].upper() + "/"   # no .git, other case, trailing slash
        h.git("-C", str(self.sb.target), "remote", "set-url", "origin", variant)
        result = self.install("--yes")
        self.assertOk(result)
        self.assertIn("is an abc-claude checkout - updating it", result.output)

    def test_unreachable_repo_exits_1_with_sign_in_hint(self):
        missing = self.sb.root / "no-such-repo.git"
        result = self.install("--yes", env=self.sb.env(ABC_CLAUDE_REPO=missing))
        self.assertEqual(result.returncode, 1, result)
        self.assertIn("Cannot reach", result.output)
        self.assertIn("gh auth login", result.output)
        self.assertFalse(self.sb.target.exists())

    def test_run_in_place_from_the_target(self):
        self.assertOk(self.install("--yes"))
        result = self.install("--yes", script_dir=self.sb.target)
        self.assertOk(result)
        self.assertIn("using it in place", result.output)

    def test_run_from_a_fresh_clone_elsewhere_into_missing_target(self):
        clone = self.sb.root / "clone"
        h.git("clone", "-q", str(self.sb.remote), str(clone))
        env = self.sb.env(ABC_CLAUDE_REPO=self.sb.root / "ignored.git")
        result = self.install("--yes", script_dir=clone, env=env)
        self.assertOk(result)
        self.assertIn("Installing from local checkout", result.output)
        self.assertEqual(self.target_git("config", "--get", "remote.origin.url"), str(self.sb.remote))


class ShTests(InstallerScenarios, unittest.TestCase):
    INTERP = "sh"


class WindowsPowerShellTests(InstallerScenarios, unittest.TestCase):
    INTERP = "powershell"


class PwshTests(InstallerScenarios, unittest.TestCase):
    INTERP = "pwsh"


class StaticChecks(unittest.TestCase):
    def test_install_ps1_is_ascii_lf_without_bom(self):
        data = (h.REPO_ROOT / "install.ps1").read_bytes()
        self.assertFalse(data.startswith(b"\xef\xbb\xbf"), "install.ps1 has a BOM")
        self.assertEqual([i for i, b in enumerate(data) if b > 0x7F], [], "non-ASCII bytes in install.ps1")
        self.assertNotIn(b"\r", data)
        self.assertIn(b"#Requires -Version 5", data)

    def test_install_sh_parses_and_is_lf(self):
        data = (h.REPO_ROOT / "install.sh").read_bytes()
        self.assertNotIn(b"\r", data)
        self.assertTrue(data.startswith(b"#!/usr/bin/env sh\n"))
        prefix = h.interpreter_command("sh")
        if prefix is None:
            self.skipTest("no sh interpreter")
        proc = subprocess.run(prefix + ["-n", str(h.REPO_ROOT / "install.sh").replace("\\", "/")],
                              capture_output=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

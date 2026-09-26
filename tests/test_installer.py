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

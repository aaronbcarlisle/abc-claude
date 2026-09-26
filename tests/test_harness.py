"""Self-tests for installer_harness.py and fake_claude.py."""

import json
import os
import subprocess
import unittest

import installer_harness as h


class HarnessTest(unittest.TestCase):
    def setUp(self):
        self.sb = h.Sandbox()
        self.addCleanup(self.sb.cleanup)

    def test_remote_has_fixture_and_real_files(self):
        self.sb.make_remote()
        files = h.git("--git-dir", str(self.sb.remote), "ls-tree", "-r", "--name-only", "master").split()
        for rel in (".gitignore", ".gitattributes", "install.sh", "install.ps1", "templates/local.md",
                    "settings.json", "CLAUDE.md", "status_line.py", "rules/dev-tooling.md"):
            self.assertIn(rel, files)

    def test_push_change_reaches_remote(self):
        self.sb.make_remote()
        self.sb.push_change({"CLAUDE.md": "# changed\n"})
        check = self.sb.root / "check"
        h.git("clone", "-q", str(self.sb.remote), str(check))
        self.assertEqual((check / "CLAUDE.md").read_bytes(), b"# changed\n")

    def test_env_isolates_home_and_config(self):
        env = self.sb.env()
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(self.sb.target))
        self.assertEqual(env["HOME"], str(self.sb.home))
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], str(self.sb.gitconfig))
        self.assertTrue(env["PATH"].startswith(str(self.sb.bin) + os.pathsep))
        self.assertNotIn("CLAUDE_CONFIG_DIR", self.sb.env(CLAUDE_CONFIG_DIR=None))

    def test_translate_flag(self):
        self.assertEqual(h.translate_flag("--yes", "sh"), "--yes")
        self.assertEqual(h.translate_flag("--yes", "pwsh"), "-Yes")
        self.assertEqual(h.translate_flag("--prune", "powershell"), "-Prune")

    def fake_call(self, *args):
        """Run the fake claude through the stub a shell would find."""
        env = self.sb.env()
        if h.IS_WINDOWS:
            argv = ["cmd", "/c", str(self.sb.bin / "claude.cmd"), *args]
        else:
            argv = [str(self.sb.bin / "claude"), *args]
        return subprocess.run(argv, capture_output=True, encoding="utf-8", env=env, timeout=60)

    def test_fake_claude_lists_installs_and_logs(self):
        self.sb.fake_claude(plugins=["alpha@claude-plugins-official"])
        listed = json.loads(self.fake_call("plugin", "list", "--json").stdout)
        self.assertEqual([p["id"] for p in listed], ["alpha@claude-plugins-official"])
        self.assertEqual(self.fake_call("plugin", "install", "beta@extra-market").returncode, 0)
        again = self.fake_call("plugin", "install", "beta@extra-market")
        self.assertEqual(again.returncode, 0)
        self.assertIn("is already installed", again.stdout)
        self.assertEqual(self.sb.claude_plugins(), ["alpha@claude-plugins-official", "beta@extra-market"])
        self.assertEqual(self.fake_call("plugin", "bogus").returncode, 1)
        self.assertEqual(self.sb.claude_calls(), ["plugin list --json", "plugin install beta@extra-market",
                                                  "plugin install beta@extra-market", "plugin bogus"])

    def test_fake_claude_sh_stub_runs_under_the_sh_interpreter(self):
        prefix = h.interpreter_command("sh")
        if prefix is None:
            self.skipTest("no sh interpreter")
        self.sb.fake_claude(marketplaces=["claude-plugins-official"])
        proc = subprocess.run(prefix + ["-c", "claude plugin marketplace list --json"], capture_output=True,
                              encoding="utf-8", env=self.sb.env(), timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual([m["name"] for m in json.loads(proc.stdout)], ["claude-plugins-official"])

    def test_install_failure_listed_in_fail(self):
        self.sb.fake_claude(fail=["beta@extra-market"])
        result = self.fake_call("plugin", "install", "beta@extra-market")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.sb.claude_plugins(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

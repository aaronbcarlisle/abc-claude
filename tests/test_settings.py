#!/usr/bin/env python3
"""
settings.json wiring: every hook script is registered, every registered hook
exists, and the command-scoped hooks carry the right `if` rules.

    python -m unittest discover -s tests
"""

import json
import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SettingsTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(REPO, "settings.json"), encoding="utf-8") as f:
            self.hooks = json.load(f)["hooks"]

    def handlers(self, event):
        return [(group.get("matcher"), h) for group in self.hooks.get(event, []) for h in group["hooks"]]

    def test_every_hook_script_is_registered(self):
        registered = json.dumps(self.hooks)
        for name in sorted(os.listdir(os.path.join(REPO, "hooks"))):
            if name.endswith(".py"):
                self.assertIn('/.claude/hooks/%s' % name, registered, name)

    def test_every_registered_hook_exists(self):
        for name in re.findall(r"/\.claude/hooks/([\w.-]+\.py)", json.dumps(self.hooks)):
            self.assertTrue(os.path.isfile(os.path.join(REPO, "hooks", name)), name)

    def test_pr_created_runs_only_after_gh_pr_create(self):
        found = [(m, h.get("if")) for m, h in self.handlers("PostToolUse") if "pr_created.py" in h["command"]]
        self.assertEqual(sorted(i for _, i in found), ["Bash(gh pr create *)", "PowerShell(gh pr create *)"])
        for matcher, _ in found:
            self.assertEqual(matcher, "Bash|PowerShell")

    def test_stop_guard_runs_on_stop(self):
        commands = [h["command"] for _, h in self.handlers("Stop")]
        self.assertTrue(any("stop_guard.py" in c for c in commands), commands)


if __name__ == "__main__":
    unittest.main(verbosity=2)

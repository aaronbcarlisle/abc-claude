"""Portability lint: tracked files must not hard-code a home directory or carry
token-like strings. docs/superpowers/ (plans and specs) and tests/fixtures/ are
exempt. Sample strings below are built by concatenation so this file passes its
own scan.
"""

import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXEMPT_PREFIXES = ("docs/superpowers/", "tests/fixtures/")

HOME_PATTERNS = (
    re.compile(r"(?i)\b[a-z]:[\\/]{1,2}users[\\/]{1,2}[a-z0-9_][\w.-]*"),   # Windows, either slash
    re.compile(r"(?i)(?<![\w.])/[a-z]/users/[a-z0-9_][\w.-]*"),             # Git Bash drive form
    re.compile(r"(?<![\w.])/home/[a-z0-9_][\w.-]*"),                        # Linux
    re.compile(r"(?<![\w.:/])/Users/[A-Za-z0-9_][\w.-]*"),                  # macOS
)
TOKEN_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{32,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def findings(text):
    """Every home-path or token-like match in `text`, as (kind, match)."""
    out = [("home path", m.group(0)) for p in HOME_PATTERNS for m in p.finditer(text)]
    out += [("token", m.group(0)) for p in TOKEN_PATTERNS for m in p.finditer(text)]
    return out


def repo_files():
    """Tracked files plus new files not ignored yet, relative, forward slashes."""
    proc = subprocess.run(["git", "-C", str(REPO), "ls-files", "--cached", "--others", "--exclude-standard"],
                          capture_output=True, encoding="utf-8", check=True)
    return sorted({line for line in proc.stdout.splitlines() if line})


class PatternTest(unittest.TestCase):
    def test_flags_home_paths(self):
        for sample in ("C:" + "\\Users\\bob\\x", "c:" + "/users/bob.smith/", "/c/Us" + "ers/bob",
                       "/ho" + "me/alice/.claude", "/Us" + "ers/carol/Library"):
            self.assertTrue(findings(sample), sample)

    def test_allows_generic_paths(self):
        for sample in ("C:" + "/Users/...", "/c" + "/Users/...", "$HOME/.claude/status_line.py",
                       "~/.claude", "C:\\Program Files\\ClaudeCode\\managed-settings.json",
                       "/Library/Application Support/ClaudeCode", "/etc/claude-code/managed-settings.json"):
            self.assertEqual(findings(sample), [], sample)

    def test_flags_tokens(self):
        for sample in ("gh" + "p_" + "a" * 36, "github" + "_pat_" + "B" * 30, "sk-" + "ant-" + "c" * 30,
                       "AK" + "IA" + "ABCDEFGHIJKLMNOP", "-----BEGIN " + "RSA PRIVATE KEY-----"):
            self.assertTrue(findings(sample), sample)


class RepoScanTest(unittest.TestCase):
    def test_tracked_files_are_portable(self):
        problems = []
        for rel in repo_files():
            if rel.startswith(EXEMPT_PREFIXES):
                continue
            path = REPO / rel
            if not path.is_file():
                continue
            data = path.read_bytes()
            if b"\0" in data:
                continue  # binary
            for kind, match in findings(data.decode("utf-8", errors="replace")):
                problems.append("%s: %s %r" % (rel, kind, match))
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

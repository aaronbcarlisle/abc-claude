#!/usr/bin/env python3
"""
Tests for scripts/handoff_state.py.

    python -m unittest discover -s tests
"""

import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "scripts", "handoff_state.py")
sys.path.insert(0, os.path.join(REPO, "scripts"))
import handoff_state as hs  # noqa: E402


class HandoffCase(unittest.TestCase):
    """Shared setUp/tearDown/write/read fixture for a SESSION_HANDOFF.md-shaped
    temp file. Task 10 subclasses this for its own state tests."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = os.path.join(self.tmp.name, "SESSION_HANDOFF.md")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text, newline="\n", bom=False):
        data = text.replace("\n", newline).encode("utf-8")
        with open(self.path, "wb") as f:
            f.write((b"\xef\xbb\xbf" if bom else b"") + data)

    def read(self):
        with open(self.path, "rb") as f:
            return f.read()


class AppendProgressTest(HandoffCase):
    def test_appends_after_the_last_entry(self):
        self.write("# H\n\n## Progress Log\n- a\n")
        hs.append_progress(self.path, "- b")
        self.assertEqual(self.read(), b"# H\n\n## Progress Log\n- a\n- b\n")

    def test_keeps_crlf(self):
        self.write("# H\n\n## Progress Log\n- a\n", newline="\r\n")
        hs.append_progress(self.path, "- b")
        self.assertEqual(self.read(), b"# H\r\n\r\n## Progress Log\r\n- a\r\n- b\r\n")

    def test_empty_section_gets_the_first_line(self):
        self.write("# H\n\n## Progress Log\n")
        hs.append_progress(self.path, "- b")
        self.assertEqual(self.read(), b"# H\n\n## Progress Log\n- b\n")

    def test_section_followed_by_another_section(self):
        self.write("## Progress Log\n- a\n\n## Notes\nn\n")
        hs.append_progress(self.path, "- b")
        self.assertEqual(self.read(), b"## Progress Log\n- a\n- b\n\n## Notes\nn\n")

    def test_missing_section_is_added_at_the_end(self):
        self.write("# H\n\nbody\n")
        hs.append_progress(self.path, "- b")
        self.assertEqual(self.read(), b"# H\n\nbody\n\n## Progress Log\n- b\n")

    def test_missing_file_is_created(self):
        hs.append_progress(self.path, "- b")
        self.assertEqual(self.read(), b"## Progress Log\n- b\n")

    def test_file_without_trailing_newline(self):
        self.write("## Progress Log\n- a")
        hs.append_progress(self.path, "- b")
        self.assertEqual(self.read(), b"## Progress Log\n- a\n- b\n")

    def test_line_breaks_inside_the_line_are_flattened(self):
        self.write("## Progress Log\n")
        hs.append_progress(self.path, "- a\r\nb\nc")
        self.assertEqual(self.read(), b"## Progress Log\n- a b c\n")

    def test_utf8_bom_and_non_ascii_are_kept(self):
        self.write("# Café\n\n## Progress Log\n", bom=True)
        hs.append_progress(self.path, "- feat/café-日本")
        expected = chr(0xFEFF) + "# Café\n\n## Progress Log\n- feat/café-日本\n"
        self.assertEqual(self.read(), expected.encode("utf-8"))

    def test_bom_file_starting_with_progress_log_gets_exactly_one_section(self):
        # F10: a leading BOM must not be mistaken for part of the first line's
        # text, which would stop "## Progress Log" matching PROGRESS and cause
        # a duplicate section to be appended at the end.
        self.write("## Progress Log\n- a\n", bom=True)
        hs.append_progress(self.path, "- b")
        content = self.read()
        expected = chr(0xFEFF) + "## Progress Log\n- a\n- b\n"
        self.assertEqual(content, expected.encode("utf-8"))
        self.assertEqual(content.decode("utf-8").count(hs.PROGRESS), 1)

    def test_cli_append(self):
        self.write("## Progress Log\n")
        proc = subprocess.run([sys.executable, SCRIPT, "append", self.path, "- $1.50 | ü"],
                              capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.read().decode("utf-8"), "## Progress Log\n- $1.50 | ü\n")

    def test_cli_usage_error(self):
        proc = subprocess.run([sys.executable, SCRIPT], capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

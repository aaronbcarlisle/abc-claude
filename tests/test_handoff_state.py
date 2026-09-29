#!/usr/bin/env python3
"""
Tests for scripts/handoff_state.py.

    python -m unittest discover -s tests
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

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


# The digest of empty normalized content: what a missing file, a missing or
# empty section and a non-UTF-8 file all hash as.
EMPTY_SIGNATURE = hashlib.sha256(b"").hexdigest()

# Latin-1 bytes that aren't valid UTF-8 (0xE9 starts a 3-byte sequence that
# "\n" can't continue).
NON_UTF8 = b"Autopilot: done\n# H caf\xe9\n\n## Progress Log\n- caf\xe9\n"


class StateTest(HandoffCase):
    def test_reads_each_state(self):
        for value, expected in (("continue", "continue"), ("owner", "owner"), ("done", "done"),
                                ("Continue", "continue")):
            self.write("# Session Handoff\nAutopilot: %s\n\n## Summary\n" % value)
            self.assertEqual(hs.autopilot_state(self.path), expected, value)

    def test_missing_or_invalid_line(self):
        for text in ("# H\n\n## Summary\n", "# H\nAutopilot: maybe\n",
                     "# H\nAutopilot: continue | owner | done\n"):
            self.write(text)
            self.assertIsNone(hs.autopilot_state(self.path), text)
        self.assertIsNone(hs.autopilot_state(os.path.join(self.tmp.name, "nope.md")))

    def test_only_the_header_counts(self):
        self.write("# H\n\n## Summary\nAutopilot: continue\n")
        self.assertIsNone(hs.autopilot_state(self.path))

    def test_crlf_and_bom(self):
        self.write("Autopilot: owner\n# H\n", newline="\r\n", bom=True)
        self.assertEqual(hs.autopilot_state(self.path), "owner")

    def signature(self, text, newline="\n"):
        self.write(text, newline=newline)
        return hs.progress_signature(self.path)

    def test_progress_signature_tracks_the_log_content(self):
        base = self.signature("# H\n\n## Progress Log\n- a\n- b\n\n## Next\n- c\n")
        self.assertRegex(base, r"^[0-9a-f]{64}$")
        # A rewritten entry changes the signature though the entry count stays 2.
        self.assertNotEqual(self.signature("# H\n\n## Progress Log\n- a\n- b (fixed)\n\n## Next\n- c\n"), base)
        self.assertNotEqual(self.signature("# H\n\n## Progress Log\n- a\n- b\n- c\n\n## Next\n- c\n"), base)
        # Text outside the section, line endings and blank lines don't.
        self.assertEqual(self.signature("# H2\n\n## Progress Log\n- a\n- b\n\n## Next\n- d\n"), base)
        self.assertEqual(self.signature("# H\n\n## Progress Log\n- a\n- b\n\n## Next\n- c\n", "\r\n"), base)
        self.assertEqual(self.signature("# H\n\n## Progress Log\n\n- a  \n- b\n\n\n## Next\n- c\n"), base)

    def test_progress_signature_of_a_missing_file_or_section(self):
        empty = self.signature("# H\n\n## Summary\n- a\n")
        self.assertEqual(hs.progress_signature(os.path.join(self.tmp.name, "nope.md")), empty)
        self.assertEqual(self.signature("# H\n\n## Progress Log\n"), empty)
        self.assertNotEqual(self.signature("# H\n\n## Progress Log\n- a\n"), empty)

    def test_cli_status(self):
        self.write("# H\nAutopilot: continue\n\n## Progress Log\n- a\n")
        proc = subprocess.run([sys.executable, SCRIPT, "status", self.path], capture_output=True,
                              encoding="utf-8", timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"exists": True, "autopilot": "continue",
                                                   "progress": hs.progress_signature(self.path)})
        missing = self.path + ".x"
        proc = subprocess.run([sys.executable, SCRIPT, "status", missing], capture_output=True,
                              encoding="utf-8", timeout=30)
        self.assertEqual(json.loads(proc.stdout), {"exists": False, "autopilot": None,
                                                   "progress": hs.progress_signature(missing)})

    # --- Hostile cases -------------------------------------------------

    def write_bytes(self, data):
        with open(self.path, "wb") as f:
            f.write(data)

    def status(self, path):
        proc = subprocess.run([sys.executable, SCRIPT, "status", path], capture_output=True,
                              encoding="utf-8", timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_state_value_case_is_ignored(self):
        for value, expected in (("DONE", "done"), ("Owner", "owner"), ("cOnTiNuE", "continue")):
            self.write("# H\nAutopilot: %s\n\n## Summary\n" % value)
            self.assertEqual(hs.autopilot_state(self.path), expected, value)

    def test_state_value_must_be_exact(self):
        for value in ("continued", "done.", "owner!", "continue owner", "", "none", "\"done\"",
                      "'owner'", "`continue`", "$done", "%done%", "done\\n", "\\u0064one",
                      "d\u043ene",  # Cyrillic o
                      "done\u2026", "\u00a0"):
            self.write("# H\nAutopilot: %s\n\n## Summary\n" % value)
            self.assertIsNone(hs.autopilot_state(self.path), repr(value))

    def test_first_autopilot_line_wins(self):
        self.write("# H\nAutopilot: owner\nAutopilot: done\n\n## Summary\n")
        self.assertEqual(hs.autopilot_state(self.path), "owner")

    def test_state_line_endings_and_bom_combinations(self):
        for newline in ("\n", "\r\n"):
            for bom in (False, True):
                self.write("# Session Handoff\nAutopilot: done\n\n## Summary\n", newline=newline, bom=bom)
                self.assertEqual(hs.autopilot_state(self.path), "done", (newline, bom))

    def test_state_with_unicode_header_text(self):
        self.write("# Caf\u00e9 \u65e5\u672c \u2014 $1.50 100%\nAutopilot: continue\n\n## Summary\n- \u00fc\n")
        self.assertEqual(hs.autopilot_state(self.path), "continue")

    def test_state_of_an_empty_file(self):
        self.write("")
        self.assertIsNone(hs.autopilot_state(self.path))

    def test_state_of_a_non_utf8_file_is_none(self):
        self.write_bytes(NON_UTF8)
        self.assertIsNone(hs.autopilot_state(self.path))

    def test_state_of_a_huge_file(self):
        body = "".join("- entry %d\n" % i for i in range(100000))
        self.write("# H\nAutopilot: owner\n\n## Progress Log\n" + body)
        self.assertEqual(hs.autopilot_state(self.path), "owner")

    def test_signature_of_empty_inputs_is_the_empty_digest(self):
        self.assertEqual(hs.progress_signature(os.path.join(self.tmp.name, "nope.md")), EMPTY_SIGNATURE)
        self.assertEqual(self.signature(""), EMPTY_SIGNATURE)
        self.assertEqual(self.signature("# H\n\n## Summary\n- a\n"), EMPTY_SIGNATURE)
        self.assertEqual(self.signature("# H\n\n## Progress Log\n\n  \n\t\n## Next\n- c\n"), EMPTY_SIGNATURE)
        self.assertEqual(self.signature("## Progress Log\n", "\r\n"), EMPTY_SIGNATURE)

    def test_signature_of_a_non_utf8_file_is_the_empty_digest(self):
        self.write_bytes(NON_UTF8)
        self.assertEqual(hs.progress_signature(self.path), EMPTY_SIGNATURE)

    def test_signature_changes_when_an_entry_is_removed_or_reordered(self):
        base = self.signature("## Progress Log\n- a\n- b\n- c\n")
        self.assertNotEqual(self.signature("## Progress Log\n- a\n- c\n"), base)
        self.assertNotEqual(self.signature("## Progress Log\n- a\n- c\n- b\n"), base)

    def test_signature_keeps_leading_whitespace(self):
        # Only trailing whitespace is stripped; indentation is content.
        base = self.signature("## Progress Log\n- a\n")
        self.assertNotEqual(self.signature("## Progress Log\n  - a\n"), base)
        self.assertEqual(self.signature("## Progress Log\n- a \t \n"), base)

    def test_signature_ignores_a_bom(self):
        for text in ("## Progress Log\n- a\n", "# H\n\n## Progress Log\n- a\n"):
            self.write(text)
            plain = hs.progress_signature(self.path)
            self.write(text, bom=True)
            self.assertEqual(hs.progress_signature(self.path), plain, text)
            self.assertNotEqual(plain, EMPTY_SIGNATURE, text)

    def test_signature_treats_escapes_and_specials_literally(self):
        decoded = self.signature("## Progress Log\n- caf\u00e9\n")
        escaped = self.signature("## Progress Log\n- caf\\u00e9\n")
        self.assertNotEqual(decoded, escaped)
        specials = "## Progress Log\n- \"q\" 'q' \\ $HOME %PATH% \u2026 \u65e5\u672c\n"
        lf = self.signature(specials)
        self.assertRegex(lf, r"^[0-9a-f]{64}$")
        self.assertEqual(self.signature(specials, "\r\n"), lf)
        self.assertNotEqual(lf, EMPTY_SIGNATURE)
        self.assertNotEqual(self.signature(specials.replace("\\", "/")), lf)

    def test_signature_of_a_huge_section(self):
        body = "".join("- entry %d\n" % i for i in range(100000))
        base = self.signature("## Progress Log\n" + body)
        self.assertRegex(base, r"^[0-9a-f]{64}$")
        self.assertEqual(self.signature("## Progress Log\n" + body, "\r\n"), base)
        self.assertNotEqual(self.signature("## Progress Log\n" + body + "- one more\n"), base)

    def test_cli_status_of_a_non_utf8_file(self):
        self.write_bytes(NON_UTF8)
        self.assertEqual(self.status(self.path),
                         {"exists": True, "autopilot": None, "progress": EMPTY_SIGNATURE})

    def test_cli_status_of_a_missing_file_exits_zero(self):
        missing = os.path.join(self.tmp.name, "nope.md")
        self.assertEqual(self.status(missing),
                         {"exists": False, "autopilot": None, "progress": EMPTY_SIGNATURE})

    def test_cli_status_without_an_autopilot_line_is_null(self):
        self.write("# H\n\n## Progress Log\n- a\n")
        self.assertEqual(self.status(self.path),
                         {"exists": True, "autopilot": None, "progress": hs.progress_signature(self.path)})

    def test_cli_status_with_crlf_bom_and_a_hostile_path(self):
        folder = os.path.join(self.tmp.name, "a $b %c% 'd' caf\u00e9 \u65e5\u672c")
        os.mkdir(folder)
        path = os.path.join(folder, "SESSION_HANDOFF.md")
        data = ("Autopilot: OWNER\n# H\n\n## Progress Log\n- \u00fc $1.50\n").replace("\n", "\r\n")
        with open(path, "wb") as f:
            f.write(b"\xef\xbb\xbf" + data.encode("utf-8"))
        result = self.status(path)
        self.assertEqual(result, {"exists": True, "autopilot": "owner",
                                  "progress": hs.progress_signature(path)})
        self.assertNotEqual(result["progress"], EMPTY_SIGNATURE)


class AtomicAppendTest(HandoffCase):
    ORIGINAL = "# H\n\n## Progress Log\n- a\n"

    def setUp(self):
        super().setUp()
        self.real_replace = os.replace
        # Keep any retry backoff instant.
        patcher = mock.patch("time.sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def entries(self):
        return sorted(os.listdir(self.tmp.name))

    def same(self, a, b):
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))

    def assert_replaced_from_same_dir(self, replace, target):
        self.assertTrue(replace.called, "append_progress did not call os.replace")
        src, dst = replace.call_args[0][:2]
        self.assertTrue(self.same(dst, target), (dst, target))
        self.assertTrue(self.same(os.path.dirname(os.path.abspath(src)), os.path.dirname(target)),
                        (src, target))
        self.assertFalse(self.same(src, target), src)

    def test_writes_through_a_temp_file_in_the_same_directory(self):
        self.write(self.ORIGINAL)
        with mock.patch("os.replace", side_effect=self.real_replace) as replace:
            hs.append_progress(self.path, "- b")
        self.assert_replaced_from_same_dir(replace, self.path)
        self.assertEqual(self.read(), b"# H\n\n## Progress Log\n- a\n- b\n")
        self.assertEqual(self.entries(), ["SESSION_HANDOFF.md"])

    def test_creating_a_missing_file_is_atomic_too(self):
        with mock.patch("os.replace", side_effect=self.real_replace) as replace:
            hs.append_progress(self.path, "- b")
        self.assert_replaced_from_same_dir(replace, self.path)
        self.assertEqual(self.read(), b"## Progress Log\n- b\n")
        self.assertEqual(self.entries(), ["SESSION_HANDOFF.md"])

    def test_crlf_bom_and_unicode_survive_the_atomic_write(self):
        self.write("# Caf\u00e9\n\n## Progress Log\n- a\n", newline="\r\n", bom=True)
        with mock.patch("os.replace", side_effect=self.real_replace) as replace:
            hs.append_progress(self.path, "- \"q\" \\ $1.50 100% \u2026 \u65e5\u672c")
        self.assert_replaced_from_same_dir(replace, self.path)
        expected = (chr(0xFEFF) + "# Caf\u00e9\r\n\r\n## Progress Log\r\n- a\r\n"
                    "- \"q\" \\ $1.50 100% \u2026 \u65e5\u672c\r\n")
        self.assertEqual(self.read(), expected.encode("utf-8"))
        self.assertEqual(self.entries(), ["SESSION_HANDOFF.md"])

    def test_temp_file_goes_next_to_a_target_in_a_hostile_directory(self):
        folder = os.path.join(self.tmp.name, "a $b %c% 'd' caf\u00e9 \u65e5\u672c")
        os.mkdir(folder)
        path = os.path.join(folder, "SESSION_HANDOFF.md")
        with open(path, "wb") as f:
            f.write(self.ORIGINAL.encode("utf-8"))
        with mock.patch("os.replace", side_effect=self.real_replace) as replace:
            hs.append_progress(path, "- b")
        self.assert_replaced_from_same_dir(replace, path)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"# H\n\n## Progress Log\n- a\n- b\n")
        self.assertEqual(os.listdir(folder), ["SESSION_HANDOFF.md"])

    def test_permission_error_is_retried_then_succeeds(self):
        self.write(self.ORIGINAL)
        failures = [PermissionError(13, "held open"), PermissionError(13, "held open")]

        def flaky(src, dst):
            if failures:
                raise failures.pop(0)
            return self.real_replace(src, dst)

        with mock.patch("os.replace", side_effect=flaky) as replace:
            hs.append_progress(self.path, "- b")
        self.assertEqual(replace.call_count, 3)
        self.assertEqual(self.read(), b"# H\n\n## Progress Log\n- a\n- b\n")
        self.assertEqual(self.entries(), ["SESSION_HANDOFF.md"])

    def test_persistent_permission_error_is_reraised_and_cleans_up(self):
        self.write(self.ORIGINAL)
        before = self.read()
        with mock.patch("os.replace", side_effect=PermissionError(13, "held open")) as replace:
            with self.assertRaises(PermissionError):
                hs.append_progress(self.path, "- b")
        self.assertGreater(replace.call_count, 1, "PermissionError was not retried")
        self.assertLess(replace.call_count, 50, "retry is not bounded to a few attempts")
        self.assertEqual(self.read(), before)
        self.assertEqual(self.entries(), ["SESSION_HANDOFF.md"])

    def test_other_replace_failure_leaves_no_temp_file(self):
        self.write(self.ORIGINAL)
        before = self.read()
        with mock.patch("os.replace", side_effect=OSError(28, "No space left on device")) as replace:
            with self.assertRaises(OSError):
                hs.append_progress(self.path, "- b")
        self.assertTrue(replace.called, "append_progress did not call os.replace")
        self.assertEqual(self.read(), before)
        self.assertEqual(self.entries(), ["SESSION_HANDOFF.md"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

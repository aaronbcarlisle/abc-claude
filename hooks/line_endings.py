#!/usr/bin/env python3
"""
Line-endings hook (PostToolUse, Edit and Write tools).

Enforces the CLAUDE.md line-endings rule: in a file whose `.gitattributes` eol
is `lf`, an edit that leaves CRLF behind is fed back to Claude. Flags mixed
endings anywhere, and an all-CRLF file that is untracked or was just written.
An all-CRLF *tracked* file after an Edit is a stale checkout from before the eol
rule: Edit keeps its endings and git normalizes on commit, so it isn't flagged.
Files outside a git repo, binary files and files without an `eol=lf` attribute
are skipped.

Exit 2 feeds stderr back to Claude (the edit itself already happened). Silent
on any error, so it never blocks work.

Tests: python -m unittest discover -s tests (from the repo root)
"""

import json
import os
import subprocess
import sys


def git(args, cwd):
    return subprocess.run(["git", "-C", cwd] + args, capture_output=True,
                          encoding="utf-8", errors="replace", timeout=10)


def problem(path, tool):
    """A message if `path` breaks its repo's eol=lf rule after `tool` ran, else None."""
    with open(path, "rb") as f:
        data = f.read()
    n_crlf = data.count(b"\r\n")
    if not n_crlf or b"\0" in data[:8000]:
        return None
    folder, name = os.path.split(path)
    if not git(["check-attr", "eol", "--", name], folder).stdout.strip().endswith(": lf"):
        return None
    mixed = n_crlf < data.count(b"\n")
    tracked = git(["ls-files", "--error-unmatch", "--", name], folder).returncode == 0
    if not mixed and tracked and tool == "Edit":
        return None
    what = "mixed CRLF/LF" if mixed else "CRLF"
    return ("line_endings hook: %s now has %s line endings, but its .gitattributes eol is lf. "
            "Convert it to LF (re-Write it with LF endings, or `sed -i 's/\\r$//' <file>`).\n"
            % (path, what))


def main():
    try:
        data = json.load(sys.stdin)
        path = (data.get("tool_input") or {}).get("file_path", "")
        if not path or not os.path.isfile(path):
            return 0
        msg = problem(os.path.abspath(path), data.get("tool_name"))
        if msg:
            sys.stderr.buffer.write(msg.encode("utf-8"))
            return 2
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Session-start hook (SessionStart).

Surfaces the project's SESSION_HANDOFF.md at startup, resume and after
compaction, so a fresh or compacted session sees the open work before it does
anything else. Prints a one-line header, the first HEAD_LINES lines (capped at
MAX_CHARS) and the last LOG_LINES entries of `## Progress Log`, which
`/handoff log` appends at the end of the file and which are newer than the
body. /resume reads the rest.

The project is $CLAUDE_PROJECT_DIR, else the hook input's `cwd`. Silent when
there's no handoff, and on any error. Always exits 0.

Tests: python -m unittest discover -s tests (from the repo root)
"""

import json
import os
import subprocess
import sys
import time

HEAD_LINES = 25
MAX_CHARS = 2500
LOG_LINES = 5
LOG_CHARS = 1500


def progress_tail(lines):
    """The last LOG_LINES entries under `## Progress Log` beyond the head, or ''."""
    for i, line in enumerate(lines):
        if line.strip().lower() == "## progress log":
            entries = [l for l in lines[max(i + 1, HEAD_LINES):] if l.strip()]
            if not entries:
                return ""
            return "## Progress Log (newest last):\n" + "\n".join(entries[-LOG_LINES:])[-LOG_CHARS:]
    return ""


def project_dir():
    root = os.environ.get("CLAUDE_PROJECT_DIR")
    if root:
        return root
    try:
        return json.load(sys.stdin).get("cwd") or os.getcwd()
    except Exception:
        return os.getcwd()


def main():
    try:
        root = project_dir()
        path = os.path.join(root, "SESSION_HANDOFF.md")
        if not os.path.isfile(path):
            return 0
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        age_h = (time.time() - os.path.getmtime(path)) / 3600
        branch = subprocess.run(["git", "branch", "--show-current"], cwd=root, capture_output=True,
                                encoding="utf-8", errors="replace", timeout=10).stdout.strip() or "?"
        head = "\n".join(lines[:HEAD_LINES])[:MAX_CHARS]
        more = " (%d more lines; /resume reads it all)" % (len(lines) - HEAD_LINES) if len(lines) > HEAD_LINES else ""
        tail = progress_tail(lines)
        out = "SESSION_HANDOFF.md (updated %.0f h ago; current branch: %s)%s:\n%s\n" % (age_h, branch, more, head)
        if tail:
            out += "...\n%s\n" % tail
        # UTF-8 explicitly: Windows Python defaults stdout to cp1252, and handoffs carry em dashes.
        sys.stdout.buffer.write(out.encode("utf-8"))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

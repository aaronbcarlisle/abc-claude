#!/usr/bin/env python3
"""
Read and append to a SESSION_HANDOFF.md without changing its line endings.

    python handoff_state.py append FILE LINE   # add LINE as the last Progress Log entry
    python handoff_state.py status FILE        # JSON: exists, autopilot state, Progress Log signature

The file keeps its newline style (CRLF when it has any, else LF), BOM and
UTF-8 text. A missing section is added at the end; a missing file is created.

Tests: python -m unittest discover -s tests (from the repo root)
"""

import hashlib
import json
import os
import re
import sys
import tempfile
import time

PROGRESS = "## Progress Log"


def _read(path):
    """(lines without endings, newline, bom) for `path`.

    ([], "\\n", "") when the file doesn't exist. `bom` is "\\ufeff" when the
    file starts with a UTF-8 BOM, else "" -- it is stripped from `lines[0]`
    here so a leading BOM never breaks matching PROGRESS against the first
    line, and it is returned so the caller can write it back unchanged.
    Private, but also consumed by a later task (Task 10) that extends this
    module with more state reads.
    """
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return [], "\n", ""
    newline = "\r\n" if b"\r\n" in raw else "\n"
    text = raw.decode("utf-8")
    utf8_bom = chr(0xFEFF)
    bom = utf8_bom if text.startswith(utf8_bom) else ""
    if bom:
        text = text[len(bom):]
    lines = text.replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines, newline, bom


def append_progress(path, line):
    line = " ".join(line.replace("\r\n", "\n").replace("\r", "\n").split("\n"))
    lines, newline, bom = _read(path)
    start = next((i for i, text in enumerate(lines) if text.strip() == PROGRESS), None)
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += [PROGRESS, line]
    else:
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
        at = end
        while at > start + 1 and not lines[at - 1].strip():
            at -= 1
        lines.insert(at, line)
    _write_atomic(path, (bom + newline.join(lines) + newline).encode("utf-8"))


def _write_atomic(path, data):
    """Write `data` to a temp file beside `path`, then os.replace it over `path`.

    Retries a PermissionError (Windows: another process has the target open) a few
    times, then re-raises. The temp file is always removed on failure."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)),
                               prefix=".handoff-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.1)
    finally:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass


AUTOPILOT = re.compile(r"^\s*Autopilot:\s*(\S+)\s*$", re.I)
STATES = ("continue", "owner", "done")


def _read_or_empty(path):
    """Lines of `path`, or [] when it isn't UTF-8, so the readers fail closed."""
    try:
        return _read(path)[0]
    except UnicodeDecodeError:
        return []


def autopilot_state(path):
    """'continue', 'owner' or 'done' from the `Autopilot:` line above the first `## ` heading;
    None when the file, the line or a valid value is missing."""
    for line in _read_or_empty(path):
        if line.startswith("## "):
            break
        match = AUTOPILOT.match(line)
        if match:
            value = match.group(1).lower()
            return value if value in STATES else None
    return None


def progress_signature(path):
    """SHA-256 hex of the Progress Log section's non-blank lines, trailing whitespace stripped.
    Content-based, so rewriting an entry counts as a change even when the entry count doesn't
    move. A missing file or section hashes as empty."""
    kept, in_log = [], False
    for line in _read_or_empty(path):
        if line.startswith("## "):
            in_log = line.strip() == PROGRESS
        elif in_log and line.strip():
            kept.append(line.rstrip())
    return hashlib.sha256("\n".join(kept).encode("utf-8")).hexdigest()


def main(argv):
    if len(argv) == 3 and argv[0] == "append":
        append_progress(argv[1], argv[2])
        return 0
    if len(argv) == 2 and argv[0] == "status":
        print(json.dumps({"exists": os.path.isfile(argv[1]), "autopilot": autopilot_state(argv[1]),
                          "progress": progress_signature(argv[1])}))
        return 0
    print("usage: handoff_state.py append FILE LINE | status FILE", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

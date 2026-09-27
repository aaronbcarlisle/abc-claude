#!/usr/bin/env python3
"""
Read and append to a SESSION_HANDOFF.md without changing its line endings.

    python handoff_state.py append FILE LINE   # add LINE as the last Progress Log entry

The file keeps its newline style (CRLF when it has any, else LF), BOM and
UTF-8 text. A missing section is added at the end; a missing file is created.

Tests: python -m unittest discover -s tests (from the repo root)
"""

import sys

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
    with open(path, "wb") as f:
        f.write((bom + newline.join(lines) + newline).encode("utf-8"))


def main(argv):
    if len(argv) == 3 and argv[0] == "append":
        append_progress(argv[1], argv[2])
        return 0
    print("usage: handoff_state.py append FILE LINE", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

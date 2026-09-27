#!/usr/bin/env python3
"""
PR-created hook (PostToolUse; `if: Bash(gh pr create *)` and `PowerShell(gh pr create *)`).

After a session opens a non-draft PR, tells Claude to carry straight on into
the ship loop: pr-review-loop with `ship #N`, which reviews, fixes and merges
it. The PR is the first PR URL on the command's stdout. Silent when:
- there's no PR URL there;
- the command asked for a draft (`--draft`, `-d`, or `--draft=<v>` with a true
  value such as `true` or `1`; `--draft=false` is a ready PR);
- it runs inside a subagent (`agent_id`);
- CLAUDE_SHIP_DISABLE=1 is set;
- the session is already inside /ship: a marker file named after the
  session_id in ~/.claude/state/ship/, under an hour old. /ship hands off
  itself, so the hook consumes the marker instead: it deletes that one file
  (best effort) and stays quiet, and the session's next PR hands off again.
  /ship can't pre-approve that rm (allowed-tools substitutes no session ID),
  so the hook does it. Only a session_id made of letters, digits, `_` and
  `-` is ever looked up or deleted, and only inside the marker directory.
CLAUDE_SHIP_STATE_DIR overrides the marker directory. Silent on any error, so
it never blocks a tool call.

Tests: python -m unittest discover -s tests (from the repo root)
"""

import json
import os
import re
import sys
import time

PR_URL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/(\d+)")
GH_PR_CREATE = re.compile(r"\bgh\s+pr\s+create\b")
SESSION_ID = re.compile(r"^[\w-]+$")
SEPARATORS = "&|;()"
DRAFT_TRUE = ("1", "t", "T", "TRUE", "true", "True")  # strconv.ParseBool's true values
MARKER_TTL = 3600
MESSAGE = "PR #{n} opened ({url}). Continue in this turn: invoke the pr-review-loop skill with `ship #{n}`."


def state_dir():
    return os.environ.get("CLAUDE_SHIP_STATE_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "state", "ship")


def split_words(command):
    """The command's words with quotes and backslash escapes removed, and None for each unquoted
    shell separator (`&&`, `||`, `|`, `;`, `(`, `)`, `&`). A quoted separator (`--body "&&"`) stays
    part of its word. An unclosed quote runs to the end of the command."""
    words, word, started, quote = [], "", False, None
    chars = iter(command)
    for c in chars:
        if quote == "'":
            if c == "'":
                quote = None
            else:
                word += c
        elif quote == '"':
            if c == '"':
                quote = None
            elif c == "\\":
                nxt = next(chars, "")
                word += nxt if nxt in '"\\$`' else c + nxt
            else:
                word += c
        elif c in "'\"":
            quote, started = c, True
        elif c == "\\":
            word, started = word + next(chars, ""), True
        elif c.isspace() or c in SEPARATORS:
            if started:
                words.append(word)
                word, started = "", False
            if c in SEPARATORS and words[-1:] != [None]:
                words.append(None)
        else:
            word, started = word + c, True
    if started:
        words.append(word)
    return words


def create_args(command):
    """The words of the `gh pr create` invocation, up to the next unquoted shell separator, so a `-d`
    from another command in the same call (`git branch -d old && gh pr create ...`) doesn't count."""
    words = split_words(command)
    for i in range(len(words) - 2):
        if words[i:i + 3] == ["gh", "pr", "create"]:
            args = []
            for w in words[i + 3:]:
                if w is None:
                    break
                args.append(w)
            return args
    return [w for w in words if w is not None]  # no clean `gh pr create` split: check every word


def is_draft(command):
    """`--draft` or `-d`, or `--draft=<v>` with a value gh's strconv.ParseBool reads as true.
    `--draft=false` (or 0, f, F, FALSE, False) opens a ready PR."""
    return any(w in ("--draft", "-d") or (w.startswith("--draft=") and w[len("--draft="):] in DRAFT_TRUE)
               for w in create_args(command))


def output(response):
    """The command's stdout, where `gh pr create` prints the new PR's URL (the whole response when
    it has no stdout field). The first URL in it is the created PR's: a later one comes from another
    command in the same call, and an "already exists" URL goes to stderr."""
    if isinstance(response, dict) and isinstance(response.get("stdout"), str):
        return response["stdout"]
    return response if isinstance(response, str) else json.dumps(response)


def in_ship(session):
    if not SESSION_ID.fullmatch(session):
        return False
    try:
        return time.time() - os.path.getmtime(os.path.join(state_dir(), session)) < MARKER_TTL
    except OSError:
        return False


def main():
    data = json.loads(sys.stdin.buffer.read())
    if data.get("agent_id") or os.environ.get("CLAUDE_SHIP_DISABLE") == "1":
        return
    command = (data.get("tool_input") or {}).get("command") or ""
    if not GH_PR_CREATE.search(command) or is_draft(command):
        return
    url = PR_URL.search(output(data.get("tool_response")))
    if not url:
        return
    session = data.get("session_id") or ""
    if in_ship(session):
        try:
            os.remove(os.path.join(state_dir(), session))  # in_ship validated the session ID
        except OSError:
            pass
        return
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": MESSAGE.format(n=url.group(1), url=url.group(0)),
    }}))
    sys.stdout.flush()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # never break a tool call over a nudge

#!/usr/bin/env python3
"""
PR-created hook (PostToolUse; `if: Bash(gh pr create *)` and `PowerShell(gh pr create *)`).

After a session opens a non-draft PR, tells Claude to carry straight on into
the ship loop: pr-review-loop with `ship #N`, which reviews, fixes and merges
it. The PR is the first PR URL on the command's stdout. Silent when:
- there's no PR URL there;
- the command asked for a draft (`--draft`, `--draft=...` or `-d`);
- it runs inside a subagent (`agent_id`);
- CLAUDE_SHIP_DISABLE=1 is set;
- the session is already inside /ship: a marker file named after the
  session_id in ~/.claude/state/ship/, under an hour old.
CLAUDE_SHIP_STATE_DIR overrides the marker directory. Silent on any error, so
it never blocks a tool call.

Tests: python -m unittest discover -s tests (from the repo root)
"""

import json
import os
import re
import shlex
import sys
import time

PR_URL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/(\d+)")
GH_PR_CREATE = re.compile(r"\bgh\s+pr\s+create\b")
SESSION_ID = re.compile(r"^[\w-]+$")
MARKER_TTL = 3600
MESSAGE = "PR #{n} opened ({url}). Continue in this turn: invoke the pr-review-loop skill with `ship #{n}`."


def state_dir():
    return os.environ.get("CLAUDE_SHIP_STATE_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "state", "ship")


def is_draft(command):
    try:
        words = shlex.split(command, posix=True)
    except ValueError:
        words = command.split()
    return any(w in ("--draft", "-d") or w.startswith("--draft=") for w in words)


def output(response):
    """The command's stdout, where `gh pr create` prints the new PR's URL (the whole response when
    it has no stdout field). The first URL in it is the created PR's: a later one comes from another
    command in the same call, and an "already exists" URL goes to stderr."""
    if isinstance(response, dict) and isinstance(response.get("stdout"), str):
        return response["stdout"]
    return response if isinstance(response, str) else json.dumps(response)


def in_ship(session):
    if not SESSION_ID.match(session):
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
    if in_ship(data.get("session_id") or ""):
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

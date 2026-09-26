#!/usr/bin/env python3
"""
Context-budget hook (PostToolUse).

Every turn re-reads the whole context, so cost tracks the token count. The first
time the main session's context reaches each LEVELS entry, this injects one
additionalContext nudge to finish the task in progress and hand off. Each level
fires once per session, even across parallel tool calls; a level re-arms if the
context drops back below it (e.g. after /compact).

Context = input + cache_read + cache_creation tokens of the last main-chain
assistant `usage` in the transcript, read from the file's tail only.

Silent inside subagents (their hook input carries `agent_id`), and silent on any
error, so it can never block a tool call. State: an empty marker file per
session and level in ~/.claude/state/context-budget/ (override with
CONTEXT_BUDGET_STATE_DIR), pruned after STATE_TTL.

Tests: python -m unittest discover -s tests (from the repo root)
"""

import json
import os
import sys
import time

LEVELS = (250_000, 400_000)   # keep in step with CONTEXT_HANDOFF / CONTEXT_HIGH in status_line.py
TAIL_BYTES = 256 * 1024       # first read; doubles until an assistant usage is found
MAX_TAIL_BYTES = 8 * 1024 * 1024
STATE_TTL = 14 * 24 * 3600    # prune state files older than this

MESSAGE = (
    "Context is at {tokens} tokens. Finish the task in progress, let running "
    "subagents return and pass review, run /handoff, then tell the owner to /clear "
    "and paste the /resume prompt. Don't start a new task first."
)


def state_dir():
    return os.environ.get("CONTEXT_BUDGET_STATE_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "state", "context-budget"
    )


def context_tokens(usage):
    return sum(usage.get(k) or 0 for k in
               ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))


def last_context_tokens(path):
    """Context size at the last main-chain assistant turn, or None."""
    size = os.path.getsize(path)
    tail = TAIL_BYTES
    with open(path, "rb") as f:
        while True:
            f.seek(max(0, size - tail))
            lines = f.read().splitlines()
            if size > tail:
                lines = lines[1:]  # first line is probably cut mid-entry
            for raw in reversed(lines):
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") != "assistant" or entry.get("isSidechain"):
                    continue
                usage = (entry.get("message") or {}).get("usage")
                tokens = context_tokens(usage) if isinstance(usage, dict) else 0
                if tokens:
                    return tokens
            if size <= tail or tail >= MAX_TAIL_BYTES:
                return None
            tail *= 2


def short_tokens(n):
    return f"{round(n / 1_000)}k"


def claim(marker):
    """Atomically create `marker`; True only for the one caller that created it."""
    try:
        os.close(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        return True
    except FileExistsError:
        return False


def prune(directory):
    """Best effort: drop markers from sessions older than STATE_TTL."""
    cutoff = time.time() - STATE_TTL
    for name in os.listdir(directory):
        try:
            old = os.path.join(directory, name)
            if os.path.getmtime(old) < cutoff:
                os.remove(old)
        except OSError:
            pass


def main():
    data = json.loads(sys.stdin.buffer.read())
    if data.get("agent_id"):
        return
    session = data.get("session_id")
    transcript = data.get("transcript_path")
    if not session or not transcript or not os.path.isfile(transcript):
        return

    tokens = last_context_tokens(transcript)
    if tokens is None:
        return

    # One marker file per (session, level). O_EXCL makes the claim atomic, so
    # parallel tool calls can't both nudge; removing a marker re-arms its level.
    directory = state_dir()
    os.makedirs(directory, exist_ok=True)
    claimed = False
    for level in LEVELS:
        marker = os.path.join(directory, f"{session}.{level}")
        if tokens >= level:
            claimed = claim(marker) or claimed
        elif os.path.exists(marker):
            try:
                os.remove(marker)
            except OSError:
                pass
    if claimed:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": MESSAGE.format(tokens=short_tokens(tokens)),
        }}))
        sys.stdout.flush()
        prune(directory)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # never break a tool call over a nudge

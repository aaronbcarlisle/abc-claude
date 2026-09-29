#!/usr/bin/env python3
"""
Stop guard hook (Stop).

Blocks a stop whose final message is only "No response requested": that's how
a turn dies right after a skill loads (CLAUDE.md: keep executing the skill).
It blocks once per chain: when stop_hook_active is set, the stop goes through.
Silent in subagents and on any error.

When CLAUDE_AUTOPILOT=1 and the handoff named by CLAUDE_AUTOPILOT_HANDOFF says
`Autopilot: continue`, the hook blocks the stop so the headless run dispatches
the next task. Once a block brings no progress (HEAD and the handoff's mtime
unchanged since that session's previous block), it lets the stop through, and
Claude Code's 8-continuation cap backs that up. The state lives in
~/.claude/state/stop-guard/ (override with STOP_GUARD_STATE_DIR).

Tests: python -m unittest discover -s tests (from the repo root)
"""

import json
import os
import re
import subprocess
import sys

NO_RESPONSE = re.compile(r"^no response requested\.?$", re.I)
REASON = ("Don't stop on 'No response requested': continue the skill or task you were "
          "running, or say what you're waiting for.")
AUTOPILOT_REASON = ("Autopilot: the handoff says continue. Dispatch the next task. If it needs the owner, "
                    "set `Autopilot: owner`, write the owner checklist, run /handoff, then stop.")


def block(reason):
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.stdout.flush()


def state_dir():
    return os.environ.get("STOP_GUARD_STATE_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "state", "stop-guard")


def progress_key(handoff):
    head = subprocess.run(["git", "-C", os.path.dirname(os.path.abspath(handoff)), "rev-parse", "HEAD"],
                          capture_output=True, encoding="utf-8", errors="replace").stdout.strip()
    return {"head": head, "mtime": os.path.getmtime(handoff)}


def autopilot_block(session):
    """True when an autopilot run should keep going: the handoff says continue, and HEAD or the
    handoff changed since this session's last block (so a block that brought no progress lets go)."""
    if os.environ.get("CLAUDE_AUTOPILOT") != "1":
        return False
    handoff = os.environ.get("CLAUDE_AUTOPILOT_HANDOFF") or ""
    if not os.path.isfile(handoff):
        return False
    # Lazy import: only load handoff_state when autopilot is active
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
    import handoff_state  # noqa: E402
    if handoff_state.autopilot_state(handoff) != "continue":
        return False
    key = progress_key(handoff)
    os.makedirs(state_dir(), exist_ok=True)
    path = os.path.join(state_dir(), re.sub(r"[^\w-]", "_", session or "none") + ".json")
    try:
        with open(path, encoding="utf-8") as f:
            previous = json.load(f)
    except (OSError, ValueError):
        previous = None
    if previous == key:
        os.remove(path)
        return False
    with open(path, "w", encoding="utf-8") as f:
        json.dump(key, f)
    return True


def main():
    data = json.loads(sys.stdin.buffer.read())
    if data.get("agent_id"):
        return
    message = (data.get("last_assistant_message") or "").strip()
    if not data.get("stop_hook_active") and NO_RESPONSE.match(message):
        block(REASON)
    elif autopilot_block(data.get("session_id")):
        block(AUTOPILOT_REASON)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # never trap a session over a guard error

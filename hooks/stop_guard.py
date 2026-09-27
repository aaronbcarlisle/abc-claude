#!/usr/bin/env python3
"""
Stop guard hook (Stop).

Blocks a stop whose final message is only "No response requested": that's how
a turn dies right after a skill loads (CLAUDE.md: keep executing the skill).
It blocks once per chain: when stop_hook_active is set, the stop goes through.
Silent in subagents and on any error.

Tests: python -m unittest discover -s tests (from the repo root)
"""

import json
import re
import sys

NO_RESPONSE = re.compile(r"^no response requested\.?$", re.I)
REASON = ("Don't stop on 'No response requested': continue the skill or task you were "
          "running, or say what you're waiting for.")


def block(reason):
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.stdout.flush()


def main():
    data = json.loads(sys.stdin.buffer.read())
    if data.get("agent_id"):
        return
    message = (data.get("last_assistant_message") or "").strip()
    if not data.get("stop_hook_active") and NO_RESPONSE.match(message):
        block(REASON)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # never trap a session over a guard error

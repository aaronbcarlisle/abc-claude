"""Fake `claude` for the autopilot tests.

Each call takes the next run from the JSON list in FAKE_AUTOPILOT_RUNS (the call
count lives in FAKE_AUTOPILOT_COUNT). It then:
1. logs its argv, cwd and CLAUDE_AUTOPILOT* environment to FAKE_AUTOPILOT_LOG,
   one JSON line per call;
2. applies the run's effects to the repo in the current directory;
3. prints the run's events as stream-json;
4. exits with the run's code.

A run: {"events": [...], "exit": 0, "commit": false, "progress": null | "- line",
        "rewrite": null | ["old text", "new text"], "autopilot": null | "done"}
`rewrite` replaces the first occurrence of the old text in the handoff, e.g. to
correct a Progress Log entry without adding one.
Calls past the end of the list exit 1 with no output.

Optional extra run keys (additive; absent means no effect):
- "stderr": ["line", ...] lines written to stderr, before anything on stdout.
- "raw": ["line", ...] lines printed to stdout as-is, after the stderr lines
  and before the events (e.g. non-JSON noise in the stream).
- "dirty": true appends a line to the tracked README.md without staging or
  committing it, so the run leaves the work tree dirty (after any "commit").
"""

import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import handoff_state  # noqa: E402

HANDOFF = "SESSION_HANDOFF.md"


def set_autopilot(value):
    with open(HANDOFF, "rb") as f:
        data = f.read()
    data = re.sub(rb"(?m)^Autopilot:[^\r\n]*", b"Autopilot: " + value.encode("utf-8"), data, count=1)
    with open(HANDOFF, "wb") as f:
        f.write(data)


def rewrite(old, new):
    with open(HANDOFF, "rb") as f:
        data = f.read()
    with open(HANDOFF, "wb") as f:
        f.write(data.replace(old.encode("utf-8"), new.encode("utf-8"), 1))


def main(argv):
    with open(os.environ["FAKE_AUTOPILOT_RUNS"], encoding="utf-8") as f:
        runs = json.load(f)
    count_path = os.environ["FAKE_AUTOPILOT_COUNT"]
    n = int(open(count_path).read()) if os.path.exists(count_path) else 0
    with open(count_path, "w") as f:
        f.write(str(n + 1))
    with open(os.environ["FAKE_AUTOPILOT_LOG"], "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": argv, "cwd": os.getcwd(),
                            "autopilot": os.environ.get("CLAUDE_AUTOPILOT"),
                            "handoff": os.environ.get("CLAUDE_AUTOPILOT_HANDOFF")}) + "\n")
    if n >= len(runs):
        return 1
    run = runs[n]
    if run.get("commit"):
        with open("work.txt", "a", encoding="utf-8") as f:
            f.write("run %d\n" % n)
        subprocess.run(["git", "add", "work.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "run %d" % n], check=True, capture_output=True)
    if run.get("dirty"):
        with open("README.md", "a", encoding="utf-8") as f:
            f.write("uncommitted edit %d\n" % n)
    if run.get("progress"):
        handoff_state.append_progress(HANDOFF, run["progress"])
    if run.get("rewrite"):
        rewrite(*run["rewrite"])
    if run.get("autopilot"):
        set_autopilot(run["autopilot"])
    for line in run.get("stderr", []):
        sys.stderr.write(line + "\n")
        sys.stderr.flush()
    for line in run.get("raw", []):
        print(line)
        sys.stdout.flush()
    for event in run.get("events", []):
        print(json.dumps(event))
        sys.stdout.flush()
    return run.get("exit", 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

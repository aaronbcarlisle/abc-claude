"""Fake `gh` for the post-merge cleanup tests.

Answers from the JSON in FAKE_GH_STATE and logs each call's arguments to
FAKE_GH_LOG (one line per call):

    {"default_branch": "main" | null, "merged": {"<branch>": [{"number": 1, "headRefOid": "<sha>"}]},
     "checks": {"exit": 0, "stdout": "", "stderr": ""}, "fail": false}

`fail: true` makes every call exit 1, like gh without auth or network.
"""

import json
import os
import sys


def main(argv):
    with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8", newline="\n") as f:
        f.write(" ".join(argv) + "\n")
    with open(os.environ["FAKE_GH_STATE"], encoding="utf-8") as f:
        state = json.load(f)
    if state.get("fail"):
        print("gh: not logged in", file=sys.stderr)
        return 1
    if argv[:2] == ["repo", "view"]:
        if not state.get("default_branch"):
            print("gh: no default branch", file=sys.stderr)
            return 1
        print(json.dumps({"defaultBranchRef": {"name": state["default_branch"]}}))
        return 0
    if argv[:2] == ["pr", "checks"]:
        checks = state.get("checks", {})
        print(checks.get("stdout", ""), end="")
        print(checks.get("stderr", ""), end="", file=sys.stderr)
        return checks.get("exit", 0)
    if argv[:2] == ["pr", "list"] and "--head" in argv:
        head = argv[argv.index("--head") + 1]
        print(json.dumps(state.get("merged", {}).get(head, [])))
        return 0
    print("fake gh: unsupported arguments: " + " ".join(argv), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

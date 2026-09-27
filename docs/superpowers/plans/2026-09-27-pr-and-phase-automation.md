# PR and Phase Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an opened PR review, fix and merge itself, and let a phase run overnight from its handoff. Also add adversarial tests before implementation, one-command stack finalizing and headless post-merge cleanup.

**Architecture:** Five sub-projects in abc-claude (`~/.claude`), each merged through its own PR, built in order A → E:
- **Deterministic helpers.** `scripts/live_sessions.py`, `scripts/handoff_state.py` and `scripts/post_merge_cleanup.py` do the git and file work without a model.
- **Skills.** `/ship`, pr-review-loop's ship mode and `/finalize-stack` drive the model through review and merge.
- **Hooks.** `pr_created.py` (PostToolUse) and `stop_guard.py` (Stop) keep a session moving without prompting.
- **Autopilot.** A PowerShell loop runs headless `claude -p "/resume autopilot"` until the handoff says stop.

**Tech Stack:** Python 3.12 stdlib only; PowerShell 7.2+; `gh` CLI; `unittest` subprocess tests; GitHub Actions on windows-latest and ubuntu-latest.

**Spec:** `docs/superpowers/specs/2026-09-27-pr-and-phase-automation-design.md` (approved 2026-09-27). Read it with this plan; where the two differ, the spec wins unless this plan's "Deviations" note says otherwise.

## Global Constraints

- Python: stdlib only, run as `python` (hooks and the statusline already assume that name). CI uses Python 3.12.
- Tests: `unittest`, run from the repo root with `python -m unittest discover -s tests -v`. Must pass on the `tests (windows-latest)` and `tests (ubuntu-latest)` CI jobs.
- Test helpers are imported as top-level modules from `tests/`, e.g. `import installer_harness as h`. Discover puts `tests/` on `sys.path`.
- Line endings: `.gitattributes` is `* text=auto eol=lf`, so every new file is LF. Edits keep a file's endings. Check with `git ls-files --eol <path>`.
- Portability: no home-directory paths or token-like strings in tracked files outside `docs/superpowers/` and `tests/fixtures/` (`tests/test_portability.py`). Use `$HOME`, `~` or `os.path.expanduser("~")`.
- Subprocess output: always pass `encoding="utf-8"` (plus `errors="replace"` for git and gh). This machine runs Python with UTF-8 mode off, so the default is cp1252.
- Never call `os.kill(pid, 0)` on Windows. It calls `TerminateProcess` and kills the process. Use the ctypes check in Task 1.
- Hooks exit 0 silently on any error. They're silent in subagents (the hook input has `agent_id`). They're registered in `settings.json` as `python "$HOME/.claude/hooks/<name>.py"`, like the existing hooks.
- Git: never push to `master`. Commit messages go in a file (in the session scratchpad) and are committed with `git commit -F <file>`. No AI attribution in any commit, PR body or comment (no `Co-Authored-By` trailer, no "Generated with" line, no session links).
- Files: write multi-line content with the Write tool, never heredocs or here-strings; `hooks/bash_guard.py` blocks those.
- Scripts refuse rather than guess (dirty trees, live sessions, unpushed commits, ambiguous stacks).
- Pushes: nothing force-pushes outside `/finalize-stack`, and that skill uses only `--force-with-lease`.
- Merging: run `gh pr merge --squash <N>` in exactly that argument order, which matches the `Bash(gh pr merge --squash *)` allow rule.
- `claude --help` doesn't list `--max-turns`, but Claude Code 2.1.283 accepts it with `-p`. Verified on 2026-09-27 with a one-turn run, whose `result` event had `num_turns: 1`, `subtype`, `is_error` and `total_cost_usd`. A `rate_limit_event` carries `rate_limit_info.status` and `resetsAt` (epoch seconds).
- Stay out of the live PyConsole (`E:\Dev\repos\PyConsole`) and ffxiv-raid-planner checkouts; read them only through `gh api`.

## Workspace and shipping each part

- **Workspace.** `~/.claude` is the live config, so it stays on `master`. All five parts are built in one sibling worktree at the short path `~/.claude-wt`:
  - `git -C ~/.claude worktree add ~/.claude-wt -b <part-branch> origin/master`;
  - `cd ~/.claude-wt` so the session's working directory, and the skills' preloaded context, point at it;
  - for the next part, `git -C ~/.claude-wt switch -c <next-branch> origin/master` after `git fetch`.
- **Branch names.** A `feat/post-merge-cleanup`, B `feat/ship-loop`, C `feat/finalize-stack`, D `feat/test-author`, E `feat/autopilot`.
- **Each task ends with a commit.** Implementer subagents commit when the dispatch says so and never push.
- **Each part ends with a PR:**
  1. Run the full gate, `python -m unittest discover -s tests -v`, in `~/.claude-wt`. It must be all green.
  2. Push the part branch and open a non-draft PR against `master`. The body says what changed and why, how it was tested (gate command and counts) and the acceptance result. No attribution.
     - Part A: run `/code-review medium <branch> --fix` before the PR, then run the pr-review-loop skill by hand on the PR.
     - Parts C–E: use `/ship`, which exists once Part B merges.
  3. After the merge: `git -C ~/.claude pull --ff-only`, then confirm `git -C ~/.claude status --short` is empty. From Part B on, Phase 6 runs the cleanup script. For Part A, run `python ~/.claude/scripts/post_merge_cleanup.py --repo ~/.claude-wt --here` by hand.
  4. Run `/handoff log`.
- **Owner gates:** plan approval, the Part B acceptance PR and the Part E real autopilot run. Stop at each and ask.

## Deviations from the spec (implementation details the spec left open)

1. **Checked-out, never-pushed branches.** Such a branch is kept even when it's an ancestor of `origin/<default>` (see Review Focus 1). The spec's ancestor rule would otherwise delete a branch the owner just created, with no commits yet.
2. **What "clean" means depends on the checkout.** For the main checkout it means no tracked changes; untracked files don't count. For worktree removal, untracked files count as dirty, as the spec says. Repos keep `SESSION_HANDOFF.md` untracked, and that mustn't block cleanup (Review Focus 2).
3. **`--dry-run` runs no fetch.** The spec says it "changes nothing", so it classifies against the refs already there.
4. **`autopilot.ps1` tells the Stop hook where the handoff is.** It passes `CLAUDE_AUTOPILOT_HANDOFF=<path>` next to `CLAUDE_AUTOPILOT=1`, so `stop_guard.py` reads that exact handoff.
5. **The `/ship` marker file expires.** `pr_created.py` ignores a marker more than an hour old, so a `/ship` run that died can't silence the hook for the rest of the session.
6. **The `Autopilot:` line is read only above the first `## ` heading.** Handoff prose that mentions "Autopilot:" can't be mistaken for it.

## Review Focus

1. **A branch created seconds ago with no commits, checked out in any checkout:** cleanup must keep it (its tip equals `origin/<default>`, so it is an ancestor). Test: `test_new_branch_without_commits_is_kept` in Task 3.
2. **Untracked files in the main checkout** (an untracked `SESSION_HANDOFF.md`, scratch notes): cleanup must proceed, not refuse as dirty. Test: `test_untracked_files_do_not_make_the_main_checkout_dirty` in Task 3.
3. **Non-ASCII branch names and handoff text on Windows** (cp1252 default decoding): the branch is deleted and reported intact, and the handoff line is written as UTF-8. Tests: `test_non_ascii_branch_name` in Task 3 and `test_utf8_bom_and_non_ascii_are_kept` in Task 2.
4. **`gh pr create` whose quoted body contains `-d` or `--draft`, or a compound command:** body text must not count as the draft flag. A separate `-d` token elsewhere in the command line may silence the hook, which is the safe side. Tests: `test_draft_word_inside_quoted_body_is_not_a_draft` and `test_compound_command` in Task 5.
5. **A headless run that crashes with no `result` event, or hits a usage limit with no reset time:** autopilot stops with `error`, or sleeps the default 30 minutes. It never spins. Tests: `test_crash_without_result_stops_with_error` and `test_usage_limit_without_reset_time_uses_default` in Task 12.
6. **A Windows PID at or above `2**31`:** it's a valid DWORD PID, so `pid_alive` must ask `OpenProcess`, not call it dead (cleanup would then delete a live session's checkout). The narrower `2**31 - 1` bound applies only to the POSIX `os.kill` path. Tests: `test_windows_high_pids_reach_open_process` and `test_posix_rejects_pids_past_the_signed_bound` in Task 1.

---

# Part A: post-merge cleanup script (PR 1, branch `feat/post-merge-cleanup`)

### Task 1: Live-session detection (`scripts/live_sessions.py`)

**Files:**
- Modify: `.gitignore` (add `!/scripts/` after `!/rules/`)
- Create: `scripts/live_sessions.py`
- Create: `tests/test_live_sessions.py`

**Interfaces:**
- Produces:
  - `sessions_dir() -> str`: reads `CLAUDE_SESSIONS_DIR`, otherwise `~/.claude/sessions`.
  - `pid_alive(pid) -> bool`. The PID bound is per platform: Windows PIDs are DWORDs, so 1 to `2**32 - 1` reach the `OpenProcess` check (`_windows_pid_alive`); the POSIX `os.kill` path takes 1 to `2**31 - 1`.
  - `norm(path) -> str`.
  - `inside(path, root) -> bool`.
  - `live_sessions(directory=None) -> list[dict]`: each dict has `pid`, `cwd` and `file`.
  - `owner(cwd, checkouts) -> str | None`: returns the most specific element of `checkouts` that contains `cwd`.
  - `live_checkouts(checkouts, sessions=None) -> dict[str, list[dict]]`: keys are elements of `checkouts`.
  - CLI `python live_sessions.py PATH [PATH ...]`: exit 0 when no session is inside any PATH; exit 1 and print `pid<TAB>cwd` per live session inside one; exit 2 on a usage error.

- [ ] **Step 1: Whitelist `scripts/`.** In `.gitignore`, insert the line `!/scripts/` directly after `!/rules/`.

- [ ] **Step 2: Write the failing tests** in `tests/test_live_sessions.py`:

```python
#!/usr/bin/env python3
"""
Tests for scripts/live_sessions.py: PID liveness, session-file parsing, and
mapping sessions to the most specific checkout.

    python -m unittest discover -s tests
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "scripts", "live_sessions.py")
sys.path.insert(0, os.path.join(REPO, "scripts"))
import live_sessions as ls  # noqa: E402


def dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


class LiveSessionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = os.path.join(self.tmp.name, "sessions")
        self.root = os.path.join(self.tmp.name, "repo")
        self.nested = os.path.join(self.root, ".worktrees", "x")
        for d in (self.dir, self.nested):
            os.makedirs(d)
        self.count = 0

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, data):
        self.count += 1
        path = os.path.join(self.dir, "%d.json" % self.count)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))
        return os.path.basename(path)

    def test_own_process_is_alive(self):
        self.assertTrue(ls.pid_alive(os.getpid()))

    def test_exited_process_is_dead(self):
        self.assertFalse(ls.pid_alive(dead_pid()))

    def test_bad_pids_are_dead(self):
        for pid in (None, 0, -1, "12", True, 2 ** 40):
            self.assertFalse(ls.pid_alive(pid), pid)

    def test_windows_high_pids_reach_open_process(self):
        # Windows PIDs are DWORDs: 2**31 .. 2**32-1 are valid and must be checked, not
        # short-circuited as dead (cleanup would delete a live session's checkout).
        # Patched, so no real high PID is needed and nothing is signalled.
        seen = []
        with mock.patch.object(ls, "WINDOWS", True), \
                mock.patch.object(ls, "_windows_pid_alive", side_effect=lambda pid: seen.append(pid) or True), \
                mock.patch.object(ls.os, "kill") as kill:
            self.assertTrue(ls.pid_alive(2 ** 31))
            self.assertTrue(ls.pid_alive(2 ** 32 - 1))
            self.assertFalse(ls.pid_alive(2 ** 32))
            self.assertFalse(ls.pid_alive(2 ** 40))
        self.assertEqual(seen, [2 ** 31, 2 ** 32 - 1])
        kill.assert_not_called()

    def test_posix_rejects_pids_past_the_signed_bound(self):
        with mock.patch.object(ls, "WINDOWS", False), mock.patch.object(ls.os, "kill") as kill:
            self.assertFalse(ls.pid_alive(2 ** 31))
            self.assertFalse(ls.pid_alive(2 ** 32 - 1))
        kill.assert_not_called()

    def test_live_sessions_skips_unreadable_dead_and_malformed(self):
        good = self.write({"pid": os.getpid(), "cwd": self.root})
        self.write({"pid": dead_pid(), "cwd": self.root})
        self.write("{")
        self.write([1])
        self.write({"pid": os.getpid()})
        with open(os.path.join(self.dir, "x.key"), "w", encoding="utf-8") as f:
            f.write("not json")
        found = ls.live_sessions(self.dir)
        self.assertEqual([s["file"] for s in found], [good])
        self.assertEqual(found[0]["cwd"], self.root)

    def test_missing_directory_means_no_sessions(self):
        self.assertEqual(ls.live_sessions(os.path.join(self.tmp.name, "nope")), [])

    def test_owner_prefers_the_most_specific_checkout(self):
        checkouts = [self.root, self.nested]
        self.assertEqual(ls.owner(os.path.join(self.nested, "sub"), checkouts), self.nested)
        self.assertEqual(ls.owner(os.path.join(self.root, "src"), checkouts), self.root)
        self.assertIsNone(ls.owner(self.tmp.name, checkouts))

    def test_prefix_is_not_containment(self):
        self.assertFalse(ls.inside(self.root + "-other", self.root))
        self.assertTrue(ls.inside(self.root, self.root))

    @unittest.skipUnless(os.name == "nt", "Windows paths are case-insensitive")
    def test_windows_paths_compare_case_insensitively(self):
        self.assertTrue(ls.inside(self.root.upper(), self.root))

    def test_live_checkouts_maps_each_session_to_one_checkout(self):
        self.write({"pid": os.getpid(), "cwd": self.nested})
        self.write({"pid": os.getpid(), "cwd": os.path.join(self.root, "src")})
        live = ls.live_checkouts([self.root, self.nested], ls.live_sessions(self.dir))
        self.assertEqual(sorted(live), sorted([self.root, self.nested]))
        self.assertEqual(len(live[self.root]), 1)
        self.assertEqual(len(live[self.nested]), 1)

    def cli(self, *paths):
        env = dict(os.environ, CLAUDE_SESSIONS_DIR=self.dir)
        return subprocess.run([sys.executable, SCRIPT, *paths], capture_output=True, encoding="utf-8",
                              env=env, timeout=30)

    def test_cli_exit_codes(self):
        self.assertEqual(self.cli(self.root).returncode, 0)
        self.write({"pid": os.getpid(), "cwd": self.nested})
        proc = self.cli(self.root)
        self.assertEqual(proc.returncode, 1)
        self.assertIn(str(os.getpid()), proc.stdout)
        self.assertEqual(self.cli().returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 3: Run them to watch them fail.** Run `python -m unittest tests.test_live_sessions -v`. Expected: `ModuleNotFoundError: No module named 'live_sessions'`.

- [ ] **Step 4: Implement** `scripts/live_sessions.py`:

```python
#!/usr/bin/env python3
"""
Live Claude Code sessions, from ~/.claude/sessions/<pid>.json.

Claude Code writes one file per running session holding its `pid` and `cwd`. A
session is live while its process runs. A checkout is live when a live
session's cwd is inside it and not inside a more specific checkout (a worktree
nested in it). Unreadable files are skipped. A PID reused by another process
still counts as live, which errs toward leaving a checkout alone.

    python live_sessions.py PATH [PATH ...]

Exit 0 when no live session is inside any PATH; 1 (listing pid<TAB>cwd) when
one is; 2 on a usage error. CLAUDE_SESSIONS_DIR overrides the directory.

Tests: python -m unittest discover -s tests (from the repo root)
"""

import json
import os
import sys

WINDOWS = os.name == "nt"
WINDOWS_PID_MAX = 2 ** 32 - 1  # PIDs are DWORDs
POSIX_PID_MAX = 2 ** 31 - 1    # pid_t is a signed 32-bit int
STILL_ACTIVE = 259
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_ACCESS_DENIED = 5


def sessions_dir():
    return os.environ.get("CLAUDE_SESSIONS_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "sessions")


def _windows_pid_alive(pid):
    """OpenProcess + GetExitCodeProcess. Never os.kill: on Windows it terminates."""
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED  # exists, not ours
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def pid_alive(pid):
    """True while process `pid` runs. Never signals it on Windows, where os.kill kills.
    The PID bound is per platform: a DWORD on Windows, a signed pid_t on POSIX."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if WINDOWS:
        return pid <= WINDOWS_PID_MAX and _windows_pid_alive(pid)
    if pid > POSIX_PID_MAX:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (OSError, OverflowError):
        return False
    return True


def norm(path):
    return os.path.normcase(os.path.realpath(path))


def inside(path, root):
    path, root = norm(path), norm(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def live_sessions(directory=None):
    """[{'pid', 'cwd', 'file'}] for every session file whose process runs."""
    directory = directory or sessions_dir()
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    found = []
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        pid, cwd = data.get("pid"), data.get("cwd")
        if isinstance(cwd, str) and cwd and pid_alive(pid):
            found.append({"pid": pid, "cwd": cwd, "file": name})
    return found


def owner(cwd, checkouts):
    """The most specific checkout in `checkouts` that contains `cwd`, or None."""
    best = None
    for checkout in checkouts:
        if inside(cwd, checkout) and (best is None or len(norm(checkout)) > len(norm(best))):
            best = checkout
    return best


def live_checkouts(checkouts, sessions=None):
    """{checkout: [session, ...]} for each checkout a live session is in."""
    sessions = live_sessions() if sessions is None else sessions
    found = {}
    for session in sessions:
        checkout = owner(session["cwd"], checkouts)
        if checkout is not None:
            found.setdefault(checkout, []).append(session)
    return found


def main(argv):
    if not argv:
        print("usage: live_sessions.py PATH [PATH ...]", file=sys.stderr)
        return 2
    hits = [s for s in live_sessions() if any(inside(s["cwd"], p) for p in argv)]
    for session in hits:
        print("%s\t%s" % (session["pid"], session["cwd"]))
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 5: Run the tests to watch them pass.** Run `python -m unittest tests.test_live_sessions -v`. Expected: all pass; on non-Windows, one test is skipped.

- [ ] **Step 6: Run the full suite.** Run `python -m unittest discover -s tests -v`. Expected: all pass, including `test_portability`, which now scans `scripts/`.

- [ ] **Step 7: Commit.** Stage `.gitignore scripts/live_sessions.py tests/test_live_sessions.py` and commit with the message `Add live-session detection for the automation scripts`.

### Task 2: Handoff progress log (`scripts/handoff_state.py`)

**Files:**
- Create: `scripts/handoff_state.py`
- Create: `tests/test_handoff_state.py`

**Interfaces:**
- Produces:
  - `PROGRESS = "## Progress Log"`.
  - `append_progress(path, line) -> None`: appends `line` as the last entry of the Progress Log section. It creates the section, or the file, when missing, and keeps the file's newline style (CRLF when the file has any `\r\n`, otherwise LF), its BOM and its UTF-8 text. CR and LF inside `line` become spaces.
  - CLI `python handoff_state.py append FILE LINE`.
  - Task 10 adds `autopilot_state` and a `status` subcommand.

- [ ] **Step 1: Write the failing tests** in `tests/test_handoff_state.py`:

```python
#!/usr/bin/env python3
"""
Tests for scripts/handoff_state.py.

    python -m unittest discover -s tests
"""

import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "scripts", "handoff_state.py")
sys.path.insert(0, os.path.join(REPO, "scripts"))
import handoff_state as hs  # noqa: E402


class AppendProgressTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = os.path.join(self.tmp.name, "SESSION_HANDOFF.md")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text, newline="\n", bom=False):
        data = text.replace("\n", newline).encode("utf-8")
        with open(self.path, "wb") as f:
            f.write((b"\xef\xbb\xbf" if bom else b"") + data)

    def read(self):
        with open(self.path, "rb") as f:
            return f.read()

    def test_appends_after_the_last_entry(self):
        self.write("# H\n\n## Progress Log\n- a\n")
        hs.append_progress(self.path, "- b")
        self.assertEqual(self.read(), b"# H\n\n## Progress Log\n- a\n- b\n")

    def test_keeps_crlf(self):
        self.write("# H\n\n## Progress Log\n- a\n", newline="\r\n")
        hs.append_progress(self.path, "- b")
        self.assertEqual(self.read(), b"# H\r\n\r\n## Progress Log\r\n- a\r\n- b\r\n")

    def test_empty_section_gets_the_first_line(self):
        self.write("# H\n\n## Progress Log\n")
        hs.append_progress(self.path, "- b")
        self.assertEqual(self.read(), b"# H\n\n## Progress Log\n- b\n")

    def test_section_followed_by_another_section(self):
        self.write("## Progress Log\n- a\n\n## Notes\nn\n")
        hs.append_progress(self.path, "- b")
        self.assertEqual(self.read(), b"## Progress Log\n- a\n- b\n\n## Notes\nn\n")

    def test_missing_section_is_added_at_the_end(self):
        self.write("# H\n\nbody\n")
        hs.append_progress(self.path, "- b")
        self.assertEqual(self.read(), b"# H\n\nbody\n\n## Progress Log\n- b\n")

    def test_missing_file_is_created(self):
        hs.append_progress(self.path, "- b")
        self.assertEqual(self.read(), b"## Progress Log\n- b\n")

    def test_file_without_trailing_newline(self):
        self.write("## Progress Log\n- a")
        hs.append_progress(self.path, "- b")
        self.assertEqual(self.read(), b"## Progress Log\n- a\n- b\n")

    def test_line_breaks_inside_the_line_are_flattened(self):
        self.write("## Progress Log\n")
        hs.append_progress(self.path, "- a\r\nb\nc")
        self.assertEqual(self.read(), b"## Progress Log\n- a b c\n")

    def test_utf8_bom_and_non_ascii_are_kept(self):
        self.write("# Café\n\n## Progress Log\n", bom=True)
        hs.append_progress(self.path, "- feat/café-日本")
        self.assertEqual(self.read(), "\ufeff# Café\n\n## Progress Log\n- feat/café-日本\n".encode("utf-8"))

    def test_cli_append(self):
        self.write("## Progress Log\n")
        proc = subprocess.run([sys.executable, SCRIPT, "append", self.path, "- $1.50 | ü"],
                              capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.read().decode("utf-8"), "## Progress Log\n- $1.50 | ü\n")

    def test_cli_usage_error(self):
        proc = subprocess.run([sys.executable, SCRIPT], capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run them to watch them fail.** Run `python -m unittest tests.test_handoff_state -v`. Expected: `ModuleNotFoundError: No module named 'handoff_state'`.

- [ ] **Step 3: Implement** `scripts/handoff_state.py`:

```python
#!/usr/bin/env python3
"""
Read and append to a SESSION_HANDOFF.md without changing its line endings.

    python handoff_state.py append FILE LINE   # add LINE as the last Progress Log entry

The file keeps its newline style (CRLF when it has any, else LF), BOM and
UTF-8 text. A missing section is added at the end; a missing file is created.

Tests: python -m unittest discover -s tests (from the repo root)
"""

import os
import sys

PROGRESS = "## Progress Log"


def _read(path):
    """(lines without endings, newline) for `path`; ([], "\\n") when it doesn't exist."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return [], "\n"
    newline = "\r\n" if b"\r\n" in raw else "\n"
    lines = raw.decode("utf-8").replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines, newline


def append_progress(path, line):
    line = " ".join(line.replace("\r\n", "\n").replace("\r", "\n").split("\n"))
    lines, newline = _read(path)
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
        f.write((newline.join(lines) + newline).encode("utf-8"))


def main(argv):
    if len(argv) == 3 and argv[0] == "append":
        append_progress(argv[1], argv[2])
        return 0
    print("usage: handoff_state.py append FILE LINE", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

The BOM survives because `decode("utf-8")` keeps it as `\ufeff` at the start of the first line, and the file is written back as UTF-8.

- [ ] **Step 4: Run the tests to watch them pass.** Run `python -m unittest tests.test_handoff_state -v`. Expected: all pass.

- [ ] **Step 5: Commit.** Stage `scripts/handoff_state.py tests/test_handoff_state.py` and commit with the message `Add a line-ending-safe handoff progress log writer`.

### Task 3: Cleanup: classify and delete merged branches (`scripts/post_merge_cleanup.py`)

**Files:**
- Create: `tests/stubs.py` (a shared helper: fake CLIs on PATH and an isolated git config)
- Create: `tests/fake_gh.py`
- Create: `scripts/post_merge_cleanup.py`
- Create: `tests/test_post_merge_cleanup.py`

**Interfaces:**
- Consumes:
  - `live_sessions.owner`, `live_sessions.live_checkouts` (Task 1);
  - `handoff_state.append_progress` (Task 2; used in Task 4).
- Produces:
  - CLI `post_merge_cleanup.py [--repo PATH] [--here] [--test CMD] [--handoff PATH] [--dry-run] [--json]`.
  - Exit 0 done, 1 tests failed, 2 refused or error.
  - The `--json` report object has these keys:
    - `repo`, `default` and `sha` (short HEAD of the main checkout);
    - `deleted` and `kept`, lists of `{"branch", "reason"}`;
    - `removed_worktrees`, `pulled`, `tests` (`ok`|`fail`|`skipped`), `test_output` and `notes`;
    - `dry_run`;
    - `refused` or `error`: a string, or absent.
  - `tests/stubs.py` provides:
    - `install_stub(bin_dir, name, script) -> str`;
    - `git_env(tmp, **extra) -> dict`;
    - `git(cwd, env, *args) -> str`.
  - Tasks 11 and 12 reuse `tests/stubs.py`.

- [ ] **Step 1: Write the test helpers.** Create `tests/stubs.py`:

```python
"""Shared test helpers: fake CLIs first on PATH, and an isolated git config.

install_stub writes `name.cmd` on Windows (subprocess and shutil.which find it
through PATHEXT) and an executable sh script elsewhere; both run `script` with
this Python.
"""

import os
import subprocess
import sys


def install_stub(bin_dir, name, script):
    os.makedirs(bin_dir, exist_ok=True)
    if os.name == "nt":
        path = os.path.join(bin_dir, name + ".cmd")
        body = '@echo off\r\n"%s" "%s" %%*\r\nexit /b %%ERRORLEVEL%%\r\n' % (sys.executable, script)
    else:
        path = os.path.join(bin_dir, name)
        body = '#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, script)
    with open(path, "wb") as f:
        f.write(body.encode("utf-8"))
    os.chmod(path, 0o755)
    return path


def git_env(tmp, **extra):
    """os.environ plus an isolated git identity and config under `tmp`, plus `extra`."""
    config = os.path.join(tmp, "gitconfig")
    with open(config, "w", encoding="utf-8", newline="\n") as f:
        f.write("[user]\n\tname = Test\n\temail = test@example.com\n"
                "[init]\n\tdefaultBranch = main\n[core]\n\tautocrlf = false\n")
    env = dict(os.environ)
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(key, None)
    env.update(GIT_CONFIG_GLOBAL=config, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
    env.update({key: str(value) for key, value in extra.items()})
    return env


def git(cwd, env, *args):
    proc = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise AssertionError("git %s failed (%d): %s" % (" ".join(args), proc.returncode, proc.stderr))
    return proc.stdout.strip()
```

- [ ] **Step 2: Write the fake `gh`.** Create `tests/fake_gh.py`:

```python
"""Fake `gh` for the post-merge cleanup tests.

Answers from the JSON in FAKE_GH_STATE and logs each call's arguments to
FAKE_GH_LOG (one line per call):

    {"default_branch": "main" | null, "merged": {"<branch>": [{"number": 1, "headRefOid": "<sha>"}]},
     "fail": false}

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
    if argv[:2] == ["pr", "list"] and "--head" in argv:
        head = argv[argv.index("--head") + 1]
        print(json.dumps(state.get("merged", {}).get(head, [])))
        return 0
    print("fake gh: unsupported arguments: " + " ".join(argv), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 3: Write the failing tests** in `tests/test_post_merge_cleanup.py`:

```python
#!/usr/bin/env python3
"""
Tests for scripts/post_merge_cleanup.py. Each test builds a bare origin, a clone
(the main checkout) and an "upstream" clone that merges PRs, with a fake `gh`
first on PATH, then runs the script as a subprocess.

    python -m unittest discover -s tests
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from stubs import git, git_env, install_stub

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "scripts", "post_merge_cleanup.py")
FAKE_GH = os.path.join(REPO, "tests", "fake_gh.py")


class CleanupCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        t = self.tmp.name
        self.count = 0
        bin_dir = os.path.join(t, "bin")
        install_stub(bin_dir, "gh", FAKE_GH)
        self.gh_state = os.path.join(t, "gh.json")
        self.set_gh(default_branch="main", merged={})
        self.sessions = os.path.join(t, "sessions")
        os.makedirs(self.sessions)
        self.env = git_env(t, FAKE_GH_STATE=self.gh_state, FAKE_GH_LOG=os.path.join(t, "gh.log"),
                           CLAUDE_SESSIONS_DIR=self.sessions,
                           PATH=bin_dir + os.pathsep + os.environ.get("PATH", ""))
        self.origin = os.path.join(t, "origin.git")
        self.work = os.path.join(t, "work")
        self.upstream = os.path.join(t, "upstream")
        git(t, self.env, "init", "-q", "--bare", "-b", "main", self.origin)
        git(t, self.env, "clone", "-q", self.origin, self.work)
        self.commit(self.work, "README.md", "hello\n", "init")
        git(self.work, self.env, "push", "-q", "-u", "origin", "main")
        git(self.work, self.env, "remote", "set-head", "origin", "main")
        git(t, self.env, "clone", "-q", self.origin, self.upstream)

    def tearDown(self):
        self.tmp.cleanup()

    # --- fixtures -------------------------------------------------------------
    def set_gh(self, **state):
        with open(self.gh_state, "w", encoding="utf-8") as f:
            json.dump(state, f)

    def gh(self):
        with open(self.gh_state, encoding="utf-8") as f:
            return json.load(f)

    def commit(self, cwd, name, text, message):
        with open(os.path.join(cwd, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        git(cwd, self.env, "add", name)
        git(cwd, self.env, "commit", "-q", "-m", message)
        return git(cwd, self.env, "rev-parse", "HEAD")

    def pushed_branch(self, name):
        """A branch with one commit, pushed with an upstream; the main checkout ends on main."""
        self.count += 1
        git(self.work, self.env, "switch", "-q", "-c", name)
        tip = self.commit(self.work, "f%d.txt" % self.count, name + "\n", "work on " + name)
        git(self.work, self.env, "push", "-q", "-u", "origin", name)
        git(self.work, self.env, "switch", "-q", "main")
        return tip

    def _upstream_main(self):
        git(self.upstream, self.env, "fetch", "-q", "origin")
        git(self.upstream, self.env, "switch", "-q", "main")
        git(self.upstream, self.env, "reset", "-q", "--hard", "origin/main")

    def squash_merge(self, name, number):
        tip = git(self.work, self.env, "rev-parse", "refs/heads/" + name)
        self._upstream_main()
        git(self.upstream, self.env, "merge", "-q", "--squash", "origin/" + name)
        git(self.upstream, self.env, "commit", "-q", "-m", "squash " + name)
        git(self.upstream, self.env, "push", "-q", "origin", "main")
        git(self.upstream, self.env, "push", "-q", "origin", "--delete", name)
        state = self.gh()
        state["merged"].setdefault(name, []).append({"number": number, "headRefOid": tip})
        self.set_gh(**state)

    def ff_merge(self, name):
        self._upstream_main()
        git(self.upstream, self.env, "merge", "-q", "--ff-only", "origin/" + name)
        git(self.upstream, self.env, "push", "-q", "origin", "main")
        git(self.upstream, self.env, "push", "-q", "origin", "--delete", name)

    def worktree(self, name, branch):
        path = os.path.join(self.tmp.name, name)
        git(self.work, self.env, "worktree", "add", "-q", path, branch)
        return path

    def live_session(self, cwd, pid=None):
        self.count += 1
        pid = pid or os.getpid()
        with open(os.path.join(self.sessions, "%d-%d.json" % (pid, self.count)), "w", encoding="utf-8") as f:
            json.dump({"pid": pid, "cwd": cwd}, f)

    # --- running ----------------------------------------------------------------
    def run_script(self, *args, cwd=None):
        return subprocess.run([sys.executable, SCRIPT, *args], cwd=cwd or self.work, env=self.env,
                              capture_output=True, encoding="utf-8", errors="replace", timeout=180)

    def cleanup(self, *args, cwd=None):
        proc = self.run_script("--json", *args, cwd=cwd)
        try:
            report = json.loads(proc.stdout)
        except ValueError:
            self.fail("no JSON on stdout (exit %d): %r %r" % (proc.returncode, proc.stdout, proc.stderr))
        return proc.returncode, report

    def branches(self):
        return set(git(self.work, self.env, "for-each-ref", "--format=%(refname:short)",
                       "refs/heads/").splitlines())

    def current(self):
        return git(self.work, self.env, "branch", "--show-current")

    @staticmethod
    def kept(report):
        return {k["branch"]: k["reason"] for k in report["kept"]}

    @staticmethod
    def deleted(report):
        return {d["branch"] for d in report["deleted"]}


class BranchTest(CleanupCase):
    def test_squash_merged_branch_is_deleted(self):
        self.pushed_branch("feat/a")
        self.squash_merge("feat/a", 1)
        code, r = self.cleanup()
        self.assertEqual(code, 0, r)
        self.assertEqual(self.deleted(r), {"feat/a"})
        self.assertNotIn("feat/a", self.branches())

    def test_fast_forward_merged_branch_is_deleted(self):
        self.pushed_branch("feat/a")
        self.ff_merge("feat/a")
        code, r = self.cleanup()
        self.assertEqual(code, 0, r)
        self.assertEqual(self.deleted(r), {"feat/a"})

    def test_unpushed_commits_after_the_merged_head_keep_the_branch(self):
        self.pushed_branch("feat/a")
        self.squash_merge("feat/a", 1)
        git(self.work, self.env, "switch", "-q", "feat/a")
        self.commit(self.work, "late.txt", "late\n", "late work")
        git(self.work, self.env, "switch", "-q", "main")
        code, r = self.cleanup()
        self.assertEqual(code, 0, r)
        self.assertIn("#1", self.kept(r)["feat/a"])
        self.assertIn("feat/a", self.branches())

    def test_unmerged_branch_is_kept(self):
        self.pushed_branch("feat/a")
        code, r = self.cleanup()
        self.assertEqual(code, 0, r)
        self.assertEqual(self.kept(r)["feat/a"], "not merged")
        self.assertIn("feat/a", self.branches())

    def test_new_branch_without_commits_is_kept(self):
        git(self.work, self.env, "switch", "-q", "-c", "fresh")
        code, r = self.cleanup()
        self.assertEqual(code, 0, r)
        self.assertIn("never pushed", self.kept(r)["fresh"])
        self.assertEqual(self.current(), "fresh")

    def test_merged_branch_in_main_checkout_is_switched_and_deleted(self):
        self.pushed_branch("feat/a")
        self.squash_merge("feat/a", 1)
        git(self.work, self.env, "switch", "-q", "feat/a")
        code, r = self.cleanup()
        self.assertEqual(code, 0, r)
        self.assertEqual(self.deleted(r), {"feat/a"})
        self.assertEqual(self.current(), "main")

    def test_non_ascii_branch_name(self):
        name = "feat/café-日本"
        self.pushed_branch(name)
        self.ff_merge(name)
        code, r = self.cleanup()
        self.assertEqual(code, 0, r)
        self.assertEqual(self.deleted(r), {name})
        self.assertNotIn(name, self.branches())

    def test_gh_unavailable_falls_back_to_origin_head(self):
        self.pushed_branch("feat/a")
        self.ff_merge("feat/a")
        self.pushed_branch("feat/b")
        self.squash_merge("feat/b", 2)
        self.set_gh(**dict(self.gh(), fail=True))
        code, r = self.cleanup()
        self.assertEqual(code, 0, r)
        self.assertEqual(r["default"], "main")
        self.assertEqual(self.deleted(r), {"feat/a"})
        self.assertEqual(self.kept(r)["feat/b"], "not merged")


class WorktreeTest(CleanupCase):
    def test_clean_worktree_is_removed(self):
        self.pushed_branch("feat/a")
        path = self.worktree("wt-a", "feat/a")
        self.squash_merge("feat/a", 1)
        code, r = self.cleanup()
        self.assertEqual(code, 0, r)
        self.assertEqual(self.deleted(r), {"feat/a"})
        self.assertFalse(os.path.exists(path))

    def test_worktree_with_untracked_file_is_kept(self):
        self.pushed_branch("feat/a")
        path = self.worktree("wt-a", "feat/a")
        with open(os.path.join(path, "scratch.txt"), "w", encoding="utf-8") as f:
            f.write("x\n")
        self.squash_merge("feat/a", 1)
        code, r = self.cleanup()
        self.assertEqual(code, 0, r)
        self.assertIn("uncommitted or untracked", self.kept(r)["feat/a"])
        self.assertTrue(os.path.exists(path))

    def test_live_session_in_worktree_keeps_it(self):
        self.pushed_branch("feat/a")
        path = self.worktree("wt-a", "feat/a")
        self.squash_merge("feat/a", 1)
        self.live_session(path)
        code, r = self.cleanup()
        self.assertEqual(code, 0, r)
        self.assertIn("live session", self.kept(r)["feat/a"])
        self.assertTrue(os.path.exists(path))

    def test_here_exempts_the_callers_worktree(self):
        self.pushed_branch("feat/a")
        path = self.worktree("wt-a", "feat/a")
        self.pushed_branch("feat/b")
        self.squash_merge("feat/a", 1)
        self.squash_merge("feat/b", 2)
        self.live_session(path)
        code, r = self.cleanup("--here", cwd=path)
        self.assertEqual(code, 0, r)
        self.assertIn("after this session ends", self.kept(r)["feat/a"])
        self.assertEqual(self.deleted(r), {"feat/b"})
        self.assertTrue(os.path.exists(path))


class RefusalTest(CleanupCase):
    def test_live_session_in_main_checkout_refuses(self):
        self.live_session(self.work)
        code, r = self.cleanup()
        self.assertEqual(code, 2, r)
        self.assertIn("live", r["refused"])

    def test_dead_session_is_ignored(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        self.live_session(self.work, pid=proc.pid)
        code, r = self.cleanup()
        self.assertEqual(code, 0, r)

    def test_here_in_main_checkout_with_live_session_proceeds(self):
        self.pushed_branch("feat/a")
        self.squash_merge("feat/a", 1)
        self.live_session(self.work)
        code, r = self.cleanup("--here")
        self.assertEqual(code, 0, r)
        self.assertEqual(self.deleted(r), {"feat/a"})

    def test_dirty_main_checkout_refuses_without_here(self):
        with open(os.path.join(self.work, "README.md"), "a", encoding="utf-8") as f:
            f.write("edit\n")
        code, r = self.cleanup()
        self.assertEqual(code, 2, r)
        self.assertIn("uncommitted", r["refused"])

    def test_dirty_main_checkout_with_here_skips_the_pull(self):
        self.pushed_branch("feat/a")
        self.squash_merge("feat/a", 1)
        with open(os.path.join(self.work, "README.md"), "a", encoding="utf-8") as f:
            f.write("edit\n")
        code, r = self.cleanup("--here")
        self.assertEqual(code, 0, r)
        self.assertEqual(self.deleted(r), {"feat/a"})
        self.assertFalse(r["pulled"])
        self.assertTrue(any("pull skipped" in n for n in r["notes"]), r["notes"])

    def test_untracked_files_do_not_make_the_main_checkout_dirty(self):
        for name in ("SESSION_HANDOFF.md", "notes.txt"):
            with open(os.path.join(self.work, name), "w", encoding="utf-8") as f:
                f.write("untracked\n")
        self.pushed_branch("feat/a")
        self.squash_merge("feat/a", 1)
        code, r = self.cleanup()
        self.assertEqual(code, 0, r)
        self.assertEqual(self.deleted(r), {"feat/a"})

    def test_not_a_git_repo_refuses(self):
        empty = os.path.join(self.tmp.name, "empty")
        os.makedirs(empty)
        code, r = self.cleanup("--repo", empty)
        self.assertEqual(code, 2, r)
        self.assertIn("not a git repository", r["refused"])

    def test_dry_run_changes_nothing(self):
        self.pushed_branch("feat/a")
        path = self.worktree("wt-a", "feat/a")
        self.squash_merge("feat/a", 1)

        def snapshot():
            return (self.branches(), git(self.work, self.env, "worktree", "list", "--porcelain"),
                    git(self.work, self.env, "rev-parse", "HEAD"),
                    git(self.work, self.env, "for-each-ref", "refs/remotes/"), os.path.exists(path))

        before = snapshot()
        code, r = self.cleanup("--dry-run")
        self.assertEqual(code, 0, r)
        self.assertTrue(r["dry_run"])
        self.assertEqual(self.deleted(r), {"feat/a"})
        self.assertEqual(snapshot(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 4: Run them to watch them fail.** Run `python -m unittest tests.test_post_merge_cleanup -v`. Expected: every test fails with "no JSON on stdout", because the script doesn't exist yet.

- [ ] **Step 5: Implement** `scripts/post_merge_cleanup.py`. Task 4 adds the pull, `--test`, handoff and human output, so leave those as shown:

```python
#!/usr/bin/env python3
"""
Post-merge cleanup: delete merged local branches and their worktrees,
fast-forward the default branch, optionally run the tests, and log the result
to the handoff.

    post_merge_cleanup.py [--repo PATH] [--here] [--test CMD] [--handoff PATH] [--dry-run] [--json]

A local branch is deleted only when nothing unpushed is lost. That means one of:
- its tip is an ancestor of origin/<default>;
- a merged PR exists for it whose head contains the tip.

A checked-out branch whose tip is an ancestor but that was never pushed (no
upstream) is kept: it's new work that hasn't started. Checkouts with a live
Claude Code session are skipped; --here exempts the caller's own checkout.
Never commits and never pushes. --dry-run changes nothing (not even a fetch).

Exit: 0 done, 1 tests failed, 2 refused or error.

Tests: python -m unittest discover -s tests (from the repo root)
"""

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import handoff_state  # noqa: E402
import live_sessions  # noqa: E402

TEST_TAIL = 40
TEST_TIMEOUT = 1800


class Refused(Exception):
    pass


def run(argv, cwd, check=True):
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise RuntimeError("%s failed (%d): %s" % (" ".join(argv), proc.returncode, proc.stderr.strip()))
    return proc


def git(cwd, *args):
    return run(["git", *args], cwd).stdout.strip()


def git_ok(cwd, *args):
    return run(["git", *args], cwd, check=False).returncode == 0


def gh_json(cwd, *args):
    """Parsed JSON from `gh args`, or None when gh is missing or fails."""
    exe = shutil.which("gh")
    if not exe:
        return None
    proc = run([exe, *args], cwd, check=False)
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return None


def default_branch(repo):
    data = gh_json(repo, "repo", "view", "--json", "defaultBranchRef")
    name = ((data or {}).get("defaultBranchRef") or {}).get("name") if isinstance(data, dict) else None
    if name:
        return name
    ref = run(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], repo, check=False).stdout.strip()
    if ref.startswith("origin/"):
        return ref[len("origin/"):]
    for name in ("main", "master"):
        if git_ok(repo, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/" + name):
            return name
    raise Refused("can't determine the default branch")


def worktrees(repo):
    """[{'path', 'branch', 'locked', 'prunable', 'bare'}]; the first entry is the main checkout."""
    found, current = [], None
    for line in git(repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            current = {"path": line[len("worktree "):], "branch": None,
                       "locked": False, "prunable": False, "bare": False}
            found.append(current)
        elif current is None:
            continue
        elif line.startswith("branch refs/heads/"):
            current["branch"] = line[len("branch refs/heads/"):]
        elif line.startswith("locked"):
            current["locked"] = True
        elif line.startswith("prunable"):
            current["prunable"] = True
        elif line == "bare":
            current["bare"] = True
    return found


def dirty(path, untracked):
    args = ["status", "--porcelain"] + ([] if untracked else ["--untracked-files=no"])
    return bool(git(path, *args))


def merged_pr(repo, branch, tip, fetch):
    """('#N', None) when merged PR #N's head contains `tip`; (None, '#N') when merged PRs exist
    but none contains it; (None, None) when there is no merged PR."""
    prs = gh_json(repo, "pr", "list", "--state", "merged", "--head", branch,
                  "--json", "number,headRefOid", "--limit", "20")
    seen = None
    for pr in prs if isinstance(prs, list) else []:
        oid, number = pr.get("headRefOid"), pr.get("number")
        if not oid or number is None:
            continue
        seen = "#%s" % number
        if fetch and not git_ok(repo, "cat-file", "-e", oid + "^{commit}"):
            run(["git", "fetch", "--quiet", "origin", "refs/pull/%s/head" % number], repo, check=False)
        if git_ok(repo, "merge-base", "--is-ancestor", tip, oid):
            return "#%s" % number, None
    return None, seen


def cleanup(args):
    report = {"repo": None, "default": None, "sha": None, "deleted": [], "kept": [],
              "removed_worktrees": [], "pulled": False, "tests": "skipped", "test_output": "",
              "notes": [], "dry_run": bool(args.dry_run)}
    start = os.path.abspath(args.repo or os.getcwd())
    top = run(["git", "rev-parse", "--show-toplevel"], start, check=False) if os.path.isdir(start) else None
    if top is None or top.returncode != 0:
        raise Refused("not a git repository: " + start)
    if not args.dry_run:
        git(top.stdout.strip(), "worktree", "prune")
    trees = worktrees(top.stdout.strip())
    main_tree = trees[0]
    if main_tree["bare"]:
        raise Refused("bare repository: " + main_tree["path"])
    repo = main_tree["path"]
    report["repo"] = repo
    paths = [t["path"] for t in trees]
    here = live_sessions.owner(os.getcwd(), paths) if args.here else None
    live = {c: s for c, s in live_sessions.live_checkouts(paths).items() if c != here}
    if repo in live:
        raise Refused("a live Claude Code session is in the main checkout (pid %s)"
                      % ", ".join(str(s["pid"]) for s in live[repo]))
    main_dirty = dirty(repo, untracked=False)
    if main_dirty and not args.here:
        raise Refused("the main checkout has uncommitted changes")

    default = default_branch(repo)
    report["default"] = default
    if not args.dry_run:
        git(repo, "fetch", "--prune", "--quiet", "origin")
    target = "refs/remotes/origin/" + default
    checked_out = {t["branch"]: t for t in trees if t["branch"]}

    for branch in git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/").splitlines():
        if branch == default:
            continue

        def keep(reason):
            report["kept"].append({"branch": branch, "reason": reason})

        tip = git(repo, "rev-parse", "refs/heads/" + branch)
        tree = checked_out.get(branch)
        ancestor = git_ok(repo, "merge-base", "--is-ancestor", tip, target)
        upstream = run(["git", "config", "--get", "branch.%s.merge" % branch], repo, check=False).stdout.strip()
        if ancestor and tree and not upstream:
            keep("checked out, no commits and never pushed")
            continue
        why = "merged into " + default if ancestor else None
        if why is None:
            pr, other = merged_pr(repo, branch, tip, fetch=not args.dry_run)
            if pr:
                why = "PR %s merged" % pr
            elif other:
                keep("has commits not in merged PR %s" % other)
                continue
            else:
                keep("not merged")
                continue

        if tree:
            path = tree["path"]
            if path == repo:
                if main_dirty:
                    keep("checked out in the main checkout, which has uncommitted changes")
                    continue
                if not args.dry_run:
                    git(repo, "switch", "--quiet", default)
            elif path == here:
                keep("checked out in this session's worktree: remove it after this session ends")
                continue
            elif path in live:
                keep("worktree %s has a live session" % path)
                continue
            elif tree["locked"]:
                keep("worktree %s is locked" % path)
                continue
            elif not tree["prunable"]:
                if dirty(path, untracked=True):
                    keep("worktree %s has uncommitted or untracked files" % path)
                    continue
                if not args.dry_run:
                    removed = run(["git", "worktree", "remove", path], repo, check=False)
                    if removed.returncode != 0:
                        keep("worktree remove failed: %s" % removed.stderr.strip())
                        continue
                report["removed_worktrees"].append(path)
        if not args.dry_run:
            git(repo, "branch", "-D", branch)
        report["deleted"].append({"branch": branch, "reason": why})

    finish(repo, default, args, report)
    return report


def finish(repo, default, args, report):
    """Pull, test and log. Task 4 fills this in."""
    report["sha"] = git(repo, "rev-parse", "--short", "HEAD")


def render(report):
    return json.dumps(report, indent=2)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Delete merged branches and worktrees after a merge.")
    parser.add_argument("--repo", help="any path inside the repo (default: the current directory)")
    parser.add_argument("--here", action="store_true",
                        help="the caller is the session in the checkout holding the current directory")
    parser.add_argument("--test", metavar="CMD", help="test command to run in the main checkout")
    parser.add_argument("--handoff", metavar="PATH", help="handoff file to log to")
    parser.add_argument("--dry-run", action="store_true", help="report what would happen; change nothing")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    try:
        report = cleanup(args)
        code = 1 if report["tests"] == "fail" else 0
    except Refused as exc:
        report, code = {"refused": str(exc)}, 2
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        report, code = {"error": str(exc)}, 2
    print(json.dumps(report, indent=2) if args.json else render(report))
    return code


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Run the tests to watch them pass.** Run `python -m unittest tests.test_post_merge_cleanup -v`. Expected: all pass.
  - If only `test_non_ascii_branch_name` fails on Windows, check the decoding in `run()`: it must pass `encoding="utf-8"`.
  - If `test_here_exempts_the_callers_worktree` fails, compare `os.getcwd()` with the porcelain path through `live_sessions.norm`. The paths differ only in slashes and case, which `norm` must absorb.

- [ ] **Step 7: Run the full suite.** Run `python -m unittest discover -s tests -v`. Expected: all pass.

- [ ] **Step 8: Commit.** Stage `scripts/post_merge_cleanup.py tests/stubs.py tests/fake_gh.py tests/test_post_merge_cleanup.py` and commit with the message `Add the post-merge cleanup script: merged-branch and worktree deletion`.

### Task 4: Cleanup: pull, tests, handoff log and human output

**Files:**
- Modify: `scripts/post_merge_cleanup.py` (replace `finish` and `render`)
- Modify: `tests/test_post_merge_cleanup.py` (add `FinishTest`)

**Interfaces:**
- Consumes: `handoff_state.append_progress(path, line)` (Task 2).
- Produces the handoff line format: `- YYYY-MM-DD HH:MM | post-merge cleanup | tests <ok|fail|skipped> | <short SHA> | deleted: <b, ...|none>; kept: <b (reason), ...|none>`

- [ ] **Step 1: Write the failing tests.** Append this class to `tests/test_post_merge_cleanup.py`, above the `if __name__` block:

```python
class FinishTest(CleanupCase):
    LINE = (r"^- \d{4}-\d{2}-\d{2} \d{2}:\d{2} \| post-merge cleanup \| tests skipped \| [0-9a-f]{7,} "
            r"\| deleted: feat/a; kept: none$")

    def merged(self):
        self.pushed_branch("feat/a")
        self.squash_merge("feat/a", 1)

    def test_pull_fast_forwards_the_default_branch(self):
        self.merged()
        before = git(self.work, self.env, "rev-parse", "HEAD")
        code, r = self.cleanup()
        self.assertEqual(code, 0, r)
        self.assertTrue(r["pulled"])
        after = git(self.work, self.env, "rev-parse", "HEAD")
        self.assertNotEqual(after, before)
        self.assertEqual(after, git(self.work, self.env, "rev-parse", "origin/main"))
        self.assertTrue(after.startswith(r["sha"]))

    def test_passing_test_command(self):
        code, r = self.cleanup("--test", '"%s" -c "print(1)"' % sys.executable)
        self.assertEqual(code, 0, r)
        self.assertEqual(r["tests"], "ok")
        self.assertEqual(r["test_output"], "")

    def test_failing_test_command_exits_1_with_the_output_tail(self):
        cmd = '"%s" -c "import sys; [print(i) for i in range(100)]; sys.exit(3)"' % sys.executable
        code, r = self.cleanup("--test", cmd)
        self.assertEqual(code, 1, r)
        self.assertEqual(r["tests"], "fail")
        lines = r["test_output"].splitlines()
        self.assertEqual(len(lines), 40)
        self.assertEqual(lines[-1], "99")

    def handoff(self, newline):
        path = os.path.join(self.work, "SESSION_HANDOFF.md")
        with open(path, "wb") as f:
            f.write("# H\n\n## Progress Log\n- old\n".replace("\n", newline).encode("utf-8"))
        return path

    def check_log(self, path, newline):
        with open(path, "rb") as f:
            data = f.read()
        if newline == "\r\n":
            self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        else:
            self.assertNotIn(b"\r", data)
        lines = data.decode("utf-8").splitlines()
        self.assertEqual(lines[-2], "- old")
        self.assertRegex(lines[-1], self.LINE)

    def test_handoff_line_appended_lf(self):
        path = self.handoff("\n")
        self.merged()
        code, r = self.cleanup()
        self.assertEqual(code, 0, r)
        self.check_log(path, "\n")

    def test_handoff_line_appended_crlf(self):
        path = self.handoff("\r\n")
        self.merged()
        code, r = self.cleanup()
        self.assertEqual(code, 0, r)
        self.check_log(path, "\r\n")

    def test_handoff_flag_creates_the_log(self):
        path = os.path.join(self.tmp.name, "other.md")
        self.merged()
        code, r = self.cleanup("--handoff", path)
        self.assertEqual(code, 0, r)
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0], "## Progress Log")
        self.assertRegex(lines[1], self.LINE)

    def test_no_handoff_file_means_no_write(self):
        self.merged()
        self.cleanup()
        self.assertFalse(os.path.exists(os.path.join(self.work, "SESSION_HANDOFF.md")))

    def test_dry_run_runs_no_tests_and_writes_no_handoff(self):
        path = self.handoff("\n")
        self.merged()
        code, r = self.cleanup("--dry-run", "--test", '"%s" -c "raise SystemExit(3)"' % sys.executable)
        self.assertEqual(code, 0, r)
        self.assertEqual(r["tests"], "skipped")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read().splitlines()[-1], "- old")

    def test_human_output(self):
        self.merged()
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("deleted: feat/a (PR #1 merged)", proc.stdout)
        self.assertIn("tests: skipped", proc.stdout)

    def test_human_output_for_a_refusal(self):
        self.live_session(self.work)
        proc = self.run_script()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("refused", proc.stdout)
```

- [ ] **Step 2: Run them to watch them fail.** Run `python -m unittest tests.test_post_merge_cleanup.FinishTest -v`. Expected failures:
  - `pulled` is False;
  - `tests` is `skipped` where `ok` or `fail` is expected;
  - the handoff isn't written;
  - the human output is JSON.

- [ ] **Step 3: Implement.** Replace `finish` and `render` in `scripts/post_merge_cleanup.py`:

```python
def progress_line(report):
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    deleted = ", ".join(d["branch"] for d in report["deleted"]) or "none"
    kept = ", ".join("%s (%s)" % (k["branch"], k["reason"]) for k in report["kept"]) or "none"
    return "- %s | post-merge cleanup | tests %s | %s | deleted: %s; kept: %s" % (
        stamp, report["tests"], report["sha"], deleted, kept)


def finish(repo, default, args, report):
    """Fast-forward the default branch, run the tests, and log to the handoff."""
    if not args.dry_run:
        on_default = git(repo, "branch", "--show-current") == default
        if on_default and not dirty(repo, untracked=False):
            pulled = run(["git", "pull", "--ff-only", "--quiet"], repo, check=False)
            report["pulled"] = pulled.returncode == 0
            if pulled.returncode != 0:
                report["notes"].append("pull failed: " + pulled.stderr.strip())
        else:
            report["notes"].append("main checkout is not clean on %s: pull skipped" % default)
        git(repo, "worktree", "prune")
    report["sha"] = git(repo, "rev-parse", "--short", "HEAD")

    if args.test and not args.dry_run:
        try:
            proc = subprocess.run(args.test, shell=True, cwd=repo, capture_output=True,
                                  encoding="utf-8", errors="replace", timeout=TEST_TIMEOUT)
            output, code = (proc.stdout or "") + (proc.stderr or ""), proc.returncode
        except subprocess.TimeoutExpired:
            output, code = "timed out after %d s" % TEST_TIMEOUT, 1
        report["tests"] = "ok" if code == 0 else "fail"
        if code != 0:
            report["test_output"] = "\n".join(output.splitlines()[-TEST_TAIL:])

    handoff = args.handoff or os.path.join(repo, "SESSION_HANDOFF.md")
    if not args.dry_run and (args.handoff or os.path.isfile(handoff)):
        handoff_state.append_progress(handoff, progress_line(report))


def render(report):
    if report.get("refused"):
        return "post-merge cleanup refused: " + report["refused"]
    if report.get("error"):
        return "post-merge cleanup failed: " + report["error"]
    lines = ["%spost-merge cleanup: %s (default %s, HEAD %s)" % (
        "DRY RUN, nothing changed. " if report["dry_run"] else "",
        report["repo"], report["default"], report["sha"])]
    lines.append("deleted: " + (", ".join("%s (%s)" % (d["branch"], d["reason"])
                                          for d in report["deleted"]) or "none"))
    lines.append("kept: " + (", ".join("%s (%s)" % (k["branch"], k["reason"])
                                       for k in report["kept"]) or "none"))
    if report["removed_worktrees"]:
        lines.append("worktrees removed: " + ", ".join(report["removed_worktrees"]))
    lines.append("pulled: " + ("yes" if report["pulled"] else "no"))
    lines.append("tests: " + report["tests"])
    if report["test_output"]:
        lines.append(report["test_output"])
    lines += ["note: " + note for note in report["notes"]]
    return "\n".join(lines)
```

- [ ] **Step 4: Run the tests to watch them pass.** Run `python -m unittest tests.test_post_merge_cleanup -v`. Expected: all pass, including every `BranchTest`, `WorktreeTest` and `RefusalTest`.

- [ ] **Step 5: Run the full suite.** Run `python -m unittest discover -s tests -v`. Expected: all pass.

- [ ] **Step 6: Commit.** Stage `scripts/post_merge_cleanup.py tests/test_post_merge_cleanup.py` and commit with the message `Post-merge cleanup: pull, run tests and log to the handoff`.

- [ ] **Step 7: Ship Part A.** Follow "Workspace and shipping each part" (manual pr-review-loop; `/ship` doesn't exist yet). Acceptance: the cleanup suite passes on both CI jobs.

# Part B: ship loop (PR 2, branch `feat/ship-loop`)

### Task 5: PR-created hook (`hooks/pr_created.py`)

**Files:**
- Create: `hooks/pr_created.py`
- Create: `tests/test_pr_created.py`
- Create: `tests/test_settings.py`
- Modify: `settings.json` (`hooks.PostToolUse`)

**Interfaces:**
- Produces:
  - On a non-draft `gh pr create` whose output holds a PR URL, the hook prints `{"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "PR #N opened (<url>). Continue in this turn: invoke the pr-review-loop skill with `ship #N`."}}`.
  - Marker directory: `CLAUDE_SHIP_STATE_DIR`, otherwise `~/.claude/state/ship`. The marker file is named after the hook input's `session_id`. Task 7's `/ship` writes and removes it as `$HOME/.claude/state/ship/${CLAUDE_SESSION_ID}`; Claude Code substitutes the same session ID into the skill text (a documented skill string substitution, https://code.claude.com/docs/en/skills). Task 7's `test_ship_marker_path_is_the_one_pr_created_reads` checks the two paths agree. A missing marker fails safe: the hook repeats the hand-off, which ship mode ignores as a duplicate.

- [ ] **Step 1: Write the failing hook tests** in `tests/test_pr_created.py`:

```python
#!/usr/bin/env python3
"""
Tests for hooks/pr_created.py. Runs the hook as a subprocess with synthetic
PostToolUse input, the way Claude Code does.

    python -m unittest discover -s tests
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "pr_created.py")
URL = "https://github.com/owner/repo-name/pull/42"


class PrCreatedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.state = os.path.join(self.tmp.name, "ship")

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, command="gh pr create --title T --body B", stdout=URL + "\n", tool="Bash",
                 env=None, **extra):
        payload = {"session_id": "s1", "hook_event_name": "PostToolUse", "tool_name": tool,
                   "tool_input": {"command": command},
                   "tool_response": {"stdout": stdout, "stderr": "", "interrupted": False}, **extra}
        full_env = dict(os.environ, CLAUDE_SHIP_STATE_DIR=self.state)
        full_env.pop("CLAUDE_SHIP_DISABLE", None)
        full_env.update(env or {})
        proc = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                              encoding="utf-8", env=full_env, timeout=20)
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))
        out = proc.stdout.strip()
        return json.loads(out)["hookSpecificOutput"] if out else None

    def assertShip(self, out, number="42"):
        self.assertIsNotNone(out, "expected a ship nudge")
        self.assertEqual(out["hookEventName"], "PostToolUse")
        self.assertIn("PR #%s opened" % number, out["additionalContext"])
        self.assertIn("`ship #%s`" % number, out["additionalContext"])
        self.assertIn("pr-review-loop", out["additionalContext"])

    def test_nudges_after_a_pr_opens(self):
        self.assertShip(self.run_hook())

    def test_powershell_tool(self):
        self.assertShip(self.run_hook(tool="PowerShell"))

    def test_string_tool_response(self):
        payload_out = self.run_hook(stdout=None, tool_response=URL)  # response given as a bare string
        self.assertShip(payload_out)

    def test_last_url_wins(self):
        out = self.run_hook(stdout="see https://github.com/o/r/pull/7\nhttps://github.com/o/r/pull/8\n")
        self.assertShip(out, "8")

    def test_silent_without_a_url(self):
        self.assertIsNone(self.run_hook(stdout="error: no commits\n"))

    def test_silent_for_a_draft(self):
        for flag in ("--draft", "-d", "--draft=true"):
            self.assertIsNone(self.run_hook(command="gh pr create %s --title T" % flag), flag)

    def test_draft_word_inside_quoted_body_is_not_a_draft(self):
        self.assertShip(self.run_hook(command='gh pr create --title T --body "use -d or --draft next time"'))

    def test_compound_command(self):
        self.assertShip(self.run_hook(command="git push -u origin HEAD && gh pr create --title T --body B"))

    def test_silent_when_the_command_is_not_gh_pr_create(self):
        self.assertIsNone(self.run_hook(command="gh pr view 42"))

    def test_silent_in_a_subagent(self):
        self.assertIsNone(self.run_hook(agent_id="a1", agent_type="implementer"))

    def test_silent_when_disabled(self):
        self.assertIsNone(self.run_hook(env={"CLAUDE_SHIP_DISABLE": "1"}))

    def test_silent_inside_ship(self):
        os.makedirs(self.state)
        open(os.path.join(self.state, "s1"), "w").close()
        self.assertIsNone(self.run_hook())

    def test_stale_ship_marker_is_ignored(self):
        os.makedirs(self.state)
        marker = os.path.join(self.state, "s1")
        open(marker, "w").close()
        old = time.time() - 2 * 3600
        os.utime(marker, (old, old))
        self.assertShip(self.run_hook())

    def test_garbage_stdin_is_silent(self):
        proc = subprocess.run([sys.executable, HOOK], input="not json", capture_output=True,
                              encoding="utf-8", timeout=20)
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, "", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

`run_hook(stdout=None, tool_response=URL)` passes `tool_response` through `**extra`, where it overrides the dict default. That's how the string-response case is built.

- [ ] **Step 2: Write the failing settings tests** in `tests/test_settings.py`:

```python
#!/usr/bin/env python3
"""
settings.json wiring: every hook script is registered, every registered hook
exists, and the command-scoped hooks carry the right `if` rules.

    python -m unittest discover -s tests
"""

import json
import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SettingsTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(REPO, "settings.json"), encoding="utf-8") as f:
            self.hooks = json.load(f)["hooks"]

    def handlers(self, event):
        return [(group.get("matcher"), h) for group in self.hooks.get(event, []) for h in group["hooks"]]

    def test_every_hook_script_is_registered(self):
        registered = json.dumps(self.hooks)
        for name in sorted(os.listdir(os.path.join(REPO, "hooks"))):
            if name.endswith(".py"):
                self.assertIn('/.claude/hooks/%s' % name, registered, name)

    def test_every_registered_hook_exists(self):
        for name in re.findall(r"/\.claude/hooks/([\w.-]+\.py)", json.dumps(self.hooks)):
            self.assertTrue(os.path.isfile(os.path.join(REPO, "hooks", name)), name)

    def test_pr_created_runs_only_after_gh_pr_create(self):
        found = [(m, h.get("if")) for m, h in self.handlers("PostToolUse") if "pr_created.py" in h["command"]]
        self.assertEqual(sorted(i for _, i in found), ["Bash(gh pr create *)", "PowerShell(gh pr create *)"])
        for matcher, _ in found:
            self.assertEqual(matcher, "Bash|PowerShell")


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 3: Run them to watch them fail.** Run `python -m unittest tests.test_pr_created tests.test_settings -v`. Expected:
  - the pr_created tests fail with `can't open file ... pr_created.py` (exit 2);
  - `test_pr_created_runs_only_after_gh_pr_create` fails with `[] != [...]`.

- [ ] **Step 4: Implement** `hooks/pr_created.py`:

```python
#!/usr/bin/env python3
"""
PR-created hook (PostToolUse; `if: Bash(gh pr create *)` and `PowerShell(gh pr create *)`).

After a session opens a non-draft PR, tells Claude to carry straight on into
the ship loop: pr-review-loop with `ship #N`, which reviews, fixes and merges
it. Silent when:
- there's no PR URL in the tool output;
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
    urls = list(PR_URL.finditer(json.dumps(data.get("tool_response"))))
    if not urls:
        return
    if in_ship(data.get("session_id") or ""):
        return
    last = urls[-1]
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": MESSAGE.format(n=last.group(1), url=last.group(0)),
    }}))
    sys.stdout.flush()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # never break a tool call over a nudge
```

- [ ] **Step 5: Register the hook.** In `settings.json`, add a second group to `hooks.PostToolUse` after the existing `context_budget` group:

```json
      {
        "matcher": "Bash|PowerShell",
        "hooks": [
          {
            "type": "command",
            "if": "Bash(gh pr create *)",
            "command": "python \"$HOME/.claude/hooks/pr_created.py\"",
            "timeout": 10
          },
          {
            "type": "command",
            "if": "PowerShell(gh pr create *)",
            "command": "python \"$HOME/.claude/hooks/pr_created.py\"",
            "timeout": 10
          }
        ]
      }
```

- [ ] **Step 6: Run the tests to watch them pass.** Run `python -m unittest tests.test_pr_created tests.test_settings -v`. Expected: all pass.

- [ ] **Step 7: Commit.** Stage `hooks/pr_created.py settings.json tests/test_pr_created.py tests/test_settings.py` and commit with the message `Add the PR-created hook that hands a new PR to the ship loop`.

### Task 6: Stop guard, "No response requested" branch (`hooks/stop_guard.py`)

**Files:**
- Create: `hooks/stop_guard.py`
- Create: `tests/test_stop_guard.py`
- Modify: `settings.json` (add `hooks.Stop`)
- Modify: `tests/test_settings.py` (add a Stop assertion)

**Interfaces:**
- Produces: `{"decision": "block", "reason": REASON}` on stdout, or nothing.
- Task 11 adds the autopilot branch to this file and its tests.

- [ ] **Step 1: Write the failing tests** in `tests/test_stop_guard.py`:

```python
#!/usr/bin/env python3
"""
Tests for hooks/stop_guard.py. Runs the hook as a subprocess with synthetic
Stop input.

    python -m unittest discover -s tests
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "stop_guard.py")


class StopGuardCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.env = dict(os.environ, STOP_GUARD_STATE_DIR=os.path.join(self.tmp.name, "state"))
        for key in ("CLAUDE_AUTOPILOT", "CLAUDE_AUTOPILOT_HANDOFF"):
            self.env.pop(key, None)

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, message="Done.", active=False, **extra):
        payload = {"session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": active,
                   "last_assistant_message": message, **extra}
        proc = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                              encoding="utf-8", env=self.env, timeout=20)
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))
        out = proc.stdout.strip()
        return json.loads(out) if out else None


class NoResponseTest(StopGuardCase):
    def test_blocks_no_response_requested(self):
        for message in ("No response requested.", "no response requested", "  No response requested.\n"):
            out = self.run_hook(message)
            self.assertEqual(out["decision"], "block", message)
            self.assertIn("continue the skill or task", out["reason"])

    def test_allows_a_second_stop_in_the_chain(self):
        self.assertIsNone(self.run_hook("No response requested.", active=True))

    def test_allows_normal_messages(self):
        for message in ("Done.", "No response requested. Waiting on CI.", "", None):
            self.assertIsNone(self.run_hook(message), message)

    def test_silent_in_a_subagent(self):
        self.assertIsNone(self.run_hook("No response requested.", agent_id="a1"))

    def test_garbage_stdin_is_silent(self):
        proc = subprocess.run([sys.executable, HOOK], input="not json", capture_output=True,
                              encoding="utf-8", env=self.env, timeout=20)
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, "", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

Add to `SettingsTest` in `tests/test_settings.py`:

```python
    def test_stop_guard_runs_on_stop(self):
        commands = [h["command"] for _, h in self.handlers("Stop")]
        self.assertTrue(any("stop_guard.py" in c for c in commands), commands)
```

- [ ] **Step 2: Run them to watch them fail.** Run `python -m unittest tests.test_stop_guard tests.test_settings -v`. Expected: the hook tests fail because `hooks/stop_guard.py` is missing, and `test_stop_guard_runs_on_stop` fails. (`test_every_hook_script_is_registered` would fail between Steps 3 and 4, until the hook is registered.)

- [ ] **Step 3: Implement** `hooks/stop_guard.py`:

```python
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
```

- [ ] **Step 4: Register the hook.** In `settings.json`, add a `Stop` key after `PostToolUse` inside `hooks`:

```json
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python \"$HOME/.claude/hooks/stop_guard.py\"",
            "timeout": 10
          }
        ]
      }
    ]
```

- [ ] **Step 5: Run the tests to watch them pass.** Run `python -m unittest tests.test_stop_guard tests.test_settings -v`. Expected: all pass.

- [ ] **Step 6: Commit.** Stage `hooks/stop_guard.py settings.json tests/test_stop_guard.py tests/test_settings.py` and commit with the message `Add a Stop hook that won't let a turn die on "No response requested"`.

### Task 7: `/ship`, pr-review-loop ship mode, and the CLAUDE.md rule

**Files:**
- Create: `skills/ship/SKILL.md`
- Modify: `skills/pr-review-loop/SKILL.md` (frontmatter, "Interpret the arguments", Phase 2, Phase 4 step 2, Phase 4 exit, new Phase 6, Guardrails)
- Modify: `CLAUDE.md` (one line under `## Git`)
- Create: `tests/test_skill_files.py`

**Interfaces:**
- Consumes:
  - `scripts/post_merge_cleanup.py --repo . --here --test CMD` (Tasks 3–4);
  - the marker directory `~/.claude/state/ship/<session id>` (Task 5).
- Produces:
  - `/ship` (model-invocable);
  - `pr-review-loop` taking the `ship` argument (model-invocable).
  - `tests/test_skill_files.py`, with `frontmatter(path) -> dict`, used by Tasks 8, 9 and 13.

- [ ] **Step 1: Write the failing tests** in `tests/test_skill_files.py`:

```python
#!/usr/bin/env python3
"""
Skill and agent files: frontmatter is well formed, names match their files,
and the automation wiring between skills, hooks and scripts holds.

    python -m unittest discover -s tests
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def frontmatter(path):
    """Top-level `key: value` pairs of a markdown file's frontmatter; `- item` lines become lists."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if not text.startswith("---\n"):
        raise AssertionError("%s has no frontmatter" % path)
    block = text[4:].split("\n---\n", 1)[0]
    data, key = {}, None
    for line in block.splitlines():
        if line.startswith("  - ") and key:
            data.setdefault(key, [])
            if isinstance(data[key], list):
                data[key].append(line[4:].strip())
            continue
        name, _, value = line.partition(":")
        key = name.strip()
        value = value.strip()
        data[key] = value[1:-1] if len(value) > 1 and value[0] == value[-1] == '"' else (value or [])
    return data


def body(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def tracked(pattern):
    proc = subprocess.run(["git", "-C", REPO, "ls-files", "--cached", "--others", "--exclude-standard",
                           pattern], capture_output=True, encoding="utf-8", check=True)
    return sorted(line for line in proc.stdout.splitlines() if line)


def skill(name):
    return os.path.join(REPO, "skills", name, "SKILL.md")


class SkillFilesTest(unittest.TestCase):
    def test_every_skill_has_a_matching_name_and_a_description(self):
        for rel in tracked("skills/*/SKILL.md"):
            data = frontmatter(os.path.join(REPO, rel))
            self.assertEqual(data.get("name"), rel.split("/")[1], rel)
            self.assertTrue(data.get("description"), rel)

    def test_scripts_named_by_skills_exist(self):
        for rel in tracked("skills/*/SKILL.md"):
            for name in re.findall(r"\.claude/scripts/([\w.-]+\.py)", body(os.path.join(REPO, rel))):
                self.assertTrue(os.path.isfile(os.path.join(REPO, "scripts", name)), "%s: %s" % (rel, name))


class ShipLoopTest(unittest.TestCase):
    def test_pr_review_loop_is_model_invocable_with_ship(self):
        data = frontmatter(skill("pr-review-loop"))
        self.assertEqual(data.get("disable-model-invocation"), "false")
        self.assertIn("ship", data.get("argument-hint", ""))
        self.assertIn("Bash(python:*)", data.get("allowed-tools", []))

    def test_pr_review_loop_merges_only_through_phase_6(self):
        text = body(skill("pr-review-loop"))
        self.assertIn("## Phase 6", text)
        self.assertIn("gh pr merge --squash <PR>", text)
        self.assertIn("no-automerge", text)
        self.assertIn("mergeStateStatus", text)
        self.assertIn('post_merge_cleanup.py" --repo . --here', text)
        self.assertNotIn("sleep 30\n   done | while", text)  # the old fixed 5-minute window is gone

    def test_a_failed_review_status_query_is_never_clean(self):
        # gh auth/API/network failures must not read as "0 pending" and lead to a merge.
        text = body(skill("pr-review-loop"))
        self.assertNotIn("|| echo 0", text)
        self.assertIn("|| { echo error; return; }", text)
        self.assertIn('[ "$w" = error ] && echo "review status unknown: gh query failed"', text)
        self.assertIn("didn't print `review status unknown`", text)  # Clean exit and Phase 6 condition
        self.assertIn("blocked: review status unknown (gh query failed)", text)

    def test_ship_skill_hands_off_and_marks_the_session(self):
        data = frontmatter(skill("ship"))
        self.assertNotEqual(data.get("disable-model-invocation"), "true")
        text = body(skill("ship"))
        self.assertIn("state/ship/${CLAUDE_SESSION_ID}", text)
        self.assertIn("ship #", text)
        self.assertIn("Never `--draft`", text)
        self.assertNotIn("gh pr create --draft", text)
        self.assertIn("that's the same hand-off: invoke pr-review-loop once", text)

    def test_ship_marker_path_is_the_one_pr_created_reads(self):
        # ${CLAUDE_SESSION_ID} is a documented skill-text substitution for the current session ID
        # (https://code.claude.com/docs/en/skills, "Available string substitutions"); pr_created.py
        # gets the same ID as the hook input's `session_id`. Build the path the skill text names,
        # with the ID substituted, and check that the hook stays quiet on it.
        text = body(skill("ship"))
        marker = '"$HOME/.claude/state/ship/${CLAUDE_SESSION_ID}"'
        self.assertIn("touch %s" % marker, text)
        self.assertIn("rm -f %s" % marker, text)
        session = "0b6f3c1e-7a1d-4c3e-9a55-1f0e2d3c4b5a"
        payload = json.dumps({"session_id": session, "hook_event_name": "PostToolUse", "tool_name": "Bash",
                              "tool_input": {"command": "gh pr create --title T --body-file b.md"},
                              "tool_response": {"stdout": "https://github.com/o/r/pull/5\n", "stderr": ""}})
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as home:
            env = dict(os.environ, HOME=home, USERPROFILE=home)  # expanduser("~") on POSIX and Windows
            for key in ("CLAUDE_SHIP_STATE_DIR", "CLAUDE_SHIP_DISABLE"):
                env.pop(key, None)

            def hook():
                proc = subprocess.run([sys.executable, os.path.join(REPO, "hooks", "pr_created.py")],
                                      input=payload, capture_output=True, encoding="utf-8", env=env, timeout=20)
                return proc.stdout

            self.assertIn("`ship #5`", hook())  # no marker: the hook hands off (a duplicate is tolerated)
            path = marker.strip('"').replace("$HOME", home).replace("${CLAUDE_SESSION_ID}", session)
            os.makedirs(os.path.dirname(path))
            open(path, "w").close()
            self.assertEqual(hook(), "")  # inside /ship: quiet

    def test_ship_mode_ignores_a_duplicate_hand_off(self):
        self.assertIn("is a duplicate: ignore it", body(skill("pr-review-loop")))

    def test_claude_md_names_the_ship_loop(self):
        self.assertIn("Open PRs with `/ship`", body(os.path.join(REPO, "CLAUDE.md")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run them to watch them fail.** Run `python -m unittest tests.test_skill_files -v`. Expected:
  - `SkillFilesTest` passes, since it covers the current skills;
  - every `ShipLoopTest` test fails, with missing `ship`, `disable-model-invocation: true` or a missing `skills/ship/SKILL.md`.

- [ ] **Step 3: Create** `skills/ship/SKILL.md`:

````markdown
---
name: ship
description: Ship the current branch - run the gates and one /code-review --fix, open a non-draft PR, then hand off to pr-review-loop in ship mode, which addresses review feedback and squash-merges. Use when a branch's work is done and should become a merged PR.
argument-hint: "[notes for the PR title or body]"
allowed-tools:
  - Bash(gh:*)
  - Bash(git:*)
  - Bash(python:*)
  - Bash(npm:*)
  - Bash(npx:*)
  - Bash(pnpm:*)
  - Bash(mkdir:*)
  - Bash(touch:*)
  - Bash(rm:*)
  - Read
  - Write
  - Edit
  - Grep
  - Glob
---

# Ship

## Context (preloaded; every line falls back instead of failing)
- Branch: !`git branch --show-current 2>/dev/null || echo "none"`
- Default branch: !`gh repo view --json defaultBranchRef --jq .defaultBranchRef.name 2>/dev/null || git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null || echo "unknown"`
- Working tree: !`git status --short --branch 2>/dev/null || echo "not a git repo"`
- PR for this branch: !`gh pr view --json number,state,isDraft,baseRefName --jq '"#\(.number) \(.state) draft=\(.isDraft) base=\(.baseRefName)"' 2>/dev/null || echo "NONE"`
- Project manifests: !`git ls-files '*package.json' '*pnpm-lock.yaml' '*pyproject.toml' 'Makefile' '*/Makefile' 2>/dev/null | head -20 | grep . || echo "none"`
- Arguments: `$ARGUMENTS`

## 1. Check
- Stop if the branch is the default branch, or `git rev-list --count origin/<default>..HEAD` is 0.
- If the tree has uncommitted changes, ask whether they belong in this PR before going on.
- If the PR for this branch is OPEN and not a draft, skip to step 6 with its number. If it's a draft, say so and stop: drafts opt out of auto-merge.

## 2. Gates
Run the repo's gates with the same discovery as pr-review-loop Phase 2 step 4 (`package.json` scripts: lint → typecheck → test → build; `pyproject.toml` or `Makefile` commands; a repo test command in CLAUDE.md or AGENTS.md wins). All must pass. Remember the test command: Phase 6 hands it to the cleanup script.

## 3. Size the review
Run `git diff --shortstat origin/<default>...HEAD` and `git diff --name-only origin/<default>...HEAD`.
- **Skip** the review when every changed file is docs or config (`*.md`, `*.txt`, `docs/**`, `*.json`, `*.yml`, `*.yaml`, `*.toml`, `.github/**`, dotfiles), or insertions plus deletions are under 20.
- **high** when that total is over 300, or a changed path has a segment or name containing `auth`, `security`, `crypto`, `migration`, `db`, `database`, `storage`, `persist` or `schema`.
- **medium** otherwise.

## 4. Review
Unless skipped, invoke the `code-review` skill with `<level> <branch> --fix`, so it reviews the whole branch against the default branch and applies its findings. If it changed files:
- write a commit message to a scratchpad file (`fix(review): apply /code-review findings`) and commit with `git commit -F <file>`;
- re-run the gates; they must pass.

## 5. Open the PR
1. Mark this session as shipping, so the pr_created hook stays quiet:
   `mkdir -p "$HOME/.claude/state/ship" && touch "$HOME/.claude/state/ship/${CLAUDE_SESSION_ID}"`
   `${CLAUDE_SESSION_ID}` isn't an environment variable. Claude Code replaces it with the current session ID when it loads this skill ("Available string substitutions", https://code.claude.com/docs/en/skills), so the command already holds the literal ID. The hook reads the same ID from its input's `session_id` field ("Common input fields", https://code.claude.com/docs/en/hooks). If the marker is missing anyway, the hook's note only repeats step 6's hand-off (see step 6).
2. `git push -u origin HEAD`
3. Write the PR body to a scratchpad file with the Write tool:
   - what changed and why;
   - how it was tested (gate commands and results);
   - the review level, or "review skipped: docs/config only" or "under 20 lines".
   - No AI attribution.
4. `gh pr create --title "<imperative title>" --body-file <file>`:
   - Base: the default branch, unless this branch is stacked on another open PR's head branch; then pass `--base <that branch>`.
   - Never `--draft`.
5. `rm -f "$HOME/.claude/state/ship/${CLAUDE_SESSION_ID}"`

## 6. Hand off
In the same turn, invoke the `pr-review-loop` skill with `ship #<N>`. Don't stop between opening the PR and handing off. If the pr_created hook also asks for `ship #<N>` (its marker was missing), that's the same hand-off: invoke pr-review-loop once.
````

- [ ] **Step 4: Edit `skills/pr-review-loop/SKILL.md`.** Apply these six edits exactly.

(a) Frontmatter. Replace lines 3–5:

```
description: Continuous PR review loop — address every piece of review, bot, and CI feedback until the PR (or a stack of PRs) is clean, optionally rebasing a stack first. Run manually with /pr-review-loop.
argument-hint: "[max-iterations] [#PR ...] [extra instructions]"
disable-model-invocation: true
```

with:

```
description: Continuous PR review loop — address every piece of review, bot, and CI feedback until the PR (or a stack of PRs) is clean, optionally rebasing a stack first. With `ship`, squash-merge it once clean and run the post-merge cleanup. Invoke after a PR opens (the ship loop hands off here), or when asked to work through a PR's review feedback.
argument-hint: "[ship] [max-iterations] [#PR ...] [extra instructions]"
disable-model-invocation: false
```

Add `  - Bash(python:*)` to `allowed-tools` after `  - Bash(bash:*)`.

(b) "Interpret the arguments". Replace `- A leading integer is max iterations (default 25).` with:

```
- `ship` anywhere in the arguments enables Phase 6: once the loop exits clean, merge and clean up.
- A second `ship #N` for a PR this session's loop is already running on is a duplicate: ignore it and carry on with the running loop. That happens when the pr_created hook's note arrives after /ship's own hand-off.
- A bare integer is max iterations (default 25).
```

(c) Phase 2. After step 4's "Never push failing validation." bullet, and before "**Stop and ask first for:**", insert:

```
5. **Fix rounds get the gates only.** No local `/code-review` and no re-requested bot review for fix commits. One exception: record the PR head SHA when the loop starts. If this PR's fix commits change 50 or more lines outside tests and docs, and the repo has `.github/workflows/claude-code-review.yml`, post `@claude review` as a PR comment, once per PR:
   `git diff --numstat <start-sha>..HEAD -- . ':(exclude)*test*' ':(exclude)*.md' ':(exclude)docs/**' | awk '{s+=$1+$2} END {print s+0}'`
```

(d) Phase 4. Replace step 2, from `2. Wait for bot re-reviews with the **Monitor** tool` through `The \`?since=\` filter matches on update time, so a bot editing its summary in place shows up as a new line.`, with:

````
2. Wait for reviewers with the **Monitor** tool (`timeout_ms: 1140000`). The command below does two things, then prints `quiet window over` and exits:
   - It polls every 30 s until no review request is pending and every check run on the head SHA whose name contains `review` has finished, up to 15 minutes. It waits at least one minute, so a review request that registers late isn't missed.
   - Then it holds a 2-minute quiet window for summary edits.
   Throughout, it prints one line per new or edited review, inline comment or PR comment since the push commit.
   - A failed `gh` query (auth, API or network error) never counts as "nothing pending": `waiting` prints `error`, which isn't `0`, so the poll retries 30 s later. It prints `review status query failed` once. If the last poll still failed when the 15 minutes end, it prints `review status unknown: gh query failed`. That means the wait is **not clean**: don't merge, report it as blocked (see the exit rules below).
   ```bash
   sha=$(git rev-parse HEAD)
   since=$(TZ=UTC git log -1 --date=format-local:%Y-%m-%dT%H:%M:%SZ --format=%cd)
   events() {
     gh api "repos/{owner}/{repo}/pulls/<PR>/reviews" --jq ".[] | select(.submitted_at > \"$since\" and .user.login != \"<me>\") | \"review \(.id) by \(.user.login)\"" || true
     gh api "repos/{owner}/{repo}/pulls/<PR>/comments?since=$since" --jq ".[] | select(.user.login != \"<me>\") | \"inline \(.id) by \(.user.login) at \(.updated_at)\"" || true
     gh api "repos/{owner}/{repo}/issues/<PR>/comments?since=$since" --jq ".[] | select(.user.login != \"<me>\") | \"comment \(.id) by \(.user.login) at \(.updated_at)\"" || true
   }
   waiting() {   # pending review requests + unfinished review check runs on the head SHA; "error" if a query fails
     p=$(gh pr view <PR> --json reviewRequests --jq '.reviewRequests | length' 2>/dev/null) || { echo error; return; }
     r=$(gh api "repos/{owner}/{repo}/commits/$sha/check-runs" --jq '[.check_runs[] | select((.name | test("review"; "i")) and .status != "completed")] | length' 2>/dev/null) || { echo error; return; }
     case "$p$r" in ''|*[!0-9]*) echo error; return ;; esac   # empty or non-numeric output is a failure too
     echo $(( p + r ))
   }
   {
     w=error
     for i in $(seq 1 30); do
       events
       w=$(waiting)
       [ "$w" = error ] && echo "review status query failed"
       [ "$i" -ge 2 ] && [ "$w" = 0 ] && break
       sleep 30
     done
     [ "$w" = error ] && echo "review status unknown: gh query failed"
     for i in 1 2 3 4; do events; sleep 30; done
   } | while IFS= read -r ev; do   # print each event once
     case "$seen" in *"|$ev|"*) ;; *) seen="$seen|$ev|"; echo "$ev" ;; esac
   done
   echo "quiet window over"
   ```
   The `?since=` filter matches on update time, so a bot editing its summary in place shows up as a new line. `review status query failed` and `review status unknown: …` aren't feedback: don't return to Phase 1 for them.
````

(e) Phase 4 exit. Replace `- **Clean:** the sweep shows 0 open threads on every PR, CI is green, and no new feedback arrived during the quiet window.` with:

```
- **Clean:** the sweep shows 0 open threads on every PR, CI is green, no new feedback arrived during the quiet window, and the wait didn't print `review status unknown`. With `ship`, go to Phase 6, then Phase 5.
- **Review status unknown:** the wait printed `review status unknown: gh query failed`, so it never established that reviewers finished. That isn't clean. Run one more Phase 4 wait; if it prints the same line again, report "blocked: review status unknown (gh query failed)" and stop without merging.
```

Then insert this new section between Phase 5's closing paragraph (`...GraphQL is authoritative.`) and the `---` before `## Guardrails`:

````
---

## Phase 6 — Ship (only with `ship`)

Check every condition fresh, right before merging:
```bash
gh pr view <PR> --json isDraft,labels,baseRefName,mergeStateStatus,headRefOid --jq '{draft: .isDraft, labels: [.labels[].name], base: .baseRefName, state: .mergeStateStatus, head: .headRefOid}'
gh repo view --json defaultBranchRef --jq .defaultBranchRef.name
gh pr checks <PR> --json name,bucket --jq '[.[] | select(.bucket != "pass" and .bucket != "skipping")] | length'
bash "${CLAUDE_SKILL_DIR}/scripts/threads.sh" <PR>
```
Merge only when all of these hold:
- the PR isn't a draft and has no `no-automerge` label. Otherwise report "auto-merge off: <draft | no-automerge>" and stop.
- its base is the default branch. Otherwise report "stacked: run /finalize-stack" and stop.
- `head` equals `git rev-parse HEAD` (nothing landed since the last check);
- the checks query prints `0`;
- `threads.sh` prints nothing;
- no item in this loop's task list is waiting on the owner;
- the last Phase 4 wait didn't print `review status unknown`. Otherwise report "blocked: review status unknown (gh query failed)" and stop.
- `mergeStateStatus` is `CLEAN`. On `UNKNOWN`, re-query up to 3 times, 30 s apart. Any other state (`BLOCKED`, `BEHIND`, `DIRTY`, `UNSTABLE`): report it and stop.

If a condition fails for a reason only the owner can resolve, report it and stop without merging. Then:
```bash
gh pr merge --squash <PR>          # this argument order matches the permission allow rule
gh pr view <PR> --json mergeCommit --jq .mergeCommit.oid
python "$HOME/.claude/scripts/post_merge_cleanup.py" --repo . --here --test "<gate test command>"
```
Omit `--test` when the repo has no test command. Report the merge SHA and the cleanup summary. Cleanup exit 1 means the tests fail on the default branch after the merge: report that first, in bold. Exit 2 means the cleanup refused; report its reason.
````

(f) Guardrails. After `- Plain \`--force\` push, or \`--force-with-lease\` outside a user-requested rebase`, add:

```
- Merge without the `ship` argument, or while any Phase 6 condition fails
```

- [ ] **Step 5: Edit `CLAUDE.md`.** Under `## Git`, after the "Never push directly to `main`" line, add:

```
- Open PRs with `/ship`; after a PR opens, the ship loop reviews, fixes and merges it unless it's a draft or labelled `no-automerge`.
```

- [ ] **Step 6: Run the tests to watch them pass.** Run `python -m unittest tests.test_skill_files -v`, then `python -m unittest discover -s tests -v`. Expected: all pass.

- [ ] **Step 7: Commit.** Stage `skills/ship/SKILL.md skills/pr-review-loop/SKILL.md CLAUDE.md tests/test_skill_files.py` and commit with the message `Add /ship and pr-review-loop ship mode: review, fix and squash-merge a new PR`.

- [ ] **Step 8: Ship Part B.** Run the manual pr-review-loop on the PR, as in Part A. After the merge, pull `~/.claude`.

- [ ] **Step 9: Acceptance (owner gate). Run a real throwaway PR from `/ship` to merged.**
  1. The skills and hooks need to load, so ask the owner to start a fresh session in `~/.claude` (on `master`, clean).
  2. In that session: `git switch -c docs/readme-automation`.
  3. Add a README section, `## Automation`, with four bullets:
     - `/ship` opens a PR and the ship loop merges it;
     - `no-automerge` label or `--draft` to opt out;
     - `scripts/post_merge_cleanup.py` usage line;
     - hooks `pr_created.py` and `stop_guard.py`, one line each.
  4. Commit with `-F`, then invoke `/ship`.

  Pass criteria:
  - nothing is typed after `/ship`;
  - the PR is squash-merged;
  - `git branch --list docs/readme-automation` is empty;
  - `~/.claude` is on `master`, equal to `origin/master`.

  Record the PR number and merge SHA with `/handoff log`.

# Part C: stacked-PR finalizer (PR 3, branch `feat/finalize-stack`)

### Task 8: `/finalize-stack` skill

**Files:**
- Create: `skills/finalize-stack/SKILL.md`
- Modify: `tests/test_skill_files.py` (add `FinalizeStackTest`)

**Interfaces:**
- Consumes:
  - pr-review-loop Phase 1 and the thread protocol;
  - `threads.sh`;
  - `scripts/post_merge_cleanup.py --repo <root> --here --test CMD`.
- Produces: `/finalize-stack`, owner-invoked only.

- [ ] **Step 1: Write the failing test.** Add to `tests/test_skill_files.py`, above the `if __name__` block:

```python
class FinalizeStackTest(unittest.TestCase):
    def test_owner_invoked_only(self):
        self.assertEqual(frontmatter(skill("finalize-stack")).get("disable-model-invocation"), "true")

    def test_force_pushes_only_with_lease_and_merges_bottom_up(self):
        text = body(skill("finalize-stack"))
        self.assertIn("--force-with-lease", text)
        self.assertNotRegex(text, r"--force(?!-with-lease)")
        self.assertIn("git rebase --update-refs", text)
        self.assertIn("gh pr merge --squash <N>", text)
        self.assertIn("post_merge_cleanup.py", text)

    def test_reuses_a_checkout_that_already_holds_a_stack_branch(self):
        # `git worktree add <path> <branch>` fails when <branch> is checked out anywhere, e.g. when
        # the top branch is the current branch of the checkout /finalize-stack runs from.
        text = body(skill("finalize-stack"))
        self.assertIn("git worktree list --porcelain", text)
        self.assertIn("the top branch is this checkout's current branch", text)
        self.assertIn("git -C <path> status --porcelain --untracked-files=no", text)
        self.assertIn("with uncommitted changes; commit or stash them there", text)
        self.assertIn("In every PR's checkout except the top PR's, run `git switch --detach`", text)
```

- [ ] **Step 2: Run it to watch it fail.** Run `python -m unittest tests.test_skill_files.FinalizeStackTest -v`. Expected: `FileNotFoundError` for `skills/finalize-stack/SKILL.md`.

- [ ] **Step 3: Create** `skills/finalize-stack/SKILL.md`:

````markdown
---
name: finalize-stack
description: Finalize a stack of PRs - triage every PR's review feedback in parallel, batch the owner's rulings into one question, restack onto the default branch, then squash-merge bottom-up and run the post-merge cleanup. Owner-invoked; invoking it authorizes --force-with-lease pushes of the stack's branches.
argument-hint: "[#PR in the stack] [extra instructions]"
disable-model-invocation: true
allowed-tools:
  - Bash(gh:*)
  - Bash(git:*)
  - Bash(python:*)
  - Bash(bash:*)
  - Monitor
  - TaskStop
  - Agent
  - Read
  - Edit
  - Write
  - Grep
  - Glob
---

# Finalize a PR stack

## Context (preloaded; every line falls back instead of failing)
- Repo root: !`git rev-parse --show-toplevel 2>/dev/null || echo "not a git repo"`
- Default branch: !`gh repo view --json defaultBranchRef --jq .defaultBranchRef.name 2>/dev/null || echo "unknown"`
- My open PRs: !`gh pr list --author @me --state open --json number,headRefName,baseRefName,isDraft --jq '.[] | "#\(.number) \(.headRefName) -> \(.baseRefName) draft=\(.isDraft)"' 2>/dev/null | grep . || echo "none"`
- Working tree: !`git status --short --branch 2>/dev/null || echo "not a git repo"`
- Arguments: `$ARGUMENTS`

Invoking this skill authorizes `git push --force-with-lease` of the stack's branches; don't ask again before each one. Never a plain force push. The thread protocol, classification table and guardrails are pr-review-loop's: read `~/.claude/skills/pr-review-loop/SKILL.md` Phase 1, Phase 3 and Guardrails first.

## 1. Map the stack
A chain starts at a PR whose base is the default branch; each next PR's base is the previous PR's head branch. Build every chain from "My open PRs".
- `#N` in the arguments picks the chain holding it.
- With more than one chain and none picked, ask which (AskUserQuestion).
- A draft in the chain: ask whether to include it.

Print the map:
```
STACK (bottom → top)
#101 feat/a -> main
#102 feat/b -> feat/a
```

## 2. Triage in parallel
1. `git fetch origin`. The working tree must be clean; if it isn't, stop and ask.
2. Give each PR exactly one checkout: git refuses to check a branch out twice, so `git worktree add` fails for a branch that's already checked out, here or in another worktree. Run `git worktree list --porcelain`; each `worktree <path>` record lists its `branch refs/heads/<name>`. Then, for each PR:
   - **Its head branch is already checked out** (for example, the top branch is this checkout's current branch): use that checkout; don't add a worktree. It must be clean: `git -C <path> status --porcelain --untracked-files=no` prints nothing. Otherwise stop with "`<head>` is checked out in `<path>` with uncommitted changes; commit or stash them there, then re-run /finalize-stack". If it isn't this checkout and `python "$HOME/.claude/scripts/live_sessions.py" <path>` exits 1, stop with "another session is working in `<path>`".
   - **Otherwise:** `git worktree add <root>-s<N> <head-branch>`. The worktree is a sibling of the repo root at a short path, which avoids long-path failures. If the path exists, reuse it only when it's a clean worktree on that branch; otherwise stop and ask.
   Record each PR's checkout. "Its checkout" below means that path.
3. In one message, dispatch one agent per PR, in parallel. Use the repo's own implementer agent if it has one, otherwise `implementer`. Fill in this prompt:
   ```
   Triage review feedback for PR #<N> (<head> -> <base>). Work only in <its checkout>.
   1. Read ~/.claude/skills/pr-review-loop/SKILL.md sections 1.1-1.4 and run them for PR #<N>
      (threads: `bash ~/.claude/skills/pr-review-loop/scripts/threads.sh <N>`, run from that checkout).
   2. Classify every item: fix; decline (only with a valid reason from that skill's Guardrails);
      or needs-owner (architecture, breaking API, ambiguous feedback, taste calls).
   3. Apply the fixes. Run the gates: <gate commands>. Commit each logical fix with the message in a
      file and `git commit -F <file>`: `fix(review): <what> (addresses <reviewer> on <path>)`.
      No AI attribution. Don't push, reply to or resolve anything.
   4. Report JSON: {"pr": N, "commits": [{"sha": "", "message": "", "items": ["<thread or comment id>"]}],
      "declines": [{"item": "", "reason": ""}], "needs_owner": [{"item": "", "question": "", "options": [""]}],
      "gates": "<command>: <result>"}
   ```

## 3. Owner rulings
Collect every `needs_owner` item across the stack and ask them in one batch: AskUserQuestion allows up to 4 questions per call, so for more, make consecutive calls with nothing in between. Apply each ruling in its PR's checkout, run the gates and commit.

## 4. Restack
1. `git rebase --update-refs` won't move a branch that's checked out in another worktree. In every PR's checkout except the top PR's, run `git switch --detach` first. This includes this checkout when it holds a lower branch; when it holds the top branch, it's the top PR's checkout and stays attached.
2. In the top PR's checkout: `git rebase --update-refs origin/<default>`.
   - If the bottom PR was already squash-merged, cut its commits off instead: `git rebase --update-refs --onto origin/<default> <merged head SHA> <top-branch>`.
   - Resolve conflicts, preserving both sides' intent. An ambiguous conflict means stop and ask.
3. In each other PR's checkout, `git switch <its branch>`, then run the gates there.
4. Push each branch with `git push --force-with-lease origin <branch>` and note old → new SHA.
5. For each PR, once its checks register, run `gh pr checks <N> --watch --interval 30` (Bash, `run_in_background`).
6. After CI is green, follow pr-review-loop's thread protocol:
   - reply with the fix's post-rebase short SHA, or `Declining — <reason>`;
   - then resolve;
   - post one comment per PR listing its PR-level items.

## 5. Merge bottom-up
For the bottom PR, whose base is the default branch:
1. Check: the checks query prints 0 (`gh pr checks <N> --json bucket --jq '[.[] | select(.bucket != "pass" and .bucket != "skipping")] | length'`); `threads.sh <N>` prints nothing; `mergeStateStatus` is `CLEAN` (on `UNKNOWN`, re-query 3 times, 30 s apart).
2. Note its head SHA (`gh pr view <N> --json headRefOid --jq .headRefOid`), then run `gh pr merge --squash <N>`.
3. `git fetch origin`. Detach every remaining PR's checkout except the top PR's. In the top PR's checkout, run `git rebase --update-refs --onto origin/<default> <merged head SHA> <top-branch>`. Re-attach, run the gates, and push each remaining branch with `--force-with-lease`.
4. `gh pr view <next> --json baseRefName --jq .baseRefName`. GitHub retargets the next PR when the merged branch is deleted; if it didn't, run `gh pr edit <next> --base <default>`.
5. Wait for green CI on the next PR. Don't wait for bot re-reviews: fix commits get the gates only. Repeat from step 1 until the stack is merged.

Stop and ask when a conflict is ambiguous, or when CI still fails on a branch after two fix attempts.

## 6. Cleanup
From the repo root:
`python "$HOME/.claude/scripts/post_merge_cleanup.py" --repo <root> --here --test "<gate test command>"`
It removes the clean `-s<N>` worktrees and deletes the merged branches. A merged branch still checked out in this checkout is handled as the script's `--here` rules say. If `SESSION_HANDOFF.md` exists, run `/handoff log`.

## Report
```
STACK FINALIZED
═══════════════════════════════
Merged:        [#N → merge SHA, … bottom first]
Declined:      [item — reason, …]
Owner rulings: [question → answer, …]
Force-pushes:  [branch old → new, …]
Cleanup:       [deleted …; kept … (reason); tests ok|fail|skipped]
Stopped at:    [none | #N — why]
═══════════════════════════════
```
````

- [ ] **Step 4: Run the tests to watch them pass.** Run `python -m unittest tests.test_skill_files -v`. Expected: all pass.

- [ ] **Step 5: Commit.** Stage `skills/finalize-stack/SKILL.md tests/test_skill_files.py` and commit with the message `Add /finalize-stack: parallel triage, batched rulings, restack and bottom-up merge`.

- [ ] **Step 6: Ship Part C** with `/ship` from `~/.claude-wt`. Acceptance per the spec: it's proven on the next real stack, and no synthetic stack is built. Start that first run from the checkout whose current branch is the stack's top branch. Pass criterion: step 2 reuses that checkout instead of failing on `git worktree add`. Note "C: awaiting first real stack (run from the top branch's checkout)" in `/handoff log`.

---

# Part D: test-author agent (PR 4, branch `feat/test-author`)

### Task 9: `agents/test-author.md`, the implementer's locked-tests rule, and the CLAUDE.md rule

**Files:**
- Create: `agents/test-author.md`
- Modify: `agents/implementer.md` (`## Work`, a new step 2)
- Modify: `CLAUDE.md` (`## Sessions and subagents`)
- Modify: `tests/test_skill_files.py` (add `AgentFilesTest`)

**Interfaces:**
- Produces: the `test-author` agent. Its report ends with a `LOCKED FILES:` list, which the dispatcher passes to the implementer.

- [ ] **Step 1: Write the failing tests.** Add to `tests/test_skill_files.py`:

```python
class AgentFilesTest(unittest.TestCase):
    def agent(self, name):
        return os.path.join(REPO, "agents", name + ".md")

    def test_every_agent_has_a_matching_name_and_a_description(self):
        for rel in tracked("agents/*.md"):
            data = frontmatter(os.path.join(REPO, rel))
            self.assertEqual(data.get("name"), os.path.basename(rel)[:-3], rel)
            self.assertTrue(data.get("description"), rel)

    def test_test_author_runs_on_opus_with_file_tools(self):
        data = frontmatter(self.agent("test-author"))
        self.assertEqual(data.get("model"), "opus")
        self.assertEqual([t.strip() for t in data.get("tools", "").split(",")],
                         ["Read", "Grep", "Glob", "Write", "Edit", "Bash"])
        text = body(self.agent("test-author"))
        self.assertIn("LOCKED FILES", text)
        self.assertIn("fail for the right reason", text)

    def test_implementer_treats_locked_tests_as_read_only(self):
        self.assertIn("locked (from test-author) are read-only", body(self.agent("implementer")))

    def test_claude_md_dispatches_test_author_first(self):
        self.assertIn("dispatch `test-author` before the implementer", body(os.path.join(REPO, "CLAUDE.md")))
```

- [ ] **Step 2: Run them to watch them fail.** Run `python -m unittest tests.test_skill_files.AgentFilesTest -v`. Expected: `test_every_agent...` passes; the other three fail (a missing file, or missing text).

- [ ] **Step 3: Create** `agents/test-author.md`:

```markdown
---
name: test-author
description: Writes failing, adversarial tests for one planned task before it is implemented, and proves each fails for the right reason. Dispatch before the implementer for plan tasks that add or change behaviour; the test files it reports are then locked for the implementer.
model: opus
tools: Read, Grep, Glob, Write, Edit, Bash
---

You write the tests for exactly one task from an approved plan, before its implementation exists. Your tests are the contract the implementer must meet and may not edit, so they must come from the spec, not from any implementation.

## Input (from the dispatch)
- The task spec and its acceptance criteria
- Where the tests go, the test framework, and the test command
- Whether to commit

If any of these is missing or the spec is ambiguous, stop and report instead of guessing.

## Read
- The spec and task text.
- Existing tests and test helpers near where yours go, and follow their conventions.
- The public interfaces of the code under test: signatures, headers, exported types and CLI usage text. Read just enough for your tests to import or compile.

Don't read the implementation bodies of the code under test, and don't mirror an implementation's structure in a test.

## Write
1. At least one failing test per acceptance criterion.
2. Hostile cases for each defect class that applies to this task. List the classes you skipped, with the reason.
   - escaping and literal characters: quotes, backslashes, `\u` sequences, `$`, `%`, ellipses
   - CRLF versus LF, in input and output
   - focus and key leaks between overlays (UI only)
   - stale or missing store state
   - hardcoded shortcuts (Ctrl versus Cmd)
   - sRGB versus linear colour
   - async ordering: await the queued work, never a fixed sleep
   - empty, huge and unicode input
   - error paths: missing files, bad input, a failing subprocess
3. Tests are deterministic and independent of each other and of the machine: no network, no real home directory, and temp directories cleaned up.

## Prove
Run the test command. Every new test must fail, and fail for the right reason: an assertion, or a missing symbol, module or command that the task will add.
- A syntax error, a broken fixture path or an import error in your own helpers is your bug. Fix it and re-run.
- A new test that already passes is testing existing behaviour. Drop it, or keep it and say why.

## Commit
Only when the dispatch says so:
- on the current branch;
- message `test: <task>`, written to a file and committed with `git commit -F <file>`;
- no AI attribution;
- never push.

## Report
- Each test: its name, its category (an acceptance criterion or a defect class) and its expected failure line, quoted from your run
- Defect classes you skipped, with the reason for each
- The test command and a summary of its output: N failed, 0 errors from the tests themselves
- `LOCKED FILES:` then every test file you created or changed, one per line. The implementer may not edit these.
```

- [ ] **Step 4: Edit `agents/implementer.md`.** In `## Work`, replace step 2:

```
2. Where the repo has tests, write or extend a test first and watch it fail for the right reason, then implement until it passes.
```

with:

```
2. Test files the dispatch lists as locked (from test-author) are read-only. If one looks wrong, stop and report; don't edit it. Make them pass. Otherwise, where the repo has tests, write or extend a test first and watch it fail for the right reason, then implement until it passes.
```

- [ ] **Step 5: Edit `CLAUDE.md`.** Under `## Sessions and subagents`, after the "For implementation subagents…" line, add:

```
- For plan tasks that add or change behaviour, dispatch `test-author` before the implementer and pass its test files to the implementer as locked. Skip it for docs, config and pure refactors. A repo's own agents take precedence.
```

- [ ] **Step 6: Run the tests to watch them pass.** Run `python -m unittest discover -s tests -v`. Expected: all pass.

- [ ] **Step 7: Commit.** Stage `agents/test-author.md agents/implementer.md CLAUDE.md tests/test_skill_files.py` and commit with the message `Add the test-author agent and lock its tests for the implementer`.

- [ ] **Step 8: Ship Part D** with `/ship`. After the merge and the pull, the new agent is available.

- [ ] **Step 9: Acceptance: one test-author dispatch against a small task.**
  1. In the session scratchpad, create a scratch repo `ta-demo`:
     - run `git init`;
     - add `slug.py` containing `def slugify(text: str) -> str:` with the body `raise NotImplementedError`;
     - add an empty `tests/` directory;
     - commit.
  2. Dispatch `test-author` with this task. `slugify(text)`:
     - lowercases;
     - turns each run of characters that aren't letters or digits (Unicode-aware) into one `-`;
     - strips leading and trailing `-`;
     - returns `''` for empty or all-punctuation input.

     Tests go in `tests/test_slug.py` (unittest); the test command is `python -m unittest discover -s tests -v`; don't commit.

  Pass criteria:
  - every reported test fails with `NotImplementedError` or an assertion, never a syntax or import error in the test file;
  - the report has `LOCKED FILES: tests/test_slug.py` and names the defect classes it covered and skipped.

  Record the result with `/handoff log`.

# Part E: overnight autopilot (PR 5, branch `feat/autopilot`)

### Task 10: Handoff state reader (`scripts/handoff_state.py`: `autopilot_state`, `progress_count`, `status`)

**Files:**
- Modify: `scripts/handoff_state.py`
- Modify: `tests/test_handoff_state.py` (add `StateTest`)

**Interfaces:**
- Produces:
  - `autopilot_state(path) -> "continue" | "owner" | "done" | None`: read from the first `Autopilot: <value>` line above the first `## ` heading. Case-insensitive. Returns None when the file or line is missing, or the value isn't exactly one of the three.
  - `progress_count(path) -> int`: the number of `- ` lines in the Progress Log section.
  - CLI `python handoff_state.py status FILE`: prints `{"exists": bool, "autopilot": str|null, "progress": int}` and exits 0.

- [ ] **Step 1: Write the failing tests.** Add to `tests/test_handoff_state.py`, above the `if __name__` block (it reuses `hs` and `SCRIPT`):

```python
class StateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = os.path.join(self.tmp.name, "SESSION_HANDOFF.md")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text, newline="\n", bom=False):
        with open(self.path, "wb") as f:
            f.write((b"\xef\xbb\xbf" if bom else b"") + text.replace("\n", newline).encode("utf-8"))

    def test_reads_each_state(self):
        for value, expected in (("continue", "continue"), ("owner", "owner"), ("done", "done"),
                                ("Continue", "continue")):
            self.write("# Session Handoff\nAutopilot: %s\n\n## Summary\n" % value)
            self.assertEqual(hs.autopilot_state(self.path), expected, value)

    def test_missing_or_invalid_line(self):
        for text in ("# H\n\n## Summary\n", "# H\nAutopilot: maybe\n",
                     "# H\nAutopilot: continue | owner | done\n"):
            self.write(text)
            self.assertIsNone(hs.autopilot_state(self.path), text)
        self.assertIsNone(hs.autopilot_state(os.path.join(self.tmp.name, "nope.md")))

    def test_only_the_header_counts(self):
        self.write("# H\n\n## Summary\nAutopilot: continue\n")
        self.assertIsNone(hs.autopilot_state(self.path))

    def test_crlf_and_bom(self):
        self.write("Autopilot: owner\n# H\n", newline="\r\n", bom=True)
        self.assertEqual(hs.autopilot_state(self.path), "owner")

    def test_progress_count(self):
        self.write("# H\n\n## Progress Log\n- a\n- b\n\n## Next\n- c\n")
        self.assertEqual(hs.progress_count(self.path), 2)
        self.assertEqual(hs.progress_count(os.path.join(self.tmp.name, "nope.md")), 0)

    def test_cli_status(self):
        self.write("# H\nAutopilot: continue\n\n## Progress Log\n- a\n")
        proc = subprocess.run([sys.executable, SCRIPT, "status", self.path], capture_output=True,
                              encoding="utf-8", timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"exists": True, "autopilot": "continue", "progress": 1})
        proc = subprocess.run([sys.executable, SCRIPT, "status", self.path + ".x"], capture_output=True,
                              encoding="utf-8", timeout=30)
        self.assertEqual(json.loads(proc.stdout), {"exists": False, "autopilot": None, "progress": 0})
```

Add `import json` to the file's imports.

- [ ] **Step 2: Run them to watch them fail.** Run `python -m unittest tests.test_handoff_state.StateTest -v`. Expected: `AttributeError: module 'handoff_state' has no attribute 'autopilot_state'`, and exit 2 from the CLI.

- [ ] **Step 3: Implement.** In `scripts/handoff_state.py`:
  - add `import json` and `import re`;
  - extend the docstring's usage block with `python handoff_state.py status FILE   # JSON: exists, autopilot state, progress-line count`;
  - add these below `append_progress`, and replace `main`:

```python
AUTOPILOT = re.compile(r"^\s*Autopilot:\s*(\S+)\s*$", re.I)
STATES = ("continue", "owner", "done")


def autopilot_state(path):
    """'continue', 'owner' or 'done' from the `Autopilot:` line above the first `## ` heading;
    None when the file, the line or a valid value is missing."""
    lines, _ = _read(path)
    for line in lines:
        if line.startswith("## "):
            break
        match = AUTOPILOT.match(line.lstrip("﻿"))
        if match:
            value = match.group(1).lower()
            return value if value in STATES else None
    return None


def progress_count(path):
    """Number of `- ` entries in the Progress Log section."""
    lines, _ = _read(path)
    count, in_log = 0, False
    for line in lines:
        if line.startswith("## "):
            in_log = line.strip() == PROGRESS
        elif in_log and line.startswith("- "):
            count += 1
    return count


def main(argv):
    if len(argv) == 3 and argv[0] == "append":
        append_progress(argv[1], argv[2])
        return 0
    if len(argv) == 2 and argv[0] == "status":
        print(json.dumps({"exists": os.path.isfile(argv[1]), "autopilot": autopilot_state(argv[1]),
                          "progress": progress_count(argv[1])}))
        return 0
    print("usage: handoff_state.py append FILE LINE | status FILE", file=sys.stderr)
    return 2
```

- [ ] **Step 4: Run the tests to watch them pass.** Run `python -m unittest tests.test_handoff_state -v`. Expected: all pass.

- [ ] **Step 5: Commit.** Stage `scripts/handoff_state.py tests/test_handoff_state.py` and commit with the message `Read the handoff's Autopilot state and progress count`.

### Task 11: Stop guard, autopilot branch

**Files:**
- Modify: `hooks/stop_guard.py`
- Modify: `tests/test_stop_guard.py` (add `AutopilotStopTest`)

**Interfaces:**
- Consumes: `handoff_state.autopilot_state` (Task 10), imported from `../scripts` relative to the hook file.
- Produces: `{"decision": "block", "reason": AUTOPILOT_REASON}`. It's emitted only when all of these hold:
  - `CLAUDE_AUTOPILOT=1`;
  - `CLAUDE_AUTOPILOT_HANDOFF` names a handoff whose state is `continue`;
  - HEAD or the handoff's mtime changed since this session's last block.

  State files live in `STOP_GUARD_STATE_DIR`, otherwise `~/.claude/state/stop-guard/<session>.json`.

- [ ] **Step 1: Write the failing tests.** Add to `tests/test_stop_guard.py`: put `from stubs import git, git_env` with the imports, and this class above the `if __name__` block:

```python
class AutopilotStopTest(StopGuardCase):
    def setUp(self):
        super().setUp()
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(self.repo)
        self.env = git_env(self.tmp.name, STOP_GUARD_STATE_DIR=self.env["STOP_GUARD_STATE_DIR"])
        for key in ("CLAUDE_AUTOPILOT", "CLAUDE_AUTOPILOT_HANDOFF"):
            self.env.pop(key, None)
        git(self.repo, self.env, "init", "-q", "-b", "main")
        self.commit("one")
        self.handoff = os.path.join(self.repo, "SESSION_HANDOFF.md")
        self.set_state("continue")
        self.env.update(CLAUDE_AUTOPILOT="1", CLAUDE_AUTOPILOT_HANDOFF=self.handoff)

    def commit(self, text):
        with open(os.path.join(self.repo, "work.txt"), "a", encoding="utf-8") as f:
            f.write(text + "\n")
        git(self.repo, self.env, "add", "work.txt")
        git(self.repo, self.env, "commit", "-q", "-m", text)

    def set_state(self, state):
        with open(self.handoff, "w", encoding="utf-8", newline="\n") as f:
            f.write("# Session Handoff\n%s\n## Progress Log\n" % ("Autopilot: %s\n" % state if state else ""))

    def assertBlocks(self, out):
        self.assertIsNotNone(out, "expected a block")
        self.assertEqual(out["decision"], "block")
        self.assertIn("Autopilot: the handoff says continue", out["reason"])

    def test_blocks_while_the_handoff_says_continue(self):
        self.assertBlocks(self.run_hook())

    def test_allows_the_stop_when_nothing_changed_since_the_last_block(self):
        self.assertBlocks(self.run_hook())
        self.assertIsNone(self.run_hook(active=True))

    def test_blocks_again_after_a_commit(self):
        self.assertBlocks(self.run_hook())
        self.commit("two")
        self.assertBlocks(self.run_hook(active=True))

    def test_a_handoff_edit_counts_as_progress(self):
        self.assertBlocks(self.run_hook())
        with open(self.handoff, "a", encoding="utf-8") as f:
            f.write("- progress\n")
        later = os.path.getmtime(self.handoff) + 5
        os.utime(self.handoff, (later, later))
        self.assertBlocks(self.run_hook(active=True))

    def test_state_is_per_session(self):
        self.assertBlocks(self.run_hook(session_id="a"))
        self.assertBlocks(self.run_hook(session_id="b"))

    def test_allows_on_owner_done_or_no_line(self):
        for state in ("owner", "done", None):
            self.set_state(state)
            self.assertIsNone(self.run_hook(), state)

    def test_needs_the_autopilot_environment(self):
        del self.env["CLAUDE_AUTOPILOT"]
        self.assertIsNone(self.run_hook())

    def test_missing_handoff_allows(self):
        self.env["CLAUDE_AUTOPILOT_HANDOFF"] = os.path.join(self.repo, "nope.md")
        self.assertIsNone(self.run_hook())

    def test_no_response_still_wins(self):
        out = self.run_hook("No response requested.")
        self.assertIn("continue the skill or task", out["reason"])
```

`run_hook`'s payload sets `session_id: "s1"` before `**extra`, so passing `session_id=` overrides it. `git_env` rebuilds the environment with an isolated git config and keeps `STOP_GUARD_STATE_DIR`.

- [ ] **Step 2: Run them to watch them fail.** Run `python -m unittest tests.test_stop_guard -v`. Expected: the block tests fail with "expected a block"; `NoResponseTest` still passes.

- [ ] **Step 3: Implement.** In `hooks/stop_guard.py`:
  - add `import os` and `import subprocess`;
  - after the imports, add:

```python
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import handoff_state  # noqa: E402

AUTOPILOT_REASON = ("Autopilot: the handoff says continue. Dispatch the next task. If it needs the owner, "
                    "set `Autopilot: owner`, write the owner checklist, run /handoff, then stop.")


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
    if not os.path.isfile(handoff) or handoff_state.autopilot_state(handoff) != "continue":
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
```

Replace the body of `main` after the `agent_id` check with:

```python
    message = (data.get("last_assistant_message") or "").strip()
    if not data.get("stop_hook_active") and NO_RESPONSE.match(message):
        block(REASON)
    elif autopilot_block(data.get("session_id")):
        block(AUTOPILOT_REASON)
```

Extend the docstring with a paragraph. When `CLAUDE_AUTOPILOT=1` and the handoff named by `CLAUDE_AUTOPILOT_HANDOFF` says `Autopilot: continue`, the hook blocks the stop so the headless run dispatches the next task. Once a block brings no progress (HEAD and the handoff's mtime unchanged since that session's previous block), it lets the stop through, and Claude Code's 8-continuation cap backs that up. The state lives in `~/.claude/state/stop-guard/` (override with `STOP_GUARD_STATE_DIR`).

- [ ] **Step 4: Run the tests to watch them pass.** Run `python -m unittest tests.test_stop_guard -v`. Expected: all pass.

- [ ] **Step 5: Commit.** Stage `hooks/stop_guard.py tests/test_stop_guard.py` and commit with the message `Stop guard: keep an autopilot run going while the handoff says continue`.

### Task 12: `scripts/autopilot.ps1`

**Files:**
- Create: `scripts/autopilot.ps1`
- Create: `tests/fake_autopilot_claude.py`
- Create: `tests/test_autopilot.py`

**Interfaces:**
- Consumes:
  - `python scripts/handoff_state.py status|append` (Tasks 2 and 10);
  - `python scripts/live_sessions.py PATH` (Task 1);
  - `tests/stubs.py` (Task 3).
- Produces:
  - `pwsh scripts/autopilot.ps1 -Repo <path> [-MaxRuns 6] [-MaxTurns 200] [-BudgetUsd 25] [-Model opus]`.
  - Exit codes: 0 when it ends normally (`done`, `owner`, `no-autopilot-line` or `max-runs`); 1 on `stalled`, `budget` or `error…`; 2 when refused (a missing repo, not a git repo, or `live-session`).
  - Handoff summary: `- YYYY-MM-DD HH:MM | autopilot | <status> | runs N | $X.XX | <short SHA>`.
  - Test overrides: `AUTOPILOT_STATE_DIR`, `AUTOPILOT_MAX_SLEEP_SECONDS`, `CLAUDE_SESSIONS_DIR`.

- [ ] **Step 1: Write the fake `claude`** in `tests/fake_autopilot_claude.py`:

```python
"""Fake `claude` for the autopilot tests.

Each call takes the next run from the JSON list in FAKE_AUTOPILOT_RUNS (the call
count lives in FAKE_AUTOPILOT_COUNT). It then:
1. logs its argv, cwd and CLAUDE_AUTOPILOT* environment to FAKE_AUTOPILOT_LOG,
   one JSON line per call;
2. applies the run's effects to the repo in the current directory;
3. prints the run's events as stream-json;
4. exits with the run's code.

A run: {"events": [...], "exit": 0, "commit": false, "progress": null | "- line", "autopilot": null | "done"}
Calls past the end of the list exit 1 with no output.
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
    if run.get("progress"):
        handoff_state.append_progress(HANDOFF, run["progress"])
    if run.get("autopilot"):
        set_autopilot(run["autopilot"])
    for event in run.get("events", []):
        print(json.dumps(event))
        sys.stdout.flush()
    return run.get("exit", 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 2: Write the failing tests** in `tests/test_autopilot.py`:

```python
#!/usr/bin/env python3
"""
Tests for scripts/autopilot.ps1, run under pwsh with a scripted fake `claude`
first on PATH. Skipped where PowerShell 7 isn't installed.

    python -m unittest discover -s tests
"""

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from stubs import git, git_env, install_stub

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "scripts", "autopilot.ps1")
FAKE = os.path.join(REPO, "tests", "fake_autopilot_claude.py")
PWSH = shutil.which("pwsh")
SUMMARY = r"^- \d{4}-\d{2}-\d{2} \d{2}:\d{2} \| autopilot \| %s \| runs %d \| \$%s \| [0-9a-f]{7}$"


def result(cost=0.5, error=False, subtype="success", text="ok"):
    return {"type": "result", "subtype": subtype, "is_error": error, "total_cost_usd": cost,
            "result": text, "num_turns": 3}


def run(events=None, commit=True, progress=None, autopilot=None, exit=0):
    return {"events": [result()] if events is None else events, "commit": commit, "progress": progress,
            "autopilot": autopilot, "exit": exit}


@unittest.skipUnless(PWSH, "needs PowerShell 7 (pwsh)")
class AutopilotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        t = self.tmp.name
        bin_dir = os.path.join(t, "bin")
        install_stub(bin_dir, "claude", FAKE)
        self.state = os.path.join(t, "state")
        self.sessions = os.path.join(t, "sessions")
        os.makedirs(self.sessions)
        self.runs_file = os.path.join(t, "runs.json")
        self.calls_file = os.path.join(t, "calls.jsonl")
        path = os.pathsep.join([bin_dir, os.path.dirname(sys.executable), os.environ.get("PATH", "")])
        self.env = git_env(t, PATH=path, FAKE_AUTOPILOT_RUNS=self.runs_file,
                           FAKE_AUTOPILOT_COUNT=os.path.join(t, "count"), FAKE_AUTOPILOT_LOG=self.calls_file,
                           AUTOPILOT_STATE_DIR=self.state, AUTOPILOT_MAX_SLEEP_SECONDS="0",
                           CLAUDE_SESSIONS_DIR=self.sessions)
        for key in ("CLAUDE_AUTOPILOT", "CLAUDE_AUTOPILOT_HANDOFF"):
            self.env.pop(key, None)
        self.repo = os.path.join(t, "repo")
        os.makedirs(self.repo)
        git(self.repo, self.env, "init", "-q", "-b", "main")
        with open(os.path.join(self.repo, "README.md"), "w", encoding="utf-8") as f:
            f.write("x\n")
        git(self.repo, self.env, "add", "README.md")
        git(self.repo, self.env, "commit", "-q", "-m", "init")
        self.handoff = os.path.join(self.repo, "SESSION_HANDOFF.md")
        self.write_handoff("continue")

    def tearDown(self):
        self.tmp.cleanup()

    def write_handoff(self, state, newline="\n"):
        text = "# Session Handoff — test\n%s\n## Summary\nx\n\n## Progress Log\n" % (
            "Autopilot: %s\n" % state if state else "")
        with open(self.handoff, "wb") as f:
            f.write(text.replace("\n", newline).encode("utf-8"))

    def script(self, *runs):
        with open(self.runs_file, "w", encoding="utf-8") as f:
            json.dump(list(runs), f)

    def autopilot(self, *args):
        return subprocess.run([PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", SCRIPT,
                               "-Repo", self.repo, *args], cwd=self.tmp.name, env=self.env,
                              capture_output=True, encoding="utf-8", errors="replace", timeout=300)

    def calls(self):
        if not os.path.exists(self.calls_file):
            return []
        with open(self.calls_file, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def last_line(self):
        with open(self.handoff, encoding="utf-8") as f:
            return f.read().splitlines()[-1]

    def log_text(self):
        logs = glob.glob(os.path.join(self.state, "*.log"))
        self.assertEqual(len(logs), 1, logs)
        with open(logs[0], encoding="utf-8") as f:
            return f.read()

    def test_stops_when_the_handoff_says_done(self):
        self.script(run(autopilot="done"))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 1)
        self.assertRegex(self.last_line(), SUMMARY % ("done", 1, r"0\.50"))

    def test_passes_the_headless_flags_and_environment(self):
        self.script(run(autopilot="done"))
        self.autopilot()
        call = self.calls()[0]
        self.assertEqual(call["argv"], ["-p", "/resume autopilot", "--permission-mode", "auto",
                                        "--model", "opus", "--max-turns", "200", "--max-budget-usd", "25.00",
                                        "--output-format", "stream-json", "--verbose"])
        self.assertEqual(call["autopilot"], "1")
        self.assertTrue(os.path.samefile(call["handoff"], self.handoff))
        self.assertTrue(os.path.samefile(call["cwd"], self.repo))

    def test_owner_or_missing_line_starts_no_run(self):
        for state, status in (("owner", "owner"), (None, "no-autopilot-line")):
            self.write_handoff(state)
            self.script(run())
            proc = self.autopilot()
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(self.calls(), [])
            self.assertRegex(self.last_line(), SUMMARY % (status, 0, r"0\.00"))

    def test_max_runs(self):
        self.script(run(), run(), run())
        proc = self.autopilot("-MaxRuns", "2")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 2)
        self.assertRegex(self.last_line(), SUMMARY % ("max-runs", 2, r"1\.00"))

    def test_stalled_after_two_idle_runs(self):
        self.script(run(commit=False), run(commit=False), run(commit=False))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 2)
        self.assertRegex(self.last_line(), SUMMARY % ("stalled", 2, r"1\.00"))

    def test_a_progress_line_counts_as_progress(self):
        self.script(run(commit=False, progress="- one"), run(commit=False, progress="- two"),
                    run(commit=False, autopilot="done"))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 3)

    def test_budget_is_cumulative(self):
        self.script(run(events=[result(cost=20)]), run(events=[result(cost=6)]), run())
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        calls = self.calls()
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["argv"][calls[1]["argv"].index("--max-budget-usd") + 1], "5.00")
        self.assertRegex(self.last_line(), SUMMARY % ("budget", 2, r"26\.00"))

    def test_usage_limit_sleeps_until_the_reset(self):
        limit = {"type": "rate_limit_event",
                 "rate_limit_info": {"status": "rejected", "resetsAt": int(time.time()) + 3600}}
        self.script(run(events=[limit, result(cost=0, error=True, subtype="error_during_execution",
                                              text="usage limit reached")], commit=False),
                    run(autopilot="done"))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 2)
        self.assertRegex(self.log_text(), r"usage limit: reset in 36\d\d s, sleeping 0 s")

    def test_usage_limit_without_reset_time_uses_default(self):
        self.script(run(events=[result(cost=0, error=True, subtype="error_during_execution",
                                       text="Claude AI usage limit reached")], commit=False),
                    run(autopilot="done"))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("usage limit: reset in 1800 s, sleeping 0 s", self.log_text())

    def test_crash_without_result_stops_with_error(self):
        self.script(run(events=[], commit=False, exit=1))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 1)
        self.assertIn("| autopilot | error: claude exited 1 with no result |", self.last_line())

    def test_error_result_stops(self):
        self.script(run(events=[result(error=True, subtype="error_during_execution", text="boom")]))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("| autopilot | error: error_during_execution |", self.last_line())

    def test_max_turns_is_not_fatal(self):
        self.script(run(events=[result(error=True, subtype="error_max_turns")]), run(autopilot="done"))
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 2)

    def test_live_session_refuses(self):
        with open(os.path.join(self.sessions, "1.json"), "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "cwd": self.repo}, f)
        self.script(run())
        proc = self.autopilot()
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertEqual(self.calls(), [])

    def test_missing_repo_refuses(self):
        proc = subprocess.run([PWSH, "-NoProfile", "-File", SCRIPT, "-Repo", os.path.join(self.tmp.name, "nope")],
                              env=self.env, capture_output=True, encoding="utf-8", timeout=120)
        self.assertEqual(proc.returncode, 2)

    def test_keeps_a_crlf_handoff(self):
        self.write_handoff("continue", newline="\r\n")
        self.script(run(autopilot="done"))
        self.autopilot()
        with open(self.handoff, "rb") as f:
            data = f.read()
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))

    def test_log_file_holds_the_stream(self):
        self.script(run(autopilot="done"))
        self.autopilot()
        text = self.log_text()
        self.assertIn('"type": "result"', text)
        self.assertIn("autopilot end:", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 3: Run them to watch them fail.** Run `python -m unittest tests.test_autopilot -v`. Expected where pwsh exists: every test fails, because pwsh reports that the file `scripts/autopilot.ps1` doesn't exist. Where pwsh is missing, all are skipped. The Windows CI job and the ubuntu CI job both have pwsh, so they run them.

- [ ] **Step 4: Implement** `scripts/autopilot.ps1`:

```powershell
#Requires -Version 7.2
<#
.SYNOPSIS
Overnight autopilot: continue a repo's phase from its SESSION_HANDOFF.md in headless runs.

.DESCRIPTION
While the handoff's `Autopilot:` line says `continue`, runs
`claude -p "/resume autopilot"` in the repo's own checkout, up to -MaxRuns runs
and -BudgetUsd dollars in all.

It stops when:
- the line says `owner` or `done`, or is missing;
- two runs in a row change neither HEAD nor the Progress Log ("stalled");
- the budget is spent;
- a live Claude Code session is in the checkout.

A usage-limit stop sleeps until the reset time (30 minutes when none is given)
and continues. The stream-json goes to ~/.claude/state/autopilot/, and a
summary line is appended to the handoff's Progress Log.

Exit: 0 ended normally (done, owner, no Autopilot line, max runs); 1 stalled,
budget or error; 2 refused (no such repo, not a git repo, live session).

Test overrides: AUTOPILOT_STATE_DIR (log directory), AUTOPILOT_MAX_SLEEP_SECONDS
(caps each usage-limit sleep), CLAUDE_SESSIONS_DIR (read by live_sessions.py).
Tests: python -m unittest discover -s tests (from the repo root)
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Repo,
    [int]$MaxRuns = 6,
    [int]$MaxTurns = 200,
    [double]$BudgetUsd = 25,
    [string]$Model = 'opus'
)

$ErrorActionPreference = 'Stop'
$Utf8 = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $Utf8
$OutputEncoding = $Utf8
$Invariant = [Globalization.CultureInfo]::InvariantCulture
$DefaultWait = 1800

if (-not (Test-Path -LiteralPath $Repo -PathType Container)) {
    [Console]::Error.WriteLine("autopilot: no such directory: $Repo")
    exit 2
}
$Repo = (Resolve-Path -LiteralPath $Repo).Path
& git -C $Repo rev-parse --show-toplevel *> $null
if ($LASTEXITCODE -ne 0) {
    [Console]::Error.WriteLine("autopilot: not a git repository: $Repo")
    exit 2
}

$Scripts = $PSScriptRoot
$Handoff = Join-Path $Repo 'SESSION_HANDOFF.md'
$StateDir = if ($env:AUTOPILOT_STATE_DIR) { $env:AUTOPILOT_STATE_DIR } else { Join-Path $HOME '.claude/state/autopilot' }
New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
$Log = Join-Path $StateDir ('{0}-{1}.log' -f (Split-Path -Leaf $Repo), (Get-Date -Format 'yyyyMMdd-HHmm'))
$MaxSleep = if ($env:AUTOPILOT_MAX_SLEEP_SECONDS) { [long]$env:AUTOPILOT_MAX_SLEEP_SECONDS } else { [long]::MaxValue }

function Write-Log([string]$Text) {
    [IO.File]::AppendAllText($Log, $Text + "`n", $Utf8)
}

function Get-HandoffState {
    $json = & python (Join-Path $Scripts 'handoff_state.py') status $Handoff
    if ($LASTEXITCODE -ne 0) { return [pscustomobject]@{ exists = $false; autopilot = $null; progress = 0 } }
    return (($json -join "`n") | ConvertFrom-Json)
}

function Get-Head {
    $head = & git -C $Repo rev-parse HEAD 2>$null
    if ($LASTEXITCODE -ne 0) { return '' }
    return "$head".Trim()
}

function Test-LiveSession {
    $found = & python (Join-Path $Scripts 'live_sessions.py') $Repo
    if ($LASTEXITCODE -eq 1) {
        Write-Log ('live session in {0}: {1}' -f $Repo, ($found -join '; '))
        return $true
    }
    return $false
}

function Invoke-Run([double]$Remaining) {
    $argv = @(
        '-p', '/resume autopilot',
        '--permission-mode', 'auto',
        '--model', $Model,
        '--max-turns', "$MaxTurns",
        '--max-budget-usd', $Remaining.ToString('0.00', $Invariant),
        '--output-format', 'stream-json',
        '--verbose'
    )
    $seen = @{ Result = $null; Limit = $null }
    $env:CLAUDE_AUTOPILOT = '1'
    $env:CLAUDE_AUTOPILOT_HANDOFF = $Handoff
    Push-Location -LiteralPath $Repo
    try {
        & claude @argv 2>&1 | ForEach-Object {
            $line = "$_"
            Write-Log $line
            try { $item = $line | ConvertFrom-Json -ErrorAction Stop } catch { return }
            if ($item.type -eq 'result') { $seen.Result = $item }
            elseif ($item.type -eq 'rate_limit_event' -and $item.rate_limit_info) { $seen.Limit = $item.rate_limit_info }
        }
        $code = $LASTEXITCODE
    }
    finally {
        Pop-Location
        Remove-Item Env:CLAUDE_AUTOPILOT, Env:CLAUDE_AUTOPILOT_HANDOFF -ErrorAction SilentlyContinue
    }
    return [pscustomobject]@{ Result = $seen.Result; Limit = $seen.Limit; Exit = $code }
}

# Seconds until a usage limit resets, or $null when the run didn't hit one.
function Get-LimitWait($Run) {
    $result = $Run.Result
    if ($result -and -not $result.is_error) { return $null }
    $byEvent = [bool]($Run.Limit -and $Run.Limit.status -and $Run.Limit.status -notin 'allowed', 'allowed_warning')
    $byText = [bool]($result -and ("$($result.result) $($result.subtype)" -match '(?i)usage limit|rate limit|limit reached'))
    if (-not ($byEvent -or $byText)) { return $null }
    if ($byEvent -and $Run.Limit.resetsAt) {
        return [Math]::Max([long]60, [long]$Run.Limit.resetsAt - [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() + 60)
    }
    return [long]$DefaultWait
}

$spent = 0.0
$runs = 0
$idle = 0
$status = $null
Write-Log ('autopilot start {0} repo={1} max-runs={2} max-turns={3} budget={4} model={5}' -f (Get-Date -Format s),
    $Repo, $MaxRuns, $MaxTurns, $BudgetUsd.ToString('0.00', $Invariant), $Model)

while ($true) {
    $state = Get-HandoffState
    if ($state.autopilot -ne 'continue') {
        $status = if ($state.autopilot) { $state.autopilot } else { 'no-autopilot-line' }
        break
    }
    if ($runs -ge $MaxRuns) { $status = 'max-runs'; break }
    $remaining = $BudgetUsd - $spent
    if ($remaining -le 0) { $status = 'budget'; break }
    if (Test-LiveSession) { $status = 'live-session'; break }

    $headBefore = Get-Head
    $progressBefore = $state.progress
    $runs++
    Write-Log ('run {0} start {1}' -f $runs, (Get-Date -Format s))
    $run = Invoke-Run $remaining
    if ($run.Result -and $null -ne $run.Result.total_cost_usd) { $spent += [double]$run.Result.total_cost_usd }

    $wait = Get-LimitWait $run
    if ($null -ne $wait) {
        $sleep = [Math]::Min([long]$wait, $MaxSleep)
        Write-Log ('usage limit: reset in {0} s, sleeping {1} s' -f $wait, $sleep)
        if ($sleep -gt 0) { Start-Sleep -Seconds $sleep }
        continue
    }
    if ($null -eq $run.Result) { $status = "error: claude exited $($run.Exit) with no result"; break }
    if ($run.Result.is_error -and "$($run.Result.subtype)" -notmatch 'max_turns|max_budget') {
        $status = "error: $($run.Result.subtype)"
        break
    }

    $after = Get-HandoffState
    if ((Get-Head) -eq $headBefore -and $after.progress -eq $progressBefore) { $idle++ } else { $idle = 0 }
    if ($idle -ge 2) { $status = 'stalled'; break }
}

$sha = Get-Head
if ($sha.Length -gt 7) { $sha = $sha.Substring(0, 7) }
$summary = '- {0} | autopilot | {1} | runs {2} | ${3} | {4}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm'), $status, $runs,
    $spent.ToString('0.00', $Invariant), $sha
Write-Log "autopilot end: $summary"
if (Test-Path -LiteralPath $Handoff) {
    & python (Join-Path $Scripts 'handoff_state.py') append $Handoff $summary | Out-Null
}
Write-Output $summary
if ($status -in 'done', 'owner', 'no-autopilot-line', 'max-runs') { exit 0 }
if ($status -eq 'live-session') { exit 2 }
exit 1
```

- [ ] **Step 5: Run the tests to watch them pass.** Run `python -m unittest tests.test_autopilot -v`. Expected: all pass. The first things to check if some fail:
  - **Argument splitting:** if `call["argv"]` shows `/resume` and `autopilot` as separate items on Windows, the `.cmd` stub is re-splitting quoted arguments. Check `install_stub`'s `%*`.
  - **Budget string:** it must be `5.00`, not `5`, and in the invariant culture.
  - **Early exit:** `$ErrorActionPreference = 'Stop'` must not turn native stderr into a terminating error. PowerShell 7.2+ doesn't, which is why the `#Requires` line is there.

- [ ] **Step 6: Run the full suite.** Run `python -m unittest discover -s tests -v`. Expected: all pass.

- [ ] **Step 7: Commit.** Stage `scripts/autopilot.ps1 tests/fake_autopilot_claude.py tests/test_autopilot.py` and commit with the message `Add the on-demand overnight autopilot loop`.

### Task 13: Handoff and resume skills learn the Autopilot line; README

**Files:**
- Modify: `skills/handoff/SKILL.md` (the Full-mode section table)
- Modify: `skills/resume/SKILL.md` (`argument-hint`, step 5)
- Modify: `README.md` (the `## Automation` section added by the Part B acceptance PR)
- Modify: `tests/test_skill_files.py` (add `AutopilotSkillsTest`)

- [ ] **Step 1: Write the failing tests.** Add to `tests/test_skill_files.py`:

```python
class AutopilotSkillsTest(unittest.TestCase):
    def test_handoff_writes_the_autopilot_line(self):
        text = body(skill("handoff"))
        self.assertIn("`Autopilot: <state>`", text)
        for state in ("`continue`", "`owner`", "`done`"):
            self.assertIn(state, text)
        self.assertIn("owner checklist", text)

    def test_resume_has_a_headless_autopilot_mode(self):
        self.assertIn("autopilot", frontmatter(skill("resume")).get("argument-hint", ""))
        text = body(skill("resume"))
        self.assertIn("never stop to ask", text)
        self.assertIn("`Autopilot: owner`", text)
```

- [ ] **Step 2: Run them to watch them fail.** Run `python -m unittest tests.test_skill_files.AutopilotSkillsTest -v`. Expected: both fail on the missing text.

- [ ] **Step 3: Edit `skills/handoff/SKILL.md`.** In the Full-mode "Write the file" table, insert a row directly after the `# Session Handoff — <date>` row:

```
| `Autopilot: <state>` | One line directly under the title. `continue` when the next task needs no owner input; `owner` when the next step is an owner gate; `done` when the phase is complete. `scripts/autopilot.ps1` reads it and runs only on `continue`. |
```

Then replace the `## Blockers / Issues` row with:

```
| `## Blockers / Issues` | "None" if none. Distinguish blocking from merely open. When Autopilot is `owner`, open with the owner checklist: `- [ ]` items, each naming exactly what to check or decide, where, and what "done" looks like. |
```

- [ ] **Step 4: Edit `skills/resume/SKILL.md`.**
  - Set `argument-hint` to `"[next task from the handoff's continuation prompt] | check | autopilot"`.
  - After step 5, insert:

```
   With `autopilot` (a headless run started by `scripts/autopilot.ps1`; nobody is watching), never stop to ask. Work through the tasks, running `/handoff log` after each one passes review. At an owner gate, or on drift that needs the owner, run `/handoff` with the focus note `owner gate: <what>`: it sets `Autopilot: owner` and writes the owner checklist. Then stop. When the phase is complete, run `/handoff` so it sets `Autopilot: done`, then stop.
```

- [ ] **Step 5: Edit `README.md`.** Add one bullet to the `## Automation` section:

```
- `pwsh ~/.claude/scripts/autopilot.ps1 -Repo <path> [-MaxRuns 6] [-MaxTurns 200] [-BudgetUsd 25]` continues a phase overnight in headless runs while the repo's `SESSION_HANDOFF.md` says `Autopilot: continue`, and refuses while a live session is in that checkout. Logs go to `~/.claude/state/autopilot/`.
```

- [ ] **Step 6: Run the tests to watch them pass.** Run `python -m unittest discover -s tests -v`. Expected: all pass.

- [ ] **Step 7: Commit.** Stage `skills/handoff/SKILL.md skills/resume/SKILL.md README.md tests/test_skill_files.py` and commit with the message `Handoff and resume: the Autopilot line and headless resume`.

- [ ] **Step 8: Ship Part E** with `/ship`, then pull `~/.claude`.

- [ ] **Step 9: Acceptance (owner gate: it spends real budget).** Ask the owner before running it. It's one short on-demand run against a scratch repo.
  1. In the session scratchpad: `git init ap-demo`.
  2. Commit a `README.md`.
  3. Write an untracked `SESSION_HANDOFF.md` containing:
     - `# Session Handoff — demo`, then `Autopilot: continue`;
     - a `## Work Remaining` item: "(NEXT) Create `hello.txt` containing `hi`, commit it, run `/handoff log`, then run `/handoff` with `Autopilot: done`";
     - an empty `## Progress Log`.
  4. Run `pwsh ~/.claude/scripts/autopilot.ps1 -Repo <scratchpad>/ap-demo -MaxRuns 1 -MaxTurns 20 -BudgetUsd 2`.

  Pass criteria:
  - the log in `~/.claude/state/autopilot/` ends with `autopilot end:`;
  - the handoff's Progress Log has an `| autopilot |` summary line;
  - `hello.txt` is committed.

  Record the status, cost and SHA with `/handoff log`.

---

## Execution notes

- **Build order is binding.** B's Phase 6 calls A's script; C and E use `/ship` from B; E extends B's `stop_guard.py` and A's `handoff_state.py`.
- **One PR per part.** Don't start a part until the previous part's PR is merged and `~/.claude` is pulled and clean.
- **Parts C–E and test-author.** After Part D merges, dispatch `test-author` before the implementer for Tasks 10–12, which add behaviour. Task 13 is docs and skill text, so it skips test-author. The test code in this plan is then the floor: test-author may add hostile cases but may not weaken these.

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
import time
import unittest

from stubs import git, git_env, install_stub

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "scripts", "post_merge_cleanup.py")
FAKE_GH = os.path.join(REPO, "tests", "fake_gh.py")

sys.path.insert(0, os.path.join(REPO, "scripts"))
import live_sessions  # noqa: E402


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

    def test_new_branch_tracking_the_default_is_kept(self):
        # Created from origin/main, so its upstream is main, not its own name.
        git(self.work, self.env, "switch", "-q", "-c", "fresh", "origin/main")
        self.assertEqual(git(self.work, self.env, "config", "--get", "branch.fresh.merge"),
                         "refs/heads/main")
        code, r = self.cleanup()
        self.assertEqual(code, 0, r)
        self.assertIn("never pushed", self.kept(r)["fresh"])
        self.assertIn("fresh", self.branches())
        self.assertEqual(self.current(), "fresh")

    def test_merged_branch_in_main_checkout_is_switched_and_deleted(self):
        self.pushed_branch("feat/a")
        self.squash_merge("feat/a", 1)
        git(self.work, self.env, "switch", "-q", "feat/a")
        code, r = self.cleanup()
        self.assertEqual(code, 0, r)
        self.assertEqual(self.deleted(r), {"feat/a"})
        self.assertEqual(self.current(), "main")

    def test_failed_switch_keeps_the_branch(self):
        # Local main tracks other.txt; feat/a doesn't, and an untracked other.txt
        # in the main checkout blocks the switch to main.
        self.pushed_branch("feat/a")
        self.commit(self.work, "other.txt", "tracked\n", "add other")
        git(self.work, self.env, "push", "-q", "origin", "main")
        self.squash_merge("feat/a", 1)
        git(self.work, self.env, "switch", "-q", "feat/a")
        with open(os.path.join(self.work, "other.txt"), "w", encoding="utf-8") as f:
            f.write("untracked\n")
        code, r = self.cleanup()
        self.assertEqual(code, 0, r)
        self.assertEqual(self.deleted(r), set())
        self.assertIn("switch to main failed", self.kept(r)["feat/a"])
        self.assertIn("feat/a", self.branches())
        self.assertEqual(self.current(), "feat/a")

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
        self.assertEqual(len([n for n in r["notes"] if n.startswith("gh unavailable")]), 1, r["notes"])

    def test_gh_available_adds_no_note(self):
        self.pushed_branch("feat/a")
        self.squash_merge("feat/a", 1)
        code, r = self.cleanup()
        self.assertEqual(code, 0, r)
        self.assertFalse([n for n in r["notes"] if n.startswith("gh unavailable")], r["notes"])

    def test_sha_is_the_local_default_branch(self):
        git(self.work, self.env, "switch", "-q", "-c", "fresh")
        self.commit(self.work, "wip.txt", "wip\n", "wip")
        code, r = self.cleanup()
        self.assertEqual(code, 0, r)
        self.assertEqual(r["sha"], git(self.work, self.env, "rev-parse", "--short", "refs/heads/main"))

    def test_branch_named_like_a_tag_is_classified(self):
        self.pushed_branch("feat/a")
        self.squash_merge("feat/a", 1)
        git(self.work, self.env, "tag", "feat/a", "main")
        code, r = self.cleanup()
        self.assertEqual(code, 0, r)
        self.assertEqual(self.deleted(r), {"feat/a"})


class WorktreeTest(CleanupCase):
    def test_clean_worktree_is_removed(self):
        self.pushed_branch("feat/a")
        path = self.worktree("wt-a", "feat/a")
        self.squash_merge("feat/a", 1)
        code, r = self.cleanup()
        self.assertEqual(code, 0, r)
        self.assertEqual(self.deleted(r), {"feat/a"})
        self.assertFalse(os.path.exists(path))

    def test_new_branch_tracking_the_default_in_a_clean_worktree_is_kept(self):
        path = os.path.join(self.tmp.name, "wt-fresh")
        git(self.work, self.env, "worktree", "add", "-q", "-b", "fresh", path, "origin/main")
        self.assertEqual(git(self.work, self.env, "config", "--get", "branch.fresh.merge"),
                         "refs/heads/main")
        code, r = self.cleanup()
        self.assertEqual(code, 0, r)
        self.assertIn("never pushed", self.kept(r)["fresh"])
        self.assertEqual(r["removed_worktrees"], [])
        self.assertTrue(os.path.exists(path))
        self.assertIn("fresh", self.branches())

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
        self.assertIs(r["pulled"], False)

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

    def test_tilde_in_repo_is_expanded(self):
        self.pushed_branch("feat/a")
        self.squash_merge("feat/a", 1)
        self.env.update(HOME=self.tmp.name, USERPROFILE=self.tmp.name)
        code, r = self.cleanup("--repo", "~/work", cwd=self.tmp.name)
        self.assertEqual(code, 0, r)
        self.assertEqual(self.deleted(r), {"feat/a"})

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


# Run as `"python" SLEEPER PIDS`: prints, then starts a grandchild that writes its
# pid to PIDS and sleeps; the middle process waits on it. Used for the timeout test.
SLEEPER = """\
import os, subprocess, sys, time
pids = sys.argv[1]
if len(sys.argv) > 2:
    with open(pids + ".tmp", "w") as f:
        f.write(str(os.getpid()))
    os.replace(pids + ".tmp", pids)
    time.sleep(120)
else:
    print("started", flush=True)
    subprocess.run([sys.executable, __file__, pids, "child"])
"""


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

    def test_dirty_main_checkout_with_here_notes_the_skipped_pull(self):
        self.merged()
        with open(os.path.join(self.work, "README.md"), "a", encoding="utf-8") as f:
            f.write("edit\n")
        code, r = self.cleanup("--here")
        self.assertEqual(code, 0, r)
        self.assertIs(r["pulled"], False)
        self.assertIn("main checkout has uncommitted changes: pull skipped", r["notes"])

    def test_main_checkout_on_another_branch_notes_the_skipped_pull(self):
        self.merged()
        git(self.work, self.env, "switch", "-q", "-c", "fresh")
        code, r = self.cleanup()
        self.assertEqual(code, 0, r)
        self.assertIs(r["pulled"], False)
        self.assertIn("main checkout is on fresh, not main: pull skipped", r["notes"])

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

    def test_timed_out_test_command_kills_the_process_tree(self):
        helper = os.path.join(self.tmp.name, "sleeper.py")
        with open(helper, "w", encoding="utf-8", newline="\n") as f:
            f.write(SLEEPER)
        pids = os.path.join(self.tmp.name, "grandchild.pid")
        self.env["POST_MERGE_TEST_TIMEOUT"] = "3"
        start = time.monotonic()
        code, r = self.cleanup("--test", '"%s" "%s" "%s"' % (sys.executable, helper, pids))
        self.assertLess(time.monotonic() - start, 90)
        self.assertEqual(code, 1, r)
        self.assertEqual(r["tests"], "fail")
        self.assertIn("started", r["test_output"])
        self.assertEqual(r["test_output"].splitlines()[-1], "timed out after 3 s")
        self.assertTrue(os.path.exists(pids), "the grandchild didn't start within the timeout")
        with open(pids, encoding="utf-8") as f:
            pid = int(f.read())
        self.addCleanup(kill, pid)
        deadline = time.monotonic() + 20
        while live_sessions.pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(live_sessions.pid_alive(pid), "the grandchild outlived the timeout")

    def test_bad_test_timeout_refuses_before_changing_anything(self):
        self.merged()
        self.env["POST_MERGE_TEST_TIMEOUT"] = "soon"
        code, r = self.cleanup("--test", '"%s" -c "print(1)"' % sys.executable)
        self.assertEqual(code, 2, r)
        self.assertIn("POST_MERGE_TEST_TIMEOUT", r["refused"])
        self.assertIn("feat/a", self.branches())

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

    def test_undecodable_handoff_is_noted_not_fatal(self):
        path = os.path.join(self.work, "SESSION_HANDOFF.md")
        data = b"# H\n\n## Progress Log\n- caf\xe9\n"
        with open(path, "wb") as f:
            f.write(data)
        self.merged()
        code, r = self.cleanup()
        self.assertEqual(code, 0, r)
        self.assertEqual(self.deleted(r), {"feat/a"})
        self.assertTrue([n for n in r["notes"] if n.startswith("handoff not written: ")], r["notes"])
        with open(path, "rb") as f:
            self.assertEqual(f.read(), data)

    def test_no_handoff_file_means_no_write(self):
        self.merged()
        code, r = self.cleanup()
        self.assertEqual(code, 0, r)
        self.assertEqual(self.deleted(r), {"feat/a"})
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

    def broken_after_a_deletion(self):
        """feat/a is deleted, then feat/b's worktree, whose .git file is garbage, raises."""
        self.merged()
        self.pushed_branch("feat/b")
        path = self.worktree("wt-b", "feat/b")
        self.squash_merge("feat/b", 2)
        gitfile = os.path.join(path, ".git")
        os.remove(gitfile)  # hidden on Windows, so it can't be opened for writing
        with open(gitfile, "w", encoding="utf-8") as f:
            f.write("not a gitfile\n")

    def test_error_mid_run_reports_what_was_done(self):
        self.broken_after_a_deletion()
        code, r = self.cleanup()
        self.assertEqual(code, 2, r)
        self.assertTrue(r["error"].startswith("RuntimeError: "), r["error"])
        self.assertEqual(self.deleted(r), {"feat/a"})
        self.assertNotIn("feat/a", self.branches())
        self.assertIn("feat/b", self.branches())

    def test_human_output_for_an_error_mid_run(self):
        self.broken_after_a_deletion()
        proc = self.run_script()
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("post-merge cleanup failed: RuntimeError: ", proc.stdout)
        self.assertIn("deleted: feat/a (PR #1 merged)", proc.stdout)


def kill(pid):
    """Best-effort kill of a stray test process."""
    if not live_sessions.pid_alive(pid):
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True,
                       encoding="utf-8", errors="replace")
    else:
        os.kill(pid, 9)


if __name__ == "__main__":
    unittest.main(verbosity=2)

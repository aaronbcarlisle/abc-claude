"""Installer scenario suite: every scenario runs against install.sh (Git Bash on
Windows, sh elsewhere) and against install.ps1 under Windows PowerShell 5.1 and
pwsh on Windows. Missing interpreters are skipped. No network, no real ~/.claude.
"""

import json
import re
import subprocess
import unittest

import installer_harness as h


class InstallerScenarios:
    INTERP = None

    def setUp(self):
        if h.interpreter_command(self.INTERP) is None:
            self.skipTest(self.INTERP + " is not available here")
        self.sb = h.Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.sb.make_remote()

    def install(self, *args, **kwargs):
        return self.sb.run(self.INTERP, *args, **kwargs)

    def assertOk(self, result):
        self.assertEqual(result.returncode, 0, result)

    def target_git(self, *args):
        return h.git("-C", str(self.sb.target), *args).strip()

    # --- task 4: skeleton ---------------------------------------------------
    def test_unknown_flag_exits_1(self):
        result = self.install("--bogus")
        self.assertEqual(result.returncode, 1, result)
        self.assertFalse(self.sb.target.exists())

    def test_help_flag_prints_usage_and_exits_0(self):
        result = self.install("--help")
        self.assertOk(result)
        self.assertIn("Usage: ", result.output)
        self.assertFalse(self.sb.target.exists())

    def test_reports_source_and_target(self):
        result = self.install("--yes")
        self.assertIn("[abc-claude] Installing from " + str(self.sb.remote) + ".", result.output)
        self.assertIn("[abc-claude] Target: ", result.output)
        self.assertNotIn("CLAUDE_CONFIG_DIR is", result.output)

    def test_warns_when_config_dir_is_not_home_claude(self):
        other = self.sb.root / "elsewhere"
        result = self.install("--yes", env=self.sb.env(CLAUDE_CONFIG_DIR=other))
        self.assertIn("the tracked settings.json commands assume ~/.claude", result.output)

    # --- task 5: bootstrap --------------------------------------------------
    def test_fresh_clone(self):
        result = self.install("--yes")
        self.assertOk(result)
        for rel in ("settings.json", "CLAUDE.md", "rules/dev-tooling.md", "install.sh", "templates/local.md"):
            self.assertTrue((self.sb.target / rel).is_file(), rel)
        self.assertEqual(self.target_git("config", "--get", "remote.origin.url"), str(self.sb.remote))
        self.assertEqual(self.target_git("config", "--get", "core.autocrlf"), "false")
        self.assertEqual(self.target_git("rev-parse", "--abbrev-ref", "HEAD"), "master")
        self.assertEqual(self.target_git("status", "--porcelain"), "")
        self.assertIn("Cloning into", result.output)

    def test_rerun_on_clean_repo_is_a_no_op(self):
        self.assertOk(self.install("--yes"))
        head = self.target_git("rev-parse", "HEAD")
        before = h.snapshot(self.sb.target)
        result = self.install("--yes")
        self.assertOk(result)
        self.assertEqual(self.target_git("rev-parse", "HEAD"), head)
        self.assertEqual(h.backups(self.sb.target), [])
        after = h.snapshot(self.sb.target)
        worktree = lambda snap: {k: v for k, v in snap.items() if not k.startswith(".git/")}
        self.assertEqual(worktree(after), worktree(before))
        self.assertIn("is an abc-claude checkout - updating it", result.output)

    def test_rerun_pulls_new_commits(self):
        self.assertOk(self.install("--yes"))
        self.sb.push_change({"CLAUDE.md": "# Updated rules\n"})
        self.assertOk(self.install("--yes"))
        self.assertEqual((self.sb.target / "CLAUDE.md").read_bytes(), b"# Updated rules\n")

    def test_rerun_with_local_edits_skips_pull(self):
        self.assertOk(self.install("--yes"))
        head = self.target_git("rev-parse", "HEAD")
        (self.sb.target / "CLAUDE.md").write_bytes(b"# My local edit\n")
        self.sb.push_change({"rules/dev-tooling.md": "# Upstream change\n"})
        result = self.install("--yes")
        self.assertOk(result)
        self.assertIn("have local changes - skipping the pull", result.output)
        self.assertEqual(self.target_git("rev-parse", "HEAD"), head)
        self.assertEqual((self.sb.target / "CLAUDE.md").read_bytes(), b"# My local edit\n")

    def test_rerun_with_a_local_commit_leaves_it_without_force(self):
        self.assertOk(self.install("--yes"))
        (self.sb.target / "CLAUDE.md").write_bytes(b"# Committed local edit\n")
        h.git("-C", str(self.sb.target), *h.FIXTURE_IDENT, "commit", "-q", "-am", "local work")
        head = self.target_git("rev-parse", "HEAD")
        self.sb.push_change({"rules/dev-tooling.md": "# Upstream change\n"})
        result = self.install("--yes")
        self.assertOk(result)
        self.assertIn("Could not fast-forward", result.output)
        self.assertEqual(self.target_git("rev-parse", "HEAD"), head)
        self.assertEqual(h.backups(self.sb.target), [])
        self.assertEqual(self.target_git("for-each-ref", "refs/heads/backup"), "")

    # --- --force ------------------------------------------------------------
    def remote_head(self):
        return h.git("-C", str(self.sb.remote), "rev-parse", "refs/heads/master").strip()

    def backup_branches(self):
        return self.target_git("for-each-ref", "--format=%(refname:short)", "refs/heads/backup").split()

    def test_help_mentions_force(self):
        result = self.install("--help")
        self.assertOk(result)
        self.assertIn(h.translate_flag("--force", self.INTERP), result.output)

    def test_unknown_flag_with_force_exits_1(self):
        result = self.install("--force", "--bogus")
        self.assertEqual(result.returncode, 1, result)
        self.assertFalse(self.sb.target.exists())

    def test_force_on_a_fresh_target_clones(self):
        result = self.install("--yes", "--force")
        self.assertOk(result)
        self.assertIn("Cloning into", result.output)
        self.assertEqual(self.target_git("rev-parse", "HEAD"), self.remote_head())

    def test_force_on_a_clean_checkout_updates_without_backups(self):
        self.assertOk(self.install("--yes"))
        self.sb.push_change({"CLAUDE.md": "# Updated rules\n"})
        result = self.install("--yes", "--force")
        self.assertOk(result)
        self.assertEqual((self.sb.target / "CLAUDE.md").read_bytes(), b"# Updated rules\n")
        self.assertEqual(self.target_git("rev-parse", "HEAD"), self.remote_head())
        self.assertEqual(h.backups(self.sb.target), [])
        self.assertEqual(self.backup_branches(), [])
        self.assertNotIn("Backed up files", result.output)

    def test_force_backs_up_local_edits_and_resets(self):
        self.assertOk(self.install("--yes"))
        t = self.sb.target
        local_md = (t / "rules" / "local.md").read_bytes()
        (t / "CLAUDE.md").write_bytes(b"# My local edit\n")
        (t / "rules" / "dev-tooling.md").write_bytes(b"# Staged local edit\n")
        h.git("-C", str(t), "add", "rules/dev-tooling.md")
        (t / "rules" / "mine.md").write_bytes(b"# Local-only rule\n")
        (t / "history.jsonl").write_bytes(b'{"junk": 1}\n')
        self.sb.push_change({"settings.json": json.dumps({"model": "haiku"}) + "\n"})
        result = self.install("--yes", "--force")
        self.assertOk(result)
        baks = h.backups(t)
        self.assertEqual(len(baks), 2, baks)
        self.assertTrue(re.fullmatch(r"CLAUDE\.md\.bak\.\d{14}", baks[0]), baks)
        self.assertTrue(re.fullmatch(r"rules/dev-tooling\.md\.bak\.\d{14}", baks[1]), baks)
        self.assertEqual((t / baks[0]).read_bytes(), b"# My local edit\n")
        self.assertEqual((t / baks[1]).read_bytes(), b"# Staged local edit\n")
        self.assertIn("Backed up files that differed from the repo", result.output)
        for bak in baks:
            self.assertIn("    " + bak, result.output)
        self.assertEqual((t / "CLAUDE.md").read_bytes(), b"# Fixture global rules\n")
        self.assertEqual((t / "rules" / "dev-tooling.md").read_bytes(), b"# Fixture tooling rules\n")
        self.assertEqual(json.loads((t / "settings.json").read_text(encoding="utf-8")), {"model": "haiku"})
        self.assertEqual(self.target_git("rev-parse", "HEAD"), self.remote_head())
        self.assertEqual(self.target_git("rev-parse", "--abbrev-ref", "HEAD"), "master")
        self.assertEqual(self.target_git("rev-parse", "--abbrev-ref", "@{u}"), "origin/master")
        self.assertEqual(self.target_git("status", "--porcelain", "--untracked-files=no"), "")
        # Untracked and ignored files are never touched.
        self.assertEqual((t / "rules" / "local.md").read_bytes(), local_md)
        self.assertEqual((t / "rules" / "mine.md").read_bytes(), b"# Local-only rule\n")
        self.assertEqual((t / "history.jsonl").read_bytes(), b'{"junk": 1}\n')
        self.assertEqual(self.backup_branches(), [])

    def test_force_keeps_local_commits_on_a_backup_branch(self):
        self.assertOk(self.install("--yes"))
        t = self.sb.target
        (t / "CLAUDE.md").write_bytes(b"# Committed local edit\n")
        h.git("-C", str(t), *h.FIXTURE_IDENT, "commit", "-q", "-am", "local work")
        local = self.target_git("rev-parse", "HEAD")
        self.sb.push_change({"rules/dev-tooling.md": "# Upstream change\n"})
        result = self.install("--yes", "--force")
        self.assertOk(result)
        branches = self.backup_branches()
        self.assertEqual(len(branches), 1, branches)
        self.assertTrue(re.fullmatch(r"backup/\d{14}", branches[0]), branches)
        self.assertEqual(self.target_git("rev-parse", branches[0]), local)
        self.assertIn(branches[0], result.output)
        self.assertEqual(self.target_git("rev-parse", "refs/heads/master"), self.remote_head())
        self.assertEqual((t / "CLAUDE.md").read_bytes(), b"# Fixture global rules\n")
        self.assertEqual((t / "rules" / "dev-tooling.md").read_bytes(), b"# Upstream change\n")
        self.assertEqual(h.backups(t), [])

    def test_force_keeps_commits_of_a_detached_head(self):
        self.assertOk(self.install("--yes"))
        t = self.sb.target
        h.git("-C", str(t), "checkout", "-q", "--detach")
        (t / "CLAUDE.md").write_bytes(b"# Detached work\n")
        h.git("-C", str(t), *h.FIXTURE_IDENT, "commit", "-q", "-am", "detached work")
        detached = self.target_git("rev-parse", "HEAD")
        result = self.install("--yes", "--force")
        self.assertOk(result)
        branches = self.backup_branches()
        self.assertEqual(len(branches), 1, branches)
        self.assertEqual(self.target_git("rev-parse", branches[0]), detached)
        self.assertEqual(self.target_git("rev-parse", "--abbrev-ref", "HEAD"), "master")
        self.assertEqual(self.target_git("rev-parse", "HEAD"), self.remote_head())

    def test_force_backs_up_an_untracked_file_the_repo_now_tracks(self):
        self.assertOk(self.install("--yes"))
        t = self.sb.target
        (t / "rules" / "new-rule.md").write_bytes(b"# My own version\n")
        (t / "rules" / "same.md").write_bytes(b"# Same\n")
        self.sb.push_change({"rules/new-rule.md": "# Upstream version\n", "rules/same.md": "# Same\n"})
        result = self.install("--yes", "--force")
        self.assertOk(result)
        baks = h.backups(t)
        self.assertEqual(len(baks), 1, baks)
        self.assertTrue(re.fullmatch(r"rules/new-rule\.md\.bak\.\d{14}", baks[0]), baks)
        self.assertEqual((t / baks[0]).read_bytes(), b"# My own version\n")
        self.assertEqual((t / "rules" / "new-rule.md").read_bytes(), b"# Upstream version\n")
        self.assertEqual(self.target_git("rev-parse", "HEAD"), self.remote_head())

    def test_force_with_unreachable_remote_changes_nothing(self):
        self.assertOk(self.install("--yes"))
        t = self.sb.target
        (t / "CLAUDE.md").write_bytes(b"# My local edit\n")
        head = self.target_git("rev-parse", "HEAD")
        self.sb.remote.rename(self.sb.root / "remote-away.git")
        before = h.snapshot(t)
        result = self.install("--yes", "--force")
        self.assertOk(result)
        self.assertIn("Cannot reach", result.output)
        self.assertEqual(self.target_git("rev-parse", "HEAD"), head)
        self.assertEqual(h.backups(t), [])
        self.assertEqual((t / "CLAUDE.md").read_bytes(), b"# My local edit\n")
        worktree = lambda snap: {k: v for k, v in snap.items() if not k.startswith(".git/")}
        self.assertEqual(worktree(h.snapshot(t)), worktree(before))

    def test_force_with_a_foreign_origin_still_exits_1(self):
        self.sb.target.mkdir()
        h.git("init", "-q", "-b", "master", cwd=self.sb.target)
        h.git("remote", "add", "origin", "https://example.invalid/someone/other.git", cwd=self.sb.target)
        (self.sb.target / "settings.json").write_bytes(b'{"mine": true}\n')
        before = h.snapshot(self.sb.target)
        result = self.install("--yes", "--force")
        self.assertEqual(result.returncode, 1, result)
        self.assertIn("is a git repo with a different origin", result.output)
        self.assertEqual(h.snapshot(self.sb.target), before)

    def test_force_in_place_notes_it_does_not_apply(self):
        self.assertOk(self.install("--yes"))
        t = self.sb.target
        (t / "CLAUDE.md").write_bytes(b"# My local edit\n")
        head = self.target_git("rev-parse", "HEAD")
        result = self.install("--yes", "--force", script_dir=t)
        self.assertOk(result)
        self.assertIn("using it in place", result.output)
        self.assertIn(h.translate_flag("--force", self.INTERP) + " doesn't apply", result.output)
        self.assertEqual((t / "CLAUDE.md").read_bytes(), b"# My local edit\n")
        self.assertEqual(self.target_git("rev-parse", "HEAD"), head)
        self.assertEqual(h.backups(t), [])

    def test_missing_tracked_files_are_restored(self):
        self.assertOk(self.install("--yes"))
        (self.sb.target / "CLAUDE.md").unlink()
        (self.sb.target / "rules" / "dev-tooling.md").unlink()
        result = self.install("--yes")
        self.assertOk(result)
        self.assertIn("Restored 2 missing tracked file(s).", result.output)
        self.assertEqual((self.sb.target / "CLAUDE.md").read_bytes(), b"# Fixture global rules\n")
        self.assertTrue((self.sb.target / "rules" / "dev-tooling.md").is_file())

    def test_foreign_origin_exits_1_and_changes_nothing(self):
        self.sb.target.mkdir()
        h.git("init", "-q", "-b", "master", cwd=self.sb.target)
        h.git("remote", "add", "origin", "https://example.invalid/someone/other.git", cwd=self.sb.target)
        (self.sb.target / "settings.json").write_bytes(b'{"mine": true}\n')
        before = h.snapshot(self.sb.target)
        result = self.install("--yes")
        self.assertEqual(result.returncode, 1, result)
        self.assertIn("is a git repo with a different origin (https://example.invalid/someone/other.git)",
                      result.output)
        self.assertEqual(h.snapshot(self.sb.target), before)

    def test_repo_without_origin_exits_1(self):
        self.sb.target.mkdir()
        h.git("init", "-q", "-b", "master", cwd=self.sb.target)
        before = h.snapshot(self.sb.target)
        result = self.install("--yes")
        self.assertEqual(result.returncode, 1, result)
        self.assertIn("different origin (none)", result.output)
        self.assertEqual(h.snapshot(self.sb.target), before)

    def test_origin_spelled_differently_is_still_recognized(self):
        self.assertOk(self.install("--yes"))
        variant = str(self.sb.remote)[:-len(".git")].upper() + "/"   # no .git, other case, trailing slash
        h.git("-C", str(self.sb.target), "remote", "set-url", "origin", variant)
        result = self.install("--yes")
        self.assertOk(result)
        self.assertIn("is an abc-claude checkout - updating it", result.output)

    def test_unreachable_repo_exits_1_with_sign_in_hint(self):
        missing = self.sb.root / "no-such-repo.git"
        result = self.install("--yes", env=self.sb.env(ABC_CLAUDE_REPO=missing))
        self.assertEqual(result.returncode, 1, result)
        self.assertIn("Cannot reach", result.output)
        self.assertIn("gh auth login", result.output)
        self.assertFalse(self.sb.target.exists())

    def test_run_in_place_from_the_target(self):
        self.assertOk(self.install("--yes"))
        h.git("-C", str(self.sb.target), "checkout", "-q", "-b", "work")
        result = self.install("--yes", script_dir=self.sb.target)
        self.assertOk(result)
        self.assertIn("using it in place", result.output)
        self.assertNotIn("not master: only master is installed", result.output)

    def test_run_from_a_fresh_clone_elsewhere_into_missing_target(self):
        clone = self.sb.root / "clone"
        h.git("clone", "-q", str(self.sb.remote), str(clone))
        env = self.sb.env(ABC_CLAUDE_REPO=self.sb.root / "ignored.git")
        result = self.install("--yes", script_dir=clone, env=env)
        self.assertOk(result)
        self.assertIn("Installing from local checkout", result.output)
        self.assertEqual(self.target_git("config", "--get", "remote.origin.url"), str(self.sb.remote))
        self.assertNotIn("is behind its origin/master", result.output)
        self.assertNotIn("not master: only master is installed", result.output)

    # --- final review: the clone's master is what gets installed -------------
    CLONE_BEHIND = ("[abc-claude] The clone's master is behind its origin/master: installing the older commit. "
                    "Run git pull --ff-only in the clone for the latest.")
    CLONE_NO_MASTER = ("has no usable master branch (install.sh or .gitignore missing). "
                       "In the clone, run: git checkout master, then git pull --ff-only.")

    def make_clone(self):
        clone = self.sb.root / "clone"
        h.git("clone", "-q", str(self.sb.remote), str(clone))
        return clone

    def clone_env(self):
        return self.sb.env(ABC_CLAUDE_REPO=self.sb.root / "ignored.git")

    def test_clone_behind_origin_on_detached_head_warns_and_installs_its_master(self):
        clone = self.make_clone()
        self.sb.push_change({"CLAUDE.md": "# Newer rules\n"})
        h.git("-C", str(clone), "fetch", "-q")
        h.git("-C", str(clone), "checkout", "-q", "--detach")
        result = self.install("--yes", script_dir=clone, env=self.clone_env())
        self.assertOk(result)
        self.assertIn(self.CLONE_BEHIND, result.output)
        self.assertIn("[abc-claude] The clone is on a detached HEAD, not master: only master is installed.",
                      result.output)
        self.assertEqual((self.sb.target / "CLAUDE.md").read_bytes(), b"# Fixture global rules\n")

    def test_clone_on_another_branch_warns_and_installs_master(self):
        clone = self.make_clone()
        h.git("-C", str(clone), "checkout", "-q", "-b", "work")
        (clone / "CLAUDE.md").write_bytes(b"# Work branch rules\n")
        h.git("-C", str(clone), *h.FIXTURE_IDENT, "commit", "-q", "-am", "work")
        result = self.install("--yes", script_dir=clone, env=self.clone_env())
        self.assertOk(result)
        self.assertIn("[abc-claude] The clone is on work, not master: only master is installed.", result.output)
        self.assertNotIn("is behind its origin/master", result.output)
        self.assertEqual((self.sb.target / "CLAUDE.md").read_bytes(), b"# Fixture global rules\n")

    def test_clone_whose_master_is_not_abc_claude_exits_1_and_changes_nothing(self):
        clone = self.make_clone()
        c = str(clone)
        h.git("-C", c, "checkout", "-q", "-b", "work")
        h.git("-C", c, "checkout", "-q", "--orphan", "stale")
        h.git("-C", c, "rm", "-rfq", ".")
        (clone / "README.md").write_bytes(b"# Initial commit\n")
        h.git("-C", c, "add", "README.md")
        h.git("-C", c, *h.FIXTURE_IDENT, "commit", "-q", "-m", "Initial commit")
        h.git("-C", c, "branch", "-q", "-f", "master", "stale")
        h.git("-C", c, "checkout", "-q", "work")
        self.sb.target.mkdir()
        (self.sb.target / "settings.json").write_bytes(b'{"mine": true}\n')
        before = h.snapshot(self.sb.target)
        result = self.install("--yes", script_dir=clone, env=self.clone_env())
        self.assertEqual(result.returncode, 1, result)
        self.assertIn("[abc-claude] The clone at ", result.output)
        self.assertIn(self.CLONE_NO_MASTER, result.output)
        self.assertEqual(h.snapshot(self.sb.target), before)

    # --- task 6: adopt ------------------------------------------------------
    def seed_existing_target(self):
        """A non-git ~/.claude with a conflict, a CRLF-only difference, junk and a local-only file."""
        t = self.sb.target
        files = {
            "settings.json": b'{"model": "sonnet"}\r\n',          # conflict: differs in content
            "CLAUDE.md": b"# Fixture global rules\r\n",            # differs only in line endings
            "history.jsonl": b'{"junk": 1}\r\n',                   # Claude Code data, ignored
            "projects/p/memory/MEMORY.md": b"memory\r\n",          # ignored
            "skills/synced/x/SKILL.md": b"synced\n",               # ignored
            "rules/mine.md": b"# Local-only rule\n",               # untracked, reported
        }
        h.Sandbox.write_files(t, files)
        return files

    def test_adopt_backs_up_conflicts_keeps_junk_and_reports_local_only(self):
        files = self.seed_existing_target()
        result = self.install("--yes")
        self.assertOk(result)
        t = self.sb.target
        baks = h.backups(t)
        self.assertEqual(len(baks), 1, baks)
        self.assertTrue(re.fullmatch(r"settings\.json\.bak\.\d{14}", baks[0]), baks)
        self.assertEqual((t / baks[0]).read_bytes(), files["settings.json"])
        for rel in ("history.jsonl", "projects/p/memory/MEMORY.md", "skills/synced/x/SKILL.md", "rules/mine.md"):
            self.assertEqual((t / rel).read_bytes(), files[rel], rel)
        for rel in h.tracked_files(t):
            self.assertNotIn(b"\r\n", (t / rel).read_bytes(), rel)
        self.assertEqual((t / "CLAUDE.md").read_bytes(), b"# Fixture global rules\n")
        self.assertIn("Local-only files to review", result.output)
        self.assertIn("    rules/mine.md", result.output)
        self.assertNotIn("history.jsonl", result.output)
        self.assertIn("    " + baks[0], result.output)
        self.assertIn("Rewrote 1 tracked file(s)", result.output)
        self.assertIn("Adopted ", result.output)
        self.assertEqual(self.target_git("status", "--porcelain"), "?? rules/mine.md")
        self.assertEqual(self.target_git("rev-parse", "--abbrev-ref", "@{u}"), "origin/master")

    def test_rerun_after_adopt_makes_no_new_backups(self):
        self.seed_existing_target()
        self.assertOk(self.install("--yes"))
        first = h.backups(self.sb.target)
        self.assertOk(self.install("--yes"))
        self.assertEqual(h.backups(self.sb.target), first)

    def test_rerun_after_interrupted_adopt_recovers(self):
        files = self.seed_existing_target()
        t = self.sb.target
        h.git("init", "-q", "-b", "master", cwd=t)
        h.git("remote", "add", "origin", str(self.sb.remote), cwd=t)
        result = self.install("--yes")
        self.assertOk(result)
        self.assertIn("Found an unfinished adopt in ", result.output)
        self.assertIn(": starting it again.", result.output)
        baks = h.backups(t)
        self.assertEqual(len(baks), 1, baks)
        self.assertTrue(re.fullmatch(r"settings\.json\.bak\.\d{14}", baks[0]), baks)
        self.assertEqual((t / baks[0]).read_bytes(), files["settings.json"])
        for rel in ("history.jsonl", "projects/p/memory/MEMORY.md", "skills/synced/x/SKILL.md", "rules/mine.md"):
            self.assertEqual((t / rel).read_bytes(), files[rel], rel)
        for rel in h.tracked_files(t):
            self.assertNotIn(b"\r\n", (t / rel).read_bytes(), rel)
        self.assertEqual((t / "CLAUDE.md").read_bytes(), b"# Fixture global rules\n")
        self.assertEqual(self.target_git("rev-parse", "--abbrev-ref", "@{u}"), "origin/master")
        # HEAD is born: rev-parse --verify raises via h.git's check=True if not.
        self.assertNotEqual(self.target_git("rev-parse", "--verify", "HEAD"), "")

    def test_rerun_from_clone_after_interrupted_adopt_recovers(self):
        clone = self.make_clone()
        files = self.seed_existing_target()
        t = self.sb.target
        h.git("init", "-q", "-b", "master", cwd=t)
        h.git("remote", "add", "origin", str(clone), cwd=t)   # interrupted before remote set-url
        result = self.install("--yes", script_dir=clone, env=self.clone_env())
        self.assertOk(result)
        self.assertIn("Found an unfinished adopt in ", result.output)
        self.assertIn(": starting it again.", result.output)
        self.assertIn("Adopted ", result.output)
        self.assertEqual(self.target_git("config", "--get", "remote.origin.url"), str(self.sb.remote))
        self.assertEqual(self.target_git("rev-parse", "--abbrev-ref", "@{u}"), "origin/master")
        self.assertEqual((t / "CLAUDE.md").read_bytes(), b"# Fixture global rules\n")
        baks = h.backups(t)
        self.assertEqual(len(baks), 1, baks)
        self.assertEqual((t / baks[0]).read_bytes(), files["settings.json"])

    def test_run_from_clone_elsewhere_adopts_with_clone_origin(self):
        clone = self.sb.root / "clone"
        h.git("clone", "-q", str(self.sb.remote), str(clone))
        (clone / "CLAUDE.md").write_bytes(b"# Uncommitted edit in the clone\n")
        self.sb.target.mkdir()
        (self.sb.target / "history.jsonl").write_bytes(b"junk\n")
        env = self.sb.env(ABC_CLAUDE_REPO=self.sb.root / "ignored.git")
        result = self.install("--yes", script_dir=clone, env=env)
        self.assertOk(result)
        self.assertIn("has uncommitted edits", result.output)
        self.assertEqual(self.target_git("config", "--get", "remote.origin.url"),
                         h.git("-C", str(clone), "config", "--get", "remote.origin.url").strip())
        self.assertEqual((self.sb.target / "CLAUDE.md").read_bytes(), b"# Fixture global rules\n")
        self.assertEqual((self.sb.target / "history.jsonl").read_bytes(), b"junk\n")

    def test_failed_adopt_leaves_no_git_dir(self):
        self.sb.target.mkdir()
        (self.sb.target / "settings.json").write_bytes(b"{}\n")
        empty = self.sb.root / "empty.git"
        h.git("init", "-q", "--bare", str(empty))
        result = self.install("--yes", env=self.sb.env(ABC_CLAUDE_REPO=empty))
        self.assertEqual(result.returncode, 1, result)
        self.assertIn("Nothing was changed.", result.output)
        self.assertFalse((self.sb.target / ".git").exists())
        self.assertEqual((self.sb.target / "settings.json").read_bytes(), b"{}\n")

    def test_adopt_of_a_master_without_gitignore_aborts(self):
        seed = self.sb.root / "bad-seed"
        bad = self.sb.root / "bad.git"
        seed.mkdir()
        h.git("init", "-q", "-b", "master", cwd=seed)
        (seed / "README.md").write_bytes(b"# Not abc-claude\n")
        h.git("add", "README.md", cwd=seed)
        h.git(*h.FIXTURE_IDENT, "commit", "-q", "-m", "Initial commit", cwd=seed)
        h.git("clone", "-q", "--bare", str(seed), str(bad))
        self.sb.target.mkdir()
        (self.sb.target / "settings.json").write_bytes(b"{}\n")
        result = self.install("--yes", env=self.sb.env(ABC_CLAUDE_REPO=bad))
        self.assertEqual(result.returncode, 1, result)
        self.assertIn("[abc-claude] The fetched master has no .gitignore. Nothing was changed.", result.output)
        self.assertFalse((self.sb.target / ".git").exists())
        self.assertEqual((self.sb.target / "settings.json").read_bytes(), b"{}\n")

    def test_unborn_head_next_to_a_local_branch_keeps_the_history(self):
        t = self.sb.target
        t.mkdir()
        h.git("init", "-q", "-b", "master", cwd=t)
        h.git("remote", "add", "origin", str(self.sb.remote), cwd=t)
        (t / "notes.md").write_bytes(b"# My notes\n")
        h.git("add", "notes.md", cwd=t)
        h.git(*h.FIXTURE_IDENT, "commit", "-q", "-m", "local work", cwd=t)
        commit = self.target_git("rev-parse", "refs/heads/master")
        h.git("checkout", "-q", "--orphan", "wip", cwd=t)
        result = self.install("--yes")
        self.assertOk(result)
        self.assertNotIn("Found an unfinished adopt", result.output)
        self.assertTrue((t / ".git").is_dir())
        self.assertEqual(self.target_git("rev-parse", "refs/heads/master"), commit)

    # --- task 7: machine-local ----------------------------------------------
    def test_local_md_created_from_template_under_yes(self):
        self.assertOk(self.install("--yes"))
        text = (self.sb.target / "rules" / "local.md").read_bytes().decode("utf-8")
        self.assertNotIn("\r", text)
        self.assertNotIn("{{", text)
        self.assertRegex(text, r"(?m)^This machine: \S.*\.$")
        self.assertNotIn("No admin rights", text)
        self.assertEqual(self.target_git("status", "--porcelain"), "")

    def test_local_md_prompts_use_answers(self):
        result = self.install(stdin="Work laptop\nn\n")
        self.assertOk(result)
        text = (self.sb.target / "rules" / "local.md").read_text(encoding="utf-8")
        self.assertIn("This machine: Work laptop.\n", text)
        self.assertIn("No admin rights or Developer Mode here: don't suggest elevated installs, symlinks "
                      "or HKLM edits.\n", text)

    def test_existing_local_md_is_unchanged(self):
        self.sb.target.mkdir()
        h.Sandbox.write_files(self.sb.target, {"rules/local.md": b"# Mine\r\nkeep me\r\n"})
        result = self.install("--yes")
        self.assertOk(result)
        self.assertEqual((self.sb.target / "rules" / "local.md").read_bytes(), b"# Mine\r\nkeep me\r\n")
        self.assertIn("rules/local.md exists - leaving it unchanged.", result.output)

    def test_git_identity_from_global_config(self):
        result = self.install("--yes")
        self.assertOk(result)
        self.assertEqual(self.target_git("config", "--local", "user.name"), "Test User")
        self.assertEqual(self.target_git("config", "--local", "user.email"), "test@example.com")
        self.assertIn("Git identity for this repo: Test User <test@example.com>.", result.output)

    def test_git_identity_left_unset_with_warning(self):
        self.sb.set_global_identity(None, None)
        result = self.install("--yes")
        self.assertOk(result)
        self.assertIn("Git identity for this repo is not set", result.output)
        local = h.git("-C", str(self.sb.target), "config", "--local", "--get", "user.name", check=False)
        self.assertEqual(local.strip(), "")

    # --- task 8: checks -----------------------------------------------------
    def test_no_managed_settings(self):
        result = self.install("--yes")
        self.assertIn("No managed settings found.", result.output)
        self.assertIn("python resolves: Python ", result.output)

    def test_managed_settings_reported_with_affected_keys(self):
        managed = self.sb.root / "managed-settings.json"
        managed.write_text(json.dumps({"companyAnnouncements": ["hi"], "statusLine": {"type": "command"},
                                       "allowManagedHooksOnly": True}), encoding="utf-8")
        paths = str(self.sb.no_managed) + ";" + str(managed)
        result = self.install("--yes", env=self.sb.env(ABC_CLAUDE_MANAGED_PATHS=paths))
        self.assertOk(result)
        self.assertIn("Managed settings found: " + str(managed), result.output)
        self.assertIn("Managed keys that affect this config: allowManagedHooksOnly, statusLine\n", result.output)
        self.assertNotIn("companyAnnouncements", result.output)

    def test_unparseable_managed_settings_is_a_warning(self):
        managed = self.sb.root / "managed-settings.json"
        managed.write_text("{not json", encoding="utf-8")
        result = self.install("--yes", env=self.sb.env(ABC_CLAUDE_MANAGED_PATHS=managed))
        self.assertOk(result)
        self.assertIn("Could not parse " + str(managed) + " as JSON.", result.output)

    # --- task 9: plugin sync ------------------------------------------------
    def test_plugin_sync_installs_only_missing(self):
        self.sb.fake_claude(plugins=["alpha@claude-plugins-official"])
        result = self.install("--yes")
        self.assertOk(result)
        calls = self.sb.claude_calls()
        self.assertIn("plugin list --json", calls)
        self.assertIn("plugin marketplace list --json", calls)
        self.assertIn("plugin marketplace add example/extra-market", calls)
        self.assertIn("plugin install beta@extra-market", calls)
        self.assertNotIn("plugin install alpha@claude-plugins-official", calls)
        self.assertIn("Installed: marketplace extra-market, beta@extra-market", result.output)
        self.assertIn("Already present: alpha@claude-plugins-official", result.output)
        # The fake CLI rewrote settings.json on install; the installer restores the tracked copy.
        self.assertIn("Restored settings.json", result.output)
        self.assertEqual(self.target_git("status", "--porcelain"), "")

    def test_plugin_sync_second_run_installs_nothing(self):
        self.sb.fake_claude(plugins=["alpha@claude-plugins-official"])
        self.assertOk(self.install("--yes"))
        first = len(self.sb.claude_calls())
        result = self.install("--yes")
        self.assertOk(result)
        second = self.sb.claude_calls()[first:]
        self.assertEqual([c for c in second if not c.endswith("--json")], [], second)
        self.assertIn("Already present: marketplace extra-market, alpha@claude-plugins-official, beta@extra-market",
                      result.output)

    def test_plugin_install_failure_is_a_warning(self):
        self.sb.fake_claude(plugins=["alpha@claude-plugins-official"], fail=["beta@extra-market"])
        result = self.install("--yes")
        self.assertOk(result)
        self.assertIn("Could not install beta@extra-market.", result.output)
        self.assertIn("Failed: beta@extra-market", result.output)

    def test_marketplace_add_failure_is_a_warning(self):
        self.sb.fake_claude(plugins=["alpha@claude-plugins-official"], fail=["example/extra-market"])
        result = self.install("--yes")
        self.assertOk(result)
        self.assertIn("Could not add marketplace extra-market (example/extra-market).", result.output)
        self.assertIn("Failed: marketplace extra-market", result.output)

    def test_unsupported_marketplace_source_type_is_a_warning(self):
        self.sb.fake_claude(plugins=["alpha@claude-plugins-official"])
        self.assertOk(self.install("--yes"))
        settings_path = self.sb.target / "settings.json"
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        settings["extraKnownMarketplaces"]["odd-market"] = {"source": {"source": "npm", "package": "example-market"}}
        settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        result = self.install("--yes")
        self.assertOk(result)
        self.assertIn(
            "Marketplace odd-market has a source type the installer can't add: add it with claude plugin marketplace add.",
            result.output)
        self.assertIn("marketplace odd-market", result.output)
        self.assertIn("Failed:", result.output)

    def test_prune_only_reports_under_yes(self):
        self.sb.fake_claude(plugins=["alpha@claude-plugins-official", "beta@extra-market",
                                     "gamma@claude-plugins-official"], marketplaces=["claude-plugins-official", "extra-market"])
        result = self.install("--yes", "--prune")
        self.assertOk(result)
        self.assertIn("Installed but not in settings.json: gamma@claude-plugins-official", result.output)
        self.assertIn("Non-interactive run: prune only reports.", result.output)
        self.assertFalse(any(c.startswith("plugin uninstall") for c in self.sb.claude_calls()))

    def test_prune_interactive_uninstalls_after_yes(self):
        self.sb.fake_claude(plugins=["alpha@claude-plugins-official", "beta@extra-market",
                                     "gamma@claude-plugins-official"], marketplaces=["claude-plugins-official", "extra-market"])
        self.assertOk(self.install("--yes"))
        result = self.install("--prune", stdin="y\n")
        self.assertOk(result)
        self.assertIn("plugin uninstall gamma@claude-plugins-official", self.sb.claude_calls())
        self.assertIn("Uninstalled gamma@claude-plugins-official.", result.output)
        self.assertNotIn("gamma@claude-plugins-official", self.sb.claude_plugins())

    def test_claude_missing_skips_plugin_sync(self):
        result = self.install("--yes")
        self.assertOk(result)
        self.assertIn("claude is not on PATH - skipping plugin sync.", result.output)


class ShTests(InstallerScenarios, unittest.TestCase):
    INTERP = "sh"


class WindowsPowerShellTests(InstallerScenarios, unittest.TestCase):
    INTERP = "powershell"


class PwshTests(InstallerScenarios, unittest.TestCase):
    INTERP = "pwsh"


class StaticChecks(unittest.TestCase):
    def test_install_ps1_is_ascii_lf_without_bom(self):
        data = (h.REPO_ROOT / "install.ps1").read_bytes()
        self.assertFalse(data.startswith(b"\xef\xbb\xbf"), "install.ps1 has a BOM")
        self.assertEqual([i for i, b in enumerate(data) if b > 0x7F], [], "non-ASCII bytes in install.ps1")
        self.assertNotIn(b"\r", data)
        self.assertIn(b"#Requires -Version 5", data)

    def test_install_sh_parses_and_is_lf(self):
        data = (h.REPO_ROOT / "install.sh").read_bytes()
        self.assertNotIn(b"\r", data)
        self.assertTrue(data.startswith(b"#!/usr/bin/env sh\n"))
        prefix = h.interpreter_command("sh")
        if prefix is None:
            self.skipTest("no sh interpreter")
        proc = subprocess.run(prefix + ["-n", str(h.REPO_ROOT / "install.sh").replace("\\", "/")],
                              capture_output=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

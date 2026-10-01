#!/usr/bin/env python3
"""
Tests for bash_guard.py. Runs the hook as a subprocess, the way Claude Code
does, against throwaway git repos in a temp dir.

    python -m unittest discover -s tests
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "bash_guard.py")
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")


def git(*args, cwd):
    subprocess.run(["git"] + list(args), cwd=cwd, env=GIT_ENV, check=True,
                   capture_output=True, encoding="utf-8")


def run_hook(command, cwd, tool="Bash", env=None):
    payload = json.dumps({"tool_name": tool, "tool_input": {"command": command}, "cwd": cwd})
    proc = subprocess.run([sys.executable, HOOK], input=payload, capture_output=True,
                          encoding="utf-8", timeout=60, env=env)
    return proc.returncode, proc.stderr


def make_clone(root, name, default):
    """A clone of a one-commit repo whose default branch is `default`."""
    seed = os.path.join(root, name + "-seed")
    os.makedirs(seed)
    git("init", "-q", "-b", default, cwd=seed)
    git("commit", "-q", "--allow-empty", "-m", "init", cwd=seed)
    bare = os.path.join(root, name + ".git")
    git("clone", "-q", "--bare", seed, bare, cwd=root)
    clone = os.path.join(root, name)
    git("clone", "-q", bare, clone, cwd=root)
    return clone


class PushGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = cls.tmp.name
        cls.main_repo = make_clone(root, "mainrepo", "main")          # on main
        cls.feature_repo = make_clone(root, "featrepo", "main")
        git("checkout", "-q", "-b", "feat/x", cwd=cls.feature_repo)  # on a feature branch
        cls.master_repo = make_clone(root, "masterrepo", "master")    # default is master
        cls.tracking_repo = make_clone(root, "trackrepo", "main")
        git("checkout", "-q", "-b", "feat/y", "--track", "origin/main", cwd=cls.tracking_repo)
        cls.other = os.path.join(root, "elsewhere")
        os.makedirs(cls.other)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def assertBlocked(self, command, cwd, tool="Bash"):
        code, err = run_hook(command, cwd, tool)
        self.assertEqual(code, 2, command)
        self.assertIn("default branch", err)

    def assertAllowed(self, command, cwd, tool="Bash"):
        code, err = run_hook(command, cwd, tool)
        self.assertEqual((code, err), (0, ""), command)

    def test_explicit_refspecs_to_default_are_blocked(self):
        for cmd in ("git push origin main", "git push origin HEAD:main", "git push origin +feat/x:main",
                    "git push origin :main", "git push origin feat/x:refs/heads/main", "git push --all origin",
                    "GIT_TRACE=1 git push origin main", "git add -A && git push origin main",
                    "git status\ngit push -u origin main", "if git push origin main; then echo ok; fi",
                    "git push origin 'refs/heads/*:refs/heads/*'", "git push origin :"):
            self.assertBlocked(cmd, self.feature_repo)
        self.assertBlocked("$out = git push origin main 2>&1", self.feature_repo, tool="PowerShell")

    def test_feature_pushes_are_allowed(self):
        for cmd in ("git push -u origin feat/x", "git push", "git push origin HEAD",
                    "git push --force-with-lease origin feat/x",
                    'git push -u origin feat/x && gh pr create --base main --title "t"',
                    "git push origin 'refs/heads/feat/*:refs/heads/feat/*'",
                    "git push origin feat/x  # not main"):
            self.assertAllowed(cmd, self.feature_repo)

    def test_bare_push_while_on_default_is_blocked(self):
        for cmd in ("git push", "git push origin HEAD", "git push -u origin HEAD", "git push origin @",
                    "git push --repo=origin"):
            self.assertBlocked(cmd, self.main_repo)

    def test_configured_bare_pushes_are_resolved(self):
        # feat/y tracks origin/main: push.default=upstream sends it to main; simple refuses.
        self.assertBlocked("git -c push.default=upstream push", self.tracking_repo)
        self.assertAllowed("git push", self.tracking_repo)
        self.assertBlocked("git -c push.default=matching push", self.feature_repo)
        self.assertBlocked("git -c remote.origin.push=refs/heads/feat/x:refs/heads/main push", self.feature_repo)
        self.assertAllowed("git -c remote.origin.push=refs/heads/feat/x:refs/heads/feat/x push", self.feature_repo)
        git("config", "push.default", "upstream", cwd=self.tracking_repo)
        try:
            self.assertBlocked("git push", self.tracking_repo)
            self.assertBlocked("git push origin", self.tracking_repo)
        finally:
            git("config", "--unset", "push.default", cwd=self.tracking_repo)

    def test_directory_changes_are_followed(self):
        self.assertBlocked('cd "%s" && git push' % self.main_repo, self.other)
        self.assertBlocked('git -C "%s" push' % self.main_repo, self.other)
        self.assertBlocked('Set-Location "%s"; git push' % self.main_repo, self.other, tool="PowerShell")
        # Unquoted, so a Windows path's backslashes must survive PowerShell tokenizing.
        self.assertBlocked("Set-Location %s; git push" % self.main_repo, self.other, tool="PowerShell")
        self.assertBlocked('pushd "%s" && git push' % self.main_repo, self.other)
        self.assertAllowed('cd "%s" && git push' % self.feature_repo, self.main_repo)

    def test_directory_changes_that_do_not_stick_are_undone(self):
        missing = os.path.join(self.other, "missing")
        self.assertBlocked('cd "%s"; git push' % missing, self.main_repo)
        self.assertBlocked('(cd "%s" && git push); git push' % self.feature_repo, self.main_repo)
        self.assertBlocked('(cd "%s" && git push origin HEAD)' % self.main_repo, self.feature_repo)
        self.assertBlocked('pushd "%s"; popd; git push' % self.feature_repo, self.main_repo)
        self.assertBlocked('Push-Location "%s"; Pop-Location; git push' % self.feature_repo, self.main_repo,
                           tool="PowerShell")
        # PowerShell parentheses aren't subshells: the location change sticks.
        self.assertAllowed('(Set-Location "%s"); git push' % self.feature_repo, self.main_repo, tool="PowerShell")
        # A PowerShell variable holding the branch or the directory is followed.
        self.assertBlocked("$b = 'main'; git push origin $b", self.feature_repo, tool="PowerShell")
        self.assertBlocked('$d = "%s"; Set-Location $d; git push' % self.main_repo, self.other, tool="PowerShell")

    def test_default_branch_comes_from_the_remote(self):
        self.assertBlocked("git push origin master", self.master_repo)
        self.assertAllowed("git push origin main", self.master_repo)

    def test_unknown_remote_head_falls_back_to_main_and_master(self):
        git("remote", "set-head", "origin", "-d", cwd=self.feature_repo)
        try:
            self.assertBlocked("git push origin master", self.feature_repo)
            self.assertBlocked("git push origin main", self.feature_repo)
            self.assertAllowed("git push origin feat/x", self.feature_repo)
        finally:
            git("remote", "set-head", "origin", "main", cwd=self.feature_repo)

    def test_quoted_text_never_trips_it(self):
        for cmd in ('echo "git push origin main"', 'git commit -m "never git push origin main"',
                    "python - <<'EOF'\nprint('git push origin main')\nEOF"):
            self.assertAllowed(cmd, self.main_repo)


class ForceGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.repo = make_clone(cls.tmp.name, "forcerepo", "main")
        git("checkout", "-q", "-b", "feat/x", cwd=cls.repo)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_plain_force_pushes_are_blocked(self):
        for cmd in ("git push --force origin feat/x", "git push -f", "git push -uf origin feat/x",
                    "git push origin +feat/x", "git push origin +HEAD:feat/x",
                    "git -C . push --force"):
            code, err = run_hook(cmd, self.repo)
            self.assertEqual(code, 2, cmd)
            self.assertIn("--force-with-lease", err, cmd)
        code, _ = run_hook("git push --force origin feat/x", self.repo, tool="PowerShell")
        self.assertEqual(code, 2)

    def test_lease_pushes_and_lookalikes_are_allowed(self):
        for cmd in ("git push --force-with-lease origin feat/x",
                    "git push --force-with-lease --force-if-includes origin feat/x",
                    "git push --force-with-lease=feat/x:abc123 origin feat/x",
                    "git push -o ci.skip origin feat/x", "git push -oci.skip origin feat/x",
                    "git push -uofoo origin feat/x", "git push --push-option=ff origin feat/x",
                    "git push origin ^feat/old feat/x",
                    'echo "git push --force"', "git fetch -f origin"):
            self.assertEqual(run_hook(cmd, self.repo), (0, ""), cmd)


class MergeGuardTest(unittest.TestCase):
    FAKE_GH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_gh.py")

    def setUp(self):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from stubs import install_stub
        self.tmp = tempfile.TemporaryDirectory()
        bin_dir = os.path.join(self.tmp.name, "bin")
        install_stub(bin_dir, "gh", self.FAKE_GH)
        self.state = os.path.join(self.tmp.name, "gh.json")
        self.log = os.path.join(self.tmp.name, "gh.log")
        self.env = dict(os.environ, FAKE_GH_STATE=self.state, FAKE_GH_LOG=self.log,
                        PATH=bin_dir + os.pathsep + os.environ.get("PATH", ""))

    def tearDown(self):
        self.tmp.cleanup()

    def checks(self, exit=0, stdout="", stderr=""):
        with open(self.state, "w", encoding="utf-8") as f:
            json.dump({"checks": {"exit": exit, "stdout": stdout, "stderr": stderr}}, f)

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as f:
            return f.read().splitlines()

    def hook(self, command, tool="Bash"):
        return run_hook(command, self.tmp.name, tool, env=self.env)

    def test_merge_over_red_or_pending_checks_is_blocked(self):
        for code in (1, 8):  # failed, pending
            self.checks(exit=code, stdout="build\tfail\t1m\thttps://example.com/run\n")
            got, err = self.hook("gh pr merge --squash --match-head-commit abc123 12")
            self.assertEqual(got, 2, code)
            self.assertIn("MERGE BLOCKED", err)
            self.assertIn("build", err)
        self.assertIn("pr checks 12", self.calls())

    def test_global_repo_option_before_the_command_is_followed(self):
        self.checks(exit=1, stdout="build\tfail\n")
        self.assertEqual(self.hook("gh -R o/r pr merge 7 --squash")[0], 2)
        self.assertEqual(self.hook("gh pr -R o/r merge 7")[0], 2)
        self.assertEqual(self.hook("gh --repo o/r pr merge --squash 7", tool="PowerShell")[0], 2)
        self.assertIn("pr checks 7 -R o/r", self.calls())

    def test_merge_over_green_checks_is_allowed(self):
        self.checks(exit=0, stdout="build\tpass\t1m\n")
        self.assertEqual(self.hook("gh pr merge --squash --match-head-commit abc123 12"), (0, ""))
        self.assertEqual(self.hook("gh pr merge 12 --squash", tool="PowerShell"), (0, ""))

    def test_repo_without_ci_is_allowed(self):
        self.checks(exit=1, stderr="no checks reported on the 'feat/x' branch\n")
        self.assertEqual(self.hook("gh pr merge --squash 12"), (0, ""))

    def test_rest_merge_endpoints_are_checked_against_their_repo(self):
        self.checks(exit=1, stdout="lint\tfail\n")
        for cmd in ("gh api -X PUT repos/o/r/pulls/7/merge-async -f merge_method=squash",
                    "gh api --method PUT repos/o/r/pulls/7/merge"):
            self.assertEqual(self.hook(cmd)[0], 2, cmd)
        self.assertIn("pr checks 7 -R o/r", self.calls())

    def test_auto_merge_and_reads_are_not_checked(self):
        self.checks(exit=1, stdout="lint\tfail\n")
        for cmd in ("gh pr merge --auto --squash 12", "gh api repos/o/r/pulls/7/merge",
                    "gh pr view 12", 'echo "gh pr merge 12"'):
            self.assertEqual(self.hook(cmd), (0, ""), cmd)
        self.assertEqual(self.calls(), [])


class HeredocGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_heredoc_file_writes_are_blocked(self):
        for cmd in ("cat > notes.md <<'EOF'\n# Title\nbody\nEOF",
                    "cat <<EOF >> progress.md\n- Task 3: complete\nEOF",
                    "cat >>progress.md << 'EOF'\nline\nEOF",
                    "tee notes.md <<'EOF' >/dev/null\nbody\nEOF",
                    "cat <<'EOF' | tee -a log.md\nline\nEOF",
                    "mkdir -p d && cat > d/x.py <<-EOF\n\tprint(1)\n\tEOF",
                    "(cat <<'EOF'\nline\nEOF\n) > notes.md",
                    "{ cat <<'EOF'; } > notes.md\nline\nEOF",
                    "cat 1>notes.md <<\\EOF\nline\nEOF",
                    "cat > notes.md <<'END-OF-NOTES'\nline\nEND-OF-NOTES"):
            code, err = run_hook(cmd, self.cwd)
            self.assertEqual(code, 2, cmd)
            self.assertIn("Write tool", err)

    def test_heredocs_that_feed_a_program_are_allowed(self):
        for cmd in ("python - <<'EOF'\nprint(1)\nEOF",
                    "python - <<'EOF' > out.txt\nprint(1)\nEOF",
                    "cat <<'EOF' | gh pr create --body-file -\nbody\nEOF",
                    "git commit -F - <<'EOF'\nsubject\nEOF",
                    "cat <<EOF\nshown only\nEOF",
                    "echo hi > out.txt",
                    "sort a.txt > b.txt",
                    "cat <<'EOF' | grep \">\" -\nline\nEOF",
                    "cat <<'EOF' 2>err.log | python -\nprint(1)\nEOF",
                    "x=$(cat <<'EOF'\nline\nEOF\n)"):
            self.assertEqual(run_hook(cmd, self.cwd), (0, ""), cmd)

    def test_powershell_herestring_file_writes_are_blocked(self):
        for cmd in ("@'\nline one\nline two\n'@ | Set-Content -Path notes.md",
                    "$t = @\"\nline\n\"@\nAdd-Content progress.md $t",
                    "@'\nline\n'@ > notes.md",
                    "@'\nline\n'@ > \"notes.md\"",
                    "@'\nline\n'@ | Out-File 'notes.md'",
                    "[IO.File]::WriteAllText('x.md', @'\nline\n'@)"):
            code, err = run_hook(cmd, self.cwd, tool="PowerShell")
            self.assertEqual(code, 2, cmd)
            self.assertIn("Write tool", err)

    def test_powershell_herestrings_that_write_no_file_are_allowed(self):
        for cmd in ("$body = @'\nuse a > b here\n'@\ngh pr create --title t --body $body",
                    "git commit -m @'\nsubject\n'@",
                    "Set-Content -Path x.txt -Value 'one line'",
                    "$b = @'\nbody\n'@\ngh pr create --title \"a > b\" --body $b",
                    "$b = @'\nbody\n'@\ngh pr create --title 'Set-Content docs' --body $b",
                    "$b = @'\nbody\n'@\ngh pr create --body $b 2>$null",
                    "$b = @'\nDon't git push origin main\n'@\ngh pr comment 1 --body $b"):
            self.assertEqual(run_hook(cmd, self.cwd, tool="PowerShell"), (0, ""), cmd)


class AttributionGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        path = os.path.join(self.cwd, name)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        return path

    def assertBlocked(self, command, cwd=None, tool="Bash"):
        code, err = run_hook(command, cwd or self.cwd, tool)
        self.assertEqual(code, 2, command)
        self.assertIn("ATTRIBUTION BLOCKED", err, command)

    def assertAllowed(self, command, cwd=None, tool="Bash"):
        code, err = run_hook(command, cwd or self.cwd, tool)
        self.assertEqual((code, err), (0, ""), command)

    def test_dash_m_trailer_blocks(self):
        self.assertBlocked('git commit -m "x\n\nCo-Authored-By: Claude <noreply@anthropic.com>"')

    def test_dash_m_clean_message_passes(self):
        self.assertAllowed('git commit -m "fix the thing"')

    def test_dash_capital_f_file_with_trailer_blocks(self):
        self.write("msg.txt", "Fix bug\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n")
        self.assertBlocked("git commit -F msg.txt")

    def test_dash_capital_f_clean_file_passes(self):
        self.write("msg.txt", "Fix bug\n\nCo-authored-by: Jane Doe <jane@example.com>\n")
        self.assertAllowed("git commit -F msg.txt")

    def test_trailer_flag_blocks(self):
        self.assertBlocked('git commit -m "subject" --trailer "Co-authored-by: Claude <noreply@anthropic.com>"')

    def test_trailer_flag_key_equals_value_form_blocks(self):
        self.assertBlocked('git commit -m "subject" --trailer "Co-authored-by=Claude <noreply@anthropic.com>"')

    def test_short_option_clusters_carry_the_message(self):
        trailer = "Co-Authored-By: Claude <noreply@anthropic.com>"
        self.assertBlocked(f'git commit -am "x\n\n{trailer}"')
        self.assertBlocked(f'git commit -sam"x\n\n{trailer}"')
        self.write("msg.txt", f"Fix bug\n\n{trailer}\n")
        self.assertBlocked("git commit -aF msg.txt")
        self.assertAllowed('git commit -am "fix the thing"')

    def test_gh_pr_create_body_file_generated_with_blocks(self):
        self.write("body.md", "Adds the thing.\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)\n")
        self.assertBlocked('gh pr create --title "t" --body-file body.md')

    def test_gh_pr_edit_body_session_link_blocks(self):
        self.assertBlocked('gh pr edit 1 --body "See notes. https://claude.ai/code/session_01AbC more text"')

    def test_gh_api_field_value_blocks(self):
        self.assertBlocked(
            'gh api -X PATCH repos/o/r/pulls/1 -f "body=Fix the thing.\n\nCo-Authored-By: Claude <noreply@anthropic.com>"')

    def test_gh_pr_merge_body_trailer_blocks(self):
        self.assertBlocked('gh pr merge 1 --squash --body "Merge it.\n\nCo-Authored-By: Claude <noreply@anthropic.com>"')

    def test_gh_repo_flag_before_or_between_command_words_blocks(self):
        body = '--body "x\n\nCo-Authored-By: Claude <noreply@anthropic.com>"'
        for words in ("--repo o/r pr create", "-R o/r pr create", "--repo=o/r pr create",
                      "pr --repo o/r create", "pr -R o/r comment 1", "-R o/r issue create"):
            self.assertBlocked(f"gh {words} --title t {body}")
        self.assertBlocked('gh -R o/r api repos/o/r/issues/1/comments'
                           ' -f "body=x\n\nCo-Authored-By: Claude <noreply@anthropic.com>"')
        self.assertAllowed('gh --repo o/r pr create --title t --body "fix the thing"')

    def test_gh_new_alias_blocks(self):
        self.assertBlocked('gh pr new --title t --body "x\n\nCo-Authored-By: Claude <noreply@anthropic.com>"')
        self.assertBlocked('gh issue new --title t --body "x\n\nCo-Authored-By: Claude <noreply@anthropic.com>"')
        self.assertBlocked('gh release new v1 --notes "x\n\nCo-Authored-By: Claude <noreply@anthropic.com>"')

    def test_powershell_commit_trailer_blocks(self):
        self.assertBlocked('git commit -m "x\n\nCo-Authored-By: Claude <noreply@anthropic.com>"', tool="PowerShell")

    def test_heredoc_text_blocks(self):
        trailer = "Co-Authored-By: Claude <noreply@anthropic.com>"
        # Claude Code's own default commit and PR forms.
        self.assertBlocked(f"git commit -m \"$(cat <<'EOF'\nFix it\n\n{trailer}\nEOF\n)\"")
        self.assertBlocked(f"gh pr create --title t --body \"$(cat <<'EOF'\nAdds it.\n\n{trailer}\nEOF\n)\"")
        # stdin from a heredoc or a here-string
        self.assertBlocked(f"git commit -F - <<'EOF'\nFix it\n\n{trailer}\nEOF")
        self.assertBlocked(f"gh api repos/o/r/issues/1/comments --input - <<'EOF'\n{{\"body\": \"x\\n\\n{trailer}\"}}\nEOF")
        self.assertBlocked(f'git commit -F - <<< "x\n\n{trailer}"')
        self.assertAllowed("git commit -m \"$(cat <<'EOF'\nFix it\nEOF\n)\"")
        self.assertAllowed("git commit -F - <<'EOF'\nFix it\nEOF")

    def test_shell_variable_holding_the_text_blocks(self):
        trailer = "Co-Authored-By: Claude <noreply@anthropic.com>"
        self.assertBlocked(f"BODY=\"$(cat <<'EOF'\nAdds it.\n\n{trailer}\nEOF\n)\"\ngh pr create --title t --body \"$BODY\"")
        self.assertBlocked(f'msg="x\n\n{trailer}"; git commit -m "${{msg}}"')
        self.assertAllowed(f'msg="x\n\n{trailer}"; git commit -m "$other"')

    def test_powershell_backtick_newlines_block(self):
        self.assertBlocked('git commit -m "x`n`nCo-Authored-By: Claude <noreply@anthropic.com>"', tool="PowerShell")

    def test_powershell_herestring_text_blocks(self):
        trailer = "Co-Authored-By: Claude <noreply@anthropic.com>"
        # A quote inside the here-string must not end it early.
        self.assertBlocked(f"git commit -m @'\nFix the parser's bug\n\n{trailer}\n'@", tool="PowerShell")
        self.assertBlocked(f'git commit -m @"\nFix the "quoted" bug\n\n{trailer}\n"@', tool="PowerShell")
        self.assertBlocked(f"gh pr create --title t --body @'\r\nAdds it.\r\n\r\n{trailer}\r\n'@", tool="PowerShell")
        self.assertAllowed("git commit -m @'\nFix the parser's bug\n'@", tool="PowerShell")

    def test_powershell_variable_holding_the_text_blocks(self):
        trailer = "Co-Authored-By: Claude <noreply@anthropic.com>"
        self.assertBlocked(f"$body = @'\nAdds it.\n\n{trailer}\n'@\ngh pr create --title t --body $body",
                           tool="PowerShell")
        self.assertBlocked(f"$Msg = \"x`n`n{trailer}\"; git commit -m $msg", tool="PowerShell")
        self.assertAllowed("$body = @'\nAdds it.\n'@\ngh pr create --title t --body $body", tool="PowerShell")

    def test_echoed_command_text_passes(self):
        self.assertAllowed("""echo "git commit -m 'Co-Authored-By: Claude'\"""")

    def test_prose_mentions_pass(self):
        self.assertAllowed('git commit -m "Not ported: the Claude review workflow"')
        self.assertAllowed('git commit -m "Cleanup: drop the Co-Authored-By trailers that name Claude"')

    def test_dash_capital_f_dash_stdin_passes(self):
        self.assertAllowed("git commit -F -")

    def test_missing_dash_capital_f_file_passes(self):
        self.assertAllowed("git commit -F does-not-exist.txt")

    def test_dash_capital_c_resolves_file_relative_to_target(self):
        target = os.path.join(self.tmp.name, "repo")
        os.makedirs(os.path.join(target, "rel"))
        with open(os.path.join(target, "rel", "path"), "w", encoding="utf-8", newline="") as f:
            f.write("Fix bug\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n")
        other = os.path.join(self.tmp.name, "elsewhere")
        os.makedirs(other)
        self.assertBlocked('git -C "%s" commit -F rel/path' % target, cwd=other)


class FailOpenTest(unittest.TestCase):
    def test_bad_input_is_allowed(self):
        for payload in ("not json", "{}", json.dumps({"tool_input": {"command": ""}})):
            proc = subprocess.run([sys.executable, HOOK], input=payload, capture_output=True,
                                  encoding="utf-8", timeout=30)
            self.assertEqual(proc.returncode, 0, payload)

    def test_unbalanced_quotes_do_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run_hook('echo "unterminated', tmp)[0], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

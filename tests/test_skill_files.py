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


def section(text, heading):
    """The text from the `heading` line up to the next heading of the same or a higher level."""
    level = len(heading) - len(heading.lstrip("#"))
    start = text.index(heading + "\n") if heading + "\n" in text else text.index(heading)
    end = re.compile(r"^#{1,%d} " % level, re.M).search(text, start + len(heading))
    return text[start:end.start() if end else len(text)]


def rule_matches(rule, command):
    """True when the Bash permission rule `Bash(<pattern>)` matches `command`: the pattern must
    cover the whole command text, and each `*` stands for any text."""
    pattern = re.fullmatch(r"Bash\((.*)\)", rule).group(1)
    if pattern.endswith(":*"):
        pattern = pattern[:-2] + " *"
    return re.fullmatch(".*".join(re.escape(part) for part in pattern.split("*")), command) is not None


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

    def test_tools_a_skill_names_are_in_its_allowed_tools(self):
        # allowed-tools pre-approves tools for the turn that invokes the skill. A tool the text tells
        # the model to use belongs in the list too, so an unattended run isn't held up by a prompt.
        named = ("AskUserQuestion", "Agent", "Monitor", "TaskStop", "TodoWrite")
        for rel in tracked("skills/*/SKILL.md"):
            path = os.path.join(REPO, rel)
            allowed = frontmatter(path).get("allowed-tools")
            if not isinstance(allowed, list) or not allowed:
                continue
            text = body(path).split("\n---\n", 1)[1]  # the text after the frontmatter
            for tool in named:
                if re.search(r"\b%s\b" % tool, text):
                    self.assertIn(tool, allowed, "%s uses %s" % (rel, tool))
            if re.search(r"invoke the `[\w:-]+` skill|run `/[\w-]+", text):
                self.assertIn("Skill", allowed, "%s invokes a skill" % rel)

    def test_skill_text_has_no_positional_argument_placeholders(self):
        # Claude Code replaces $0-$9 in a skill's text with its arguments, so shell or awk code
        # such as `awk '{print $1}'` would run with the arguments pasted in.
        for rel in tracked("skills/*/SKILL.md"):
            self.assertIsNone(re.search(r"\$\d", body(os.path.join(REPO, rel))), rel)


class ShipLoopTest(unittest.TestCase):
    def test_pr_review_loop_is_model_invocable_with_ship(self):
        data = frontmatter(skill("pr-review-loop"))
        self.assertEqual(data.get("disable-model-invocation"), "false")
        self.assertIn("ship", data.get("argument-hint", ""))
        for tool in ("Bash(python:*)", "AskUserQuestion", "TodoWrite", "TaskCreate", "TaskUpdate", "TaskList"):
            self.assertIn(tool, data.get("allowed-tools", []), tool)

    def test_ship_allows_the_tools_it_uses(self):
        allowed = frontmatter(skill("ship")).get("allowed-tools", [])
        for tool in ("Skill", "AskUserQuestion", "Write"):
            self.assertIn(tool, allowed, tool)

    def test_ship_allows_rm_only_on_its_marker(self):
        # A bare `Bash(rm:*)` would pre-approve any rm, `rm -rf` included, while /ship runs.
        allowed = frontmatter(skill("ship")).get("allowed-tools", [])
        rm_rules = [rule for rule in allowed if re.match(r"Bash\(rm\b", rule)]
        self.assertEqual(rm_rules, ['Bash(rm -f "$HOME/.claude/state/ship/*")'])
        text = body(skill("ship"))
        command = 'rm -f "$HOME/.claude/state/ship/${CLAUDE_SESSION_ID}"'
        self.assertIn(command, text)
        self.assertTrue(rule_matches(rm_rules[0], command))
        for other in ("rm -rf /", "rm -f build/out", 'rm -rf "$HOME/.claude"'):
            self.assertFalse(rule_matches(rm_rules[0], other), other)

    def test_pr_review_loop_merges_only_through_phase_6(self):
        text = body(skill("pr-review-loop"))
        self.assertIn("## Phase 6", text)
        self.assertIn("gh pr merge --squash <PR>", text)
        # Every merge command uses the argument order of the `Bash(gh pr merge --squash *)` allow rule.
        self.assertEqual(re.findall(r"gh pr merge\b\S*(?: \S+){0,2}", text), ["gh pr merge --squash <PR>"])
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

    def test_the_review_wait_outlasts_its_script_and_a_cut_off_wait_is_unknown(self):
        phase4 = section(body(skill("pr-review-loop")), "## Phase 4")
        self.assertIn("`timeout_ms: 1500000`", phase4)
        self.assertNotIn("1140000", phase4)
        self.assertIn("If the Monitor ends at its deadline without printing `quiet window over`, "
                      "treat it as `review status unknown`", phase4)

    def test_a_fresh_ship_pr_waits_for_ci_instead_of_stopping(self):
        text = body(skill("pr-review-loop"))
        self.assertIn("With `ship`, TOTAL 0 and CI still running also goes to Phase 4.",
                      section(text, "### 1.5"))
        self.assertIn("If this loop hasn't pushed yet (a fresh PR from /ship), start "
                      "`gh pr checks <PR> --watch --interval 30` with Bash `run_in_background` now.",
                      section(text, "## Phase 4"))
        phase6 = section(text, "## Phase 6")
        self.assertIn("If the checks query counts only pending checks, wait on that background watch "
                      "and re-check everything.", phase6)
        self.assertIn("A failing check goes back to Phase 1.", phase6)
        self.assertIn("`UNSTABLE` or `BLOCKED` while checks are pending counts as pending, not a stop.", phase6)

    def test_phase_6_runs_the_cleanup_with_bounded_timeouts(self):
        data = frontmatter(skill("pr-review-loop"))
        # An allow rule doesn't match past a leading assignment of a non-safe environment variable.
        self.assertIn("Bash(POST_MERGE_TEST_TIMEOUT=540 python *)", data.get("allowed-tools", []))
        command = ("POST_MERGE_TEST_TIMEOUT=540 python \"$HOME/.claude/scripts/post_merge_cleanup.py\" "
                   "--repo . --here --test '<gate test command>'")
        phase6 = section(body(skill("pr-review-loop")), "## Phase 6")
        self.assertIn(command, phase6)
        self.assertTrue(rule_matches("Bash(POST_MERGE_TEST_TIMEOUT=540 python *)", command))
        self.assertIn("Bash tool's `timeout: 600000`", phase6)
        self.assertIn("never start a second run", phase6)

    def test_phase_6_passes_a_cmd_exe_safe_test_command(self):
        phase6 = section(body(skill("pr-review-loop")), "## Phase 6")
        self.assertIn("Pass it single-quoted in Bash. Inside it use double quotes only, because on Windows "
                      "the script runs it through cmd.exe", phase6)
        self.assertIn("no `$VAR`, `'…'`, `~` or `2>/dev/null`", phase6)
        self.assertIn("Don't wrap it in `bash -c` and don't switch shells.", phase6)
        self.assertIn("Omit `--test` when the repo has no test command.", phase6)
        self.assertIn("pr-review-loop's Phase 6 passes the same command to the cleanup script",
                      body(skill("ship")))
        # The script's own help says the same about cmd.exe and quoting.
        script = body(os.path.join(REPO, "scripts", "post_merge_cleanup.py"))
        self.assertIn("(cmd.exe on Windows): quote with double quotes only", script)

    def test_phase_6_reads_the_cleanup_output_the_script_prints(self):
        phase6 = section(body(skill("pr-review-loop")), "## Phase 6")
        script = body(os.path.join(REPO, "scripts", "post_merge_cleanup.py"))
        # Each phrase Phase 6 tells the model to look for, next to the script code that prints it.
        for skill_text, script_text in (
                ("`tests: fail`", '"ok" if code == 0 else "fail"'),
                ("`timed out after 540 s`", '"timed out after %g s" % timeout'),
                ("`post-merge cleanup refused: <reason>`", '"post-merge cleanup refused: "'),
                ("`post-merge cleanup failed: <error>`", '"post-merge cleanup failed: "'),
                ("`tests: skipped`", '"tests": "skipped"'),
                ("`note:`", '"note: "'),
                ("`checked out in this session's worktree`",
                 "checked out in this session's worktree: remove it after this session ends")):
            self.assertIn(skill_text, phase6)
            self.assertIn(script_text, script)
        self.assertIn("Exit 1:", phase6)
        self.assertIn("Exit 2:", phase6)
        self.assertIn("Exit 0 with `tests: skipped` isn't a failure.", phase6)
        self.assertIn("SESSION_HANDOFF.md", phase6)
        self.assertIn("Exit: 0 done, 1 tests failed, 2 refused or error.", script)

    def test_ship_skill_hands_off_and_marks_the_session(self):
        data = frontmatter(skill("ship"))
        self.assertNotEqual(data.get("disable-model-invocation"), "true")
        text = body(skill("ship"))
        self.assertIn("state/ship/${CLAUDE_SESSION_ID}", text)
        self.assertIn("ship #", text)
        self.assertIn("Never `--draft`", text)
        self.assertNotIn("gh pr create --draft", text)
        self.assertIn("that's the same hand-off: invoke pr-review-loop once", text)

    def test_ship_names_the_session_placeholder_only_in_its_marker_commands(self):
        # Claude Code substitutes the placeholder everywhere in the text, prose included.
        text = body(skill("ship"))
        self.assertEqual(text.count("${CLAUDE_SESSION_ID}"), 2)
        for line in text.splitlines():
            if "${CLAUDE_SESSION_ID}" in line:
                self.assertRegex(line, r"^\s+`(mkdir|rm) ")
        self.assertIn("The session-ID placeholder in these commands isn't an environment variable", text)
        self.assertEqual(len(re.findall(r"with the Bash tool:\n\s+`(?:mkdir|rm) ", text)), 2)

    def test_ship_gates_get_a_long_bash_timeout(self):
        gates = section(body(skill("ship")), "## 2. Gates")
        self.assertIn("Bash tool's `timeout: 600000`", gates)

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

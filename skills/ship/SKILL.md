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
  - Bash(rm -f "$HOME/.claude/state/ship/*")
  - Skill
  - AskUserQuestion
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
- If the tree has uncommitted changes, ask (AskUserQuestion) whether they belong in this PR before going on.
- If the PR for this branch is OPEN and not a draft, skip to step 6 with its number. If it's a draft, say so and stop: drafts opt out of auto-merge.

## 2. Gates
Run the repo's gates with the same discovery as pr-review-loop Phase 2 step 4 (`package.json` scripts: lint → typecheck → test → build; `pyproject.toml` or `Makefile` commands; a repo test command in CLAUDE.md or AGENTS.md wins). All must pass. Run long gates with the Bash tool's `timeout: 600000` (up to 10 min), or in the background and wait for the notification. Remember the test command: pr-review-loop's Phase 6 passes the same command to the cleanup script.

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
1. Mark this session as shipping, so the pr_created hook stays quiet. Run it with the Bash tool:
   `mkdir -p "$HOME/.claude/state/ship" && touch "$HOME/.claude/state/ship/${CLAUDE_SESSION_ID}"`
   The session-ID placeholder in these commands isn't an environment variable: Claude Code replaces it with the current session ID when it loads this skill ("Available string substitutions", https://code.claude.com/docs/en/skills), so the command already holds the literal ID. The hook reads the same ID from its input's `session_id` field ("Common input fields", https://code.claude.com/docs/en/hooks). If the marker is missing anyway, the hook's note only repeats step 6's hand-off (see step 6).
2. `git push -u origin HEAD`
3. Write the PR body to a scratchpad file with the Write tool:
   - what changed and why;
   - how it was tested (gate commands and results);
   - the review level, or "review skipped: docs/config only" or "under 20 lines".
   - No AI attribution.
4. `gh pr create --title "<imperative title>" --body-file <file>`:
   - Base: the default branch, unless this branch is stacked on another open PR's head branch; then pass `--base <that branch>`.
   - Never `--draft`.
5. Clear the mark. Run it with the Bash tool:
   `rm -f "$HOME/.claude/state/ship/${CLAUDE_SESSION_ID}"`

## 6. Hand off
In the same turn, invoke the `pr-review-loop` skill with `ship #<N>`. Don't stop between opening the PR and handing off. If the pr_created hook also asks for `ship #<N>` (its marker was missing), that's the same hand-off: invoke pr-review-loop once.

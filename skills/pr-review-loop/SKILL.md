---
name: pr-review-loop
description: Continuous PR review loop — address every piece of review, bot, and CI feedback until the PR (or a stack of PRs) is clean, optionally rebasing a stack first. Run manually with /pr-review-loop.
argument-hint: "[max-iterations] [#PR ...] [extra instructions]"
disable-model-invocation: true
allowed-tools:
  - Bash(gh:*)
  - Bash(git:*)
  - Bash(npm:*)
  - Bash(npx:*)
  - Bash(curl:*)
  - Bash(sleep:*)
  - Bash(bash:*)
  - Read
  - Edit
  - Write
  - Grep
  - Glob
  - TodoWrite
---

# PR Review Loop

## Context (preloaded; every line falls back instead of failing)
- Current-branch PR: !`gh pr view --json number,url,headRefName,baseRefName,state,isDraft --jq '"#\(.number) \(.headRefName) -> \(.baseRefName) \(.state) draft=\(.isDraft) \(.url)"' 2>/dev/null || echo "NONE"`
- My open PRs: !`gh pr list --author @me --state open --json number,headRefName,baseRefName --jq '.[] | "#\(.number) \(.headRefName) -> \(.baseRefName)"' 2>/dev/null || echo "none"`
- Me: !`gh api user --jq .login 2>/dev/null || echo "unknown"`
- Working tree: !`git status --short --branch 2>/dev/null || echo "not a git repo"`
- Project manifests: !`git ls-files '*package.json' '*pyproject.toml' 'Makefile' '*/Makefile' 2>/dev/null | head -10 || echo "none"`
- Arguments: `$ARGUMENTS`

## Interpret the arguments
- A leading integer is max iterations (default 25).
- `#N` tokens are the PRs to work on. If none are given, use the current-branch PR. If that is `NONE`, stop and tell the user there is no PR for this branch.
- Anything else is an extra instruction from the user (e.g. "rebase the stack"). Do it before Phase 1 and follow the rules below.

If the working tree has unrelated uncommitted changes, ask before continuing.

## Goal
Address ALL feedback until clean. "Low priority" ≠ optional. Every item is either **fixed** or **declined with a technical justification**. Track every item in TodoWrite as `[#PR source:id] summary`.

| Level | Handling |
|---|---|
| 🔴 Critical / 🟡 Should fix | Fix. Blocks merge. |
| 🟢 Suggestion / nitpick | Fix, or decline with a valid reason (see Guardrails) |
| 📝 Docs / style | Fix |

`gh api` fills `{owner}` and `{repo}` from the current directory. Never hand-substitute them.

---

## Stacked PRs and rebases (only when the user asked for it)

A stack is a chain of PRs where each one's base is the previous one's head branch (see "My open PRs" above). Rebasing rewrites pushed history, so it runs **only** when the user explicitly asked in the arguments or conversation.

1. `git fetch origin` and map the stack bottom → top from the PR list.
2. Check out the **top** branch and rebase the whole chain in one pass: `git rebase --update-refs origin/<bottom-base>`. This moves every intermediate branch too.
3. Resolve conflicts preserving both sides' intent. If a conflict is ambiguous, stop and ask.
4. Run validation (Phase 2 step 4) on each branch.
5. **Confirm with the user** before pushing, listing each branch and its old → new SHA. Then push each with `git push --force-with-lease origin <branch>`. Never plain `--force`.
6. Then run the loop below for every PR in the stack, bottom first. A fix in a lower PR needs another `--update-refs` rebase of the branches above it. That repeat rebase is covered by the original request, but still confirm before force-pushing.

---

## Phase 1 — Assess (read everything before changing anything)

Run for each PR being worked on.

### 1.1 Review threads — GraphQL is the only source of truth
Run the bundled script from this skill's base directory:
```bash
bash <skill-base-dir>/scripts/threads.sh <PR>         # unresolved threads only
bash <skill-base-dir>/scripts/threads.sh <PR> --all   # every thread, for the final check
```
It paginates automatically. Each thread returns: `thread` (id to resolve), `reply_to` (comment id to reply to), `last_by`, `on_commit`, `ask`, `last`.

Classify every unresolved thread into exactly one bucket:

| Condition | Action |
|---|---|
| `last_by` == me (fix SHA or decline already posted) | Missed resolve → verify, then resolve now |
| `last_by` == reviewer | Open item → queue for Phase 2 |
| Reviewer flags code that a later commit already fixed (`on_commit` predates the fix) | Race → reply with the fixing SHA and resolve; do not re-fix |

`outdated: true` only means nearby lines moved. It is **never** grounds to resolve. After a rebase, many threads go outdated. Check each one against the new code.

### 1.2 Review summaries — hidden feedback lives here
Parse for "Comments suppressed due to low confidence", collapsed `<details>`, and "Nitpick" sections. These never become threads but are real feedback.
```bash
gh api --paginate "repos/{owner}/{repo}/pulls/<PR>/reviews" --jq '.[] | select(.body != "") | {id, user:.user.login, state, at:.submitted_at, body}'
```

### 1.3 PR-level comments (bots and humans — commonly missed)
Read full bodies. Parse every section: Must Fix, Suggestions, Recommendations, Code Quality, Security, Performance.
```bash
gh pr view <PR> --json comments --jq '.comments[] | select(.author.login != "<me>") | {author:.author.login, at:.createdAt, body}'
```
Bots often edit one summary comment in place, so re-read it each iteration. On later iterations, focus on items newer than your last push.

### 1.4 CI and deployments
```bash
gh pr checks <PR>
gh run list --branch <head-branch> --limit 5
gh run view <run-id> --log-failed | tail -150   # for each failed run
```
Vercel (only if the repo uses it):
```bash
gh pr view <PR> --json comments --jq '[.comments[] | select(.author.login|test("vercel";"i"))] | last | .body'
```

### 1.5 Assessment report
```
PR ASSESSMENT — #PR — iteration N/MAX
═══════════════════════════════
Open threads:      [N]   (missed resolves: [N], races: [N])
Review/bot items:  🔴[N] 🟡[N] 🟢[N] 📝[N]
CI failures:       [N]
Vercel:            [status | n/a]
TOTAL:             [N]
═══════════════════════════════
```
If TOTAL is 0 and CI is green for every PR, go to Phase 4.

---

## Phase 2 — Address

1. Read the surrounding code before editing, and confirm the feedback is correct for this codebase.
2. Fix it, or decline with a valid reason.
3. Group related fixes into logical commits. Message format: `fix(review): <what> (addresses <reviewer> on <path>)`.
4. **Validate locally before pushing.** Use the manifests from the context above:
   - For each `package.json`, read its `scripts` and run what exists, fastest first: lint → typecheck → test → build. Use `npm --prefix <dir> run <script>`. If there is no typecheck script and a `tsconfig.json` exists there, run `npx tsc --noEmit -p <dir>`.
   - For `pyproject.toml` or `Makefile` projects, read them to find the lint and test commands. If a needed tool is not pre-approved, request it once.
   - If there are no manifests, say so and rely on CI.
   - Never push failing validation.

**Stop and ask first for:** architectural changes, breaking API changes, or ambiguous feedback. Keep working on everything else, then report the blocked items.

---

## Phase 3 — Push, verify, close threads

```bash
git push origin HEAD
gh pr checks <PR> --watch --interval 30   # use a long Bash timeout; re-run if it times out
```
Verify all checks pass. If Vercel is in use, confirm the preview returns 200: `curl -s -o /dev/null -w '%{http_code}\n' <preview-url>`.

### Thread protocol: reply → verify → resolve, always in that order
1. **Reply** to every thread you handled:
   ```bash
   gh api "repos/{owner}/{repo}/pulls/<PR>/comments/<reply_to>/replies" -f body="Fixed in \`<sha>\` — <what changed>"
   ```
   For declines, reply `Declining — <valid reason>`. A resolution with no reply is indistinguishable from a dismissal.
2. **Resolve** only after CI passes on the fix commit:
   ```bash
   gh api graphql -f query='mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{isResolved}}}' -F id=<thread>
   ```
3. **PR-level and summary items** have no threads to resolve. Post one comment listing each item with its fix SHA or decline reason.
4. **Sweep** before ending every iteration (safe to re-run): run `threads.sh <PR>` again. Every remaining thread must have `last_by` == reviewer and be queued. If `last_by` is you, the thread was replied to but not resolved, so resolve it now.

---

## Phase 4 — Monitor loop

One iteration is one push cycle. After each push:
1. Wait for CI (`gh pr checks --watch`).
2. Wait for bot re-reviews. Poll every 60s (`sleep 60`) for up to 5 minutes, looking for reviews or comments newer than the push, or edited bot summaries.
3. If new feedback appears, return to Phase 1.

Exit when any of these is true:
- **Clean:** the sweep shows 0 open threads on every PR, CI is green, and no new feedback arrived during the quiet window.
- **Max iterations reached:** report the remaining items.
- **Blocked:** every remaining item needs user input, so report and ask.

---

## Phase 5 — Final report
```
PR REVIEW COMPLETE
═══════════════════════════════
PRs:               [#N, …]
Iterations:        [N]
Rebased:           [branches, old → new SHA | no]
Addressed:         🔴[N] 🟡[N] 🟢[N] 📝[N]
Declined:          [N] (each with reason)
Commits:           [sha — message, …]
CI:                [per PR: passing | failing]
Vercel:            [status | n/a]
Unresolved threads:[N] (threads.sh --all verified, must be 0)
Needs your input:  [list or none]
Ready for merge:   [Yes | No — why]
═══════════════════════════════
```
If the user sees "phantom" unresolved threads on GitHub, tell them to hard-refresh. GraphQL is authoritative.

---

## Guardrails

**Never:**
- Plain `--force` push, or `--force-with-lease` outside a user-requested rebase
- Use `--no-verify`
- Batch-resolve threads without verification
- Resolve a thread without replying first
- Resolve a thread whose last word is the reviewer's unanswered feedback
- Resolve a thread on `isOutdated` alone
- Trust the rendered PR page for resolution state
- Skip any section of a bot comment

**Valid decline reasons:**
- Conflicts with an established codebase pattern (cite file:line)
- Out of scope, with a created issue (`gh issue create`, cite #N)
- A specific technical constraint
- Already handled in [location]

**Invalid decline reasons:**
- "Just a suggestion"
- "Low priority"
- "Will do later" without an issue

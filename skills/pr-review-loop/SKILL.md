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
  - Bash(pnpm:*)
  - Bash(curl:*)
  - Bash(bash:*)
  - Monitor
  - TaskStop
  - Read
  - Edit
  - Write
  - Grep
  - Glob
---

# PR Review Loop

## Context (preloaded; every line falls back instead of failing)
- Current-branch PR: !`gh pr view --json number,url,headRefName,baseRefName,state,isDraft --jq '"#\(.number) \(.headRefName) -> \(.baseRefName) \(.state) draft=\(.isDraft) \(.url)"' 2>/dev/null || echo "NONE"`
- My open PRs: !`gh pr list --author @me --state open --json number,headRefName,baseRefName --jq '.[] | "#\(.number) \(.headRefName) -> \(.baseRefName)"' 2>/dev/null | grep . || echo "none"`
- Me: !`gh api user --jq .login 2>/dev/null || echo "unknown"`
- Working tree: !`git status --short --branch 2>/dev/null || echo "not a git repo"`
- Project manifests: !`git ls-files '*package.json' '*pnpm-lock.yaml' '*pyproject.toml' 'Makefile' '*/Makefile' 2>/dev/null | head -20 | grep . || echo "none"`
- Arguments: `$ARGUMENTS`

## Interpret the arguments
- A leading integer is max iterations (default 25).
- `#N` tokens are the PRs to work on. If none are given, use the current-branch PR. If that is `NONE`, stop and tell the user there is no PR for this branch.
- Anything else is an extra instruction from the user (e.g. "rebase the stack"). Do it before Phase 1 and follow the rules below.

If the working tree has unrelated uncommitted changes, ask before continuing.

## Goal
Address ALL feedback until clean. "Low priority" ≠ optional. Every item is either **fixed** or **declined with a technical justification**. Track every item in the session's task list (TodoWrite or the Task tools, whichever this session has) as `[#PR source:id] summary`.

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
   - **If the bottom PR was squash-merged**, its commits are not in `origin/<bottom-base>`, so a plain rebase replays them and conflicts. Cut them off instead: `git rebase --update-refs --onto origin/<bottom-base> <merged-tip>`, where `<merged-tip>` is the merged PR's last head commit (`gh pr view <N> --json headRefOid --jq .headRefOid`). Then point the next PR at the new base (`gh pr edit <next> --base <bottom-base>`) if GitHub didn't retarget it already.
3. Resolve conflicts preserving both sides' intent. If a conflict is ambiguous, stop and ask.
4. Run validation (Phase 2 step 4) on each branch.
5. **Confirm with the user** before pushing, listing each branch and its old → new SHA, and any base change. Then push each with `git push --force-with-lease origin <branch>`. Never plain `--force`.
6. Then run the loop below for every PR in the stack, bottom first. A fix in a lower PR needs another `--update-refs` rebase of the branches above it. That repeat rebase is covered by the original request, but still confirm before force-pushing.

---

## Phase 1 — Assess (read everything before changing anything)

Run for each PR being worked on.

### 1.1 Review threads — GraphQL is the only source of truth
Run the bundled script:
```bash
bash "${CLAUDE_SKILL_DIR}/scripts/threads.sh" <PR>         # unresolved threads only
bash "${CLAUDE_SKILL_DIR}/scripts/threads.sh" <PR> --all   # every thread, for the final check
```
It paginates automatically. Each thread returns: `thread` (id to resolve), `reply_to` (comment id to reply to), `last_id`, `last_by`, `on_commit`, `ask`, `last`, `truncated`.

`ask` and `last` are cut to 400 and 300 characters. When `truncated` is true, read the full bodies before classifying, because suggestion blocks and the rest of a bot's reasoning come at the end:
```bash
gh api "repos/{owner}/{repo}/pulls/comments/<reply_to>" --jq .body   # opening comment
gh api "repos/{owner}/{repo}/pulls/comments/<last_id>" --jq .body    # latest comment
```

Classify every unresolved thread into exactly one bucket. Check the rows in order; the first match wins:

| # | Condition | Action |
|---|---|---|
| 1 | `last_by` == me (fix SHA or decline already posted) | Missed resolve → verify, then resolve now |
| 2 | `last_by` == reviewer, but a later commit already changed the flagged code (`git log --oneline <on_commit>..HEAD -- <path>` shows it) | Race → verify the fix covers it, reply with the fixing SHA and resolve; do not re-fix |
| 3 | `last_by` == reviewer | Open item → queue for Phase 2 |

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
   - For each `package.json`, read its `scripts` and run what exists, fastest first: lint → typecheck → test → build. Pick the package manager from the lockfile: if a `pnpm-lock.yaml` is listed in that directory or an ancestor (a workspace root), use `pnpm --dir <dir> run <script>`, otherwise `npm --prefix <dir> run <script>`. If there is no typecheck script and a `tsconfig.json` exists there, run `pnpm --dir <dir> exec tsc --noEmit` or `npx tsc --noEmit -p <dir>` to match.
   - For `pyproject.toml` or `Makefile` projects, read them to find the lint and test commands. If a needed tool is not pre-approved, request it once.
   - If there are no manifests, say so and rely on CI.
   - Never push failing validation.

**Stop and ask first for:** architectural changes, breaking API changes, or ambiguous feedback. Keep working on everything else, then report the blocked items.

---

## Phase 3 — Push, verify, close threads

```bash
git push origin HEAD
gh pr checks <PR> --watch --interval 30   # Bash with run_in_background: one notification when CI finishes
```
Verify all checks pass. Foreground `sleep` is blocked, so never wait on CI with a sleep loop. If Vercel is in use, confirm the preview returns 200: `curl -s -o /dev/null -w '%{http_code}\n' <preview-url>`.

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
1. Wait for CI (the background `gh pr checks --watch` from Phase 3).
2. Wait for bot re-reviews with the **Monitor** tool (`timeout_ms: 360000`). This command polls every 30s for 5 minutes and prints one line per new or edited review, inline comment or PR comment since the push commit, then exits:
   ```bash
   since=$(TZ=UTC git log -1 --date=format-local:%Y-%m-%dT%H:%M:%SZ --format=%cd)
   for i in 1 2 3 4 5 6 7 8 9 10; do
     gh api "repos/{owner}/{repo}/pulls/<PR>/reviews" --jq ".[] | select(.submitted_at > \"$since\" and .user.login != \"<me>\") | \"review \(.id) by \(.user.login)\"" || true
     gh api "repos/{owner}/{repo}/pulls/<PR>/comments?since=$since" --jq ".[] | select(.user.login != \"<me>\") | \"inline \(.id) by \(.user.login) at \(.updated_at)\"" || true
     gh api "repos/{owner}/{repo}/issues/<PR>/comments?since=$since" --jq ".[] | select(.user.login != \"<me>\") | \"comment \(.id) by \(.user.login) at \(.updated_at)\"" || true
     sleep 30
   done | while IFS= read -r ev; do   # print each event once
     case "$seen" in *"|$ev|"*) ;; *) seen="$seen|$ev|"; echo "$ev" ;; esac
   done
   echo "quiet window over"
   ```
   The `?since=` filter matches on update time, so a bot editing its summary in place shows up as a new line.
3. If new feedback appears, stop the monitor with TaskStop and return to Phase 1. Phase 1 re-reads everything, so anything that lands later is caught on the next iteration.

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

---
name: pr-review-loop
description: Continuous PR review loop — address every piece of review, bot, and CI feedback until the PR (or a stack of PRs) is clean, optionally rebasing a stack first. With `ship`, squash-merge it once clean and run the post-merge cleanup. Invoke after a PR opens (the ship loop hands off here), or when asked to work through a PR's review feedback.
argument-hint: "[ship] [max-iterations] [#PR ...] [extra instructions]"
disable-model-invocation: false
allowed-tools:
  - Bash(gh:*)
  - Bash(git:*)
  - Bash(npm:*)
  - Bash(npx tsc *)
  - Bash(pnpm:*)
  - Bash(python -m unittest discover -s tests)
  - Bash(bash "${CLAUDE_SKILL_DIR}/scripts/threads.sh" *)
  - Bash(POST_MERGE_TEST_TIMEOUT=540 python "$HOME/.claude/scripts/post_merge_cleanup.py" --repo . --here)
  - Bash(POST_MERGE_TEST_TIMEOUT=540 python "$HOME/.claude/scripts/post_merge_cleanup.py" --repo . --here --test 'python -m unittest discover -s tests')
  - Bash(POST_MERGE_TEST_TIMEOUT=540 python "$HOME/.claude/scripts/post_merge_cleanup.py" --repo . --here --test 'npm test')
  - Bash(POST_MERGE_TEST_TIMEOUT=540 python "$HOME/.claude/scripts/post_merge_cleanup.py" --repo . --here --test 'pnpm test')
  - Monitor
  - TaskStop
  - TodoWrite
  - TaskCreate
  - TaskUpdate
  - TaskList
  - AskUserQuestion
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
- `ship` anywhere in the arguments enables Phase 6: once the loop exits clean, merge and clean up.
- Inside /ship, the pr_created hook's note (sent only when /ship's marker was missing) arrives right after `gh pr create`, before /ship's own hand-off: hand off once. A second `ship #N` for a PR this session's loop is already running on is a duplicate: ignore it and carry on with the running loop.
- A bare integer is max iterations (default 25).
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
Run it in exactly this form: the allowed-tools rule pre-approves `bash "${CLAUDE_SKILL_DIR}/scripts/threads.sh" …` and no other `bash` command. It paginates automatically. Each thread returns: `thread` (id to resolve), `reply_to` (comment id to reply to), `last_id`, `last_by`, `on_commit`, `ask`, `last`, `truncated`.

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
If TOTAL is 0 and CI is green for every PR, go to Phase 4. With `ship`, TOTAL 0 and CI still running also goes to Phase 4.

---

## Phase 2 — Address

1. Read the surrounding code before editing, and confirm the feedback is correct for this codebase.
2. Fix it, or decline with a valid reason.
3. Group related fixes into logical commits. Message format: `fix(review): <what> (addresses <reviewer> on <path>)`.
4. **Validate locally before pushing.** Use the manifests from the context above:
   - For each `package.json`, read its `scripts` and run what exists, fastest first: lint → typecheck → test → build. Pick the package manager from the lockfile: if a `pnpm-lock.yaml` is listed in that directory or an ancestor (a workspace root), use `pnpm --dir <dir> run <script>`, otherwise `npm --prefix <dir> run <script>`. If there is no typecheck script and a `tsconfig.json` exists there, run `pnpm --dir <dir> exec tsc --noEmit` or `npx tsc --noEmit -p <dir>` to match.
   - For `pyproject.toml` or `Makefile` projects, read them to find the lint and test commands. If a needed tool is not pre-approved, request it once.
   - No `package.json`, `pyproject.toml` or `Makefile`, but `tests/test_*.py` files: the test command is `python -m unittest discover -s tests`. It is pre-approved in exactly that form (a rule with no `*`), so run it without extra arguments.
   - If none of these applies, say so and rely on CI.
   - Never push failing validation.
5. **Fix rounds get the gates only.** No local `/code-review` and no re-requested bot review for fix commits. One exception: record the PR head SHA when the loop starts. If this PR's fix commits change 50 or more lines outside tests and docs, and the repo has `.github/workflows/claude-code-review.yml`, post `@claude review` as a PR comment, once per PR. Count the lines as the insertions plus deletions this prints:
   `git diff --shortstat <start-sha>..HEAD -- . ':(exclude)*test*' ':(exclude)*.md' ':(exclude)docs/**'`

**Stop and ask first for:** architectural changes, breaking API changes, or ambiguous feedback. Keep working on everything else, then report the blocked items.

---

## Phase 3 — Push, verify, close threads

```bash
git push origin HEAD
gh pr checks <PR> --watch --interval 30   # Bash with run_in_background: one notification when CI finishes
```
Verify all checks pass. Foreground `sleep` is blocked, so never wait on CI with a sleep loop. If Vercel is in use, confirm the preview returns 200: `curl -s -o /dev/null -w '%{http_code}\n' <preview-url>` (`curl` isn't pre-approved: it goes through the normal permission check).

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
4. **Sweep** before ending every iteration (safe to re-run): run `bash "${CLAUDE_SKILL_DIR}/scripts/threads.sh" <PR>` again. Every remaining thread must have `last_by` == reviewer and be queued. If `last_by` is you, the thread was replied to but not resolved, so resolve it now.

---

## Phase 4 — Monitor loop

One iteration is one push cycle. After each push:
1. Wait for CI (the background `gh pr checks --watch` from Phase 3). If this loop hasn't pushed yet (a fresh PR from /ship), start `gh pr checks <PR> --watch --interval 30` with Bash `run_in_background` now.
2. Wait for reviewers with the **Monitor** tool (`timeout_ms: 1500000`). The command below does two things, then prints `quiet window over` and exits:
   - It polls every 30 s until no review request is pending and every check run on the head SHA whose name contains `review` has finished, up to 15 minutes. It waits at least one minute, so a review request that registers late isn't missed.
   - Then it holds a 2-minute quiet window for summary edits.
   Throughout, it prints one line per new or edited review, inline comment or PR comment since the push commit.
   - A failed `gh` query (auth, API or network error) never counts as "nothing pending": `waiting` prints `error`, which isn't `0`, so the poll retries 30 s later. It prints `review status query failed` once. If the last poll still failed when the 15 minutes end, it prints `review status unknown: gh query failed`. That means the wait is **not clean**: don't merge, report it as blocked (see the exit rules below).
   - A failed feedback query in `events` never counts as "no feedback" either: it prints `feedback query failed` (once per wait, like every line). Feedback may have arrived unseen, so that wait is **not clean** either.
   ```bash
   sha=$(git rev-parse HEAD)
   since=$(TZ=UTC git log -1 --date=format-local:%Y-%m-%dT%H:%M:%SZ --format=%cd)
   events() {
     gh api "repos/{owner}/{repo}/pulls/<PR>/reviews" --jq ".[] | select(.submitted_at > \"$since\" and .user.login != \"<me>\") | \"review \(.id) by \(.user.login)\"" || echo "feedback query failed"
     gh api "repos/{owner}/{repo}/pulls/<PR>/comments?since=$since" --jq ".[] | select(.user.login != \"<me>\") | \"inline \(.id) by \(.user.login) at \(.updated_at)\"" || echo "feedback query failed"
     gh api "repos/{owner}/{repo}/issues/<PR>/comments?since=$since" --jq ".[] | select(.user.login != \"<me>\") | \"comment \(.id) by \(.user.login) at \(.updated_at)\"" || echo "feedback query failed"
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
   The `?since=` filter matches on update time, so a bot editing its summary in place shows up as a new line. `review status query failed`, `review status unknown: …` and `feedback query failed` aren't feedback: don't return to Phase 1 for them. If the Monitor ends at its deadline without printing `quiet window over`, treat it as `review status unknown`: run the wait once more, then report blocked.
3. If new feedback appears, stop the monitor with TaskStop and return to Phase 1. Phase 1 re-reads everything, so anything that lands later is caught on the next iteration.

Exit when any of these is true:
- **Clean:** the sweep shows 0 open threads on every PR, CI is green, no new feedback arrived during the quiet window, and the wait didn't print `review status unknown` or `feedback query failed`. With `ship`, go to Phase 6, then Phase 5.
- **Review status unknown or feedback query failed:** the wait printed `review status unknown: gh query failed` or `feedback query failed`, so it never established that reviewers finished or that no feedback arrived. That isn't clean. Run one more Phase 4 wait; if it prints either line again, report "blocked: review status unknown (gh query failed)" or "blocked: feedback query failed" (whichever it printed) and stop without merging.
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

## Phase 6 — Ship (only with `ship`)

Check every condition fresh, right before merging:
```bash
gh pr view <PR> --json isDraft,labels,baseRefName,mergeStateStatus,headRefOid --jq '{draft: .isDraft, labels: [.labels[].name], base: .baseRefName, state: .mergeStateStatus, head: .headRefOid}'
gh repo view --json defaultBranchRef --jq .defaultBranchRef.name
gh pr checks <PR> --json name,bucket --jq '[.[] | select(.bucket != "pass" and .bucket != "skipping")] | length'
bash "${CLAUDE_SKILL_DIR}/scripts/threads.sh" <PR>
```
All of these must hold:
- the PR isn't a draft and has no `no-automerge` label. Otherwise report "auto-merge off: <draft | no-automerge>".
- its base is the default branch. Otherwise report "stacked: run /finalize-stack".
- `head` equals `git rev-parse HEAD` (nothing landed since the last check);
- the checks query prints `0`. If it instead fails with `no checks reported on the '<branch>' branch`, the repo runs no CI on this PR: count that as `0`. Any other failure is a failed query: run it once more, and if it fails again report "blocked: checks query failed".
- `threads.sh` exits 0 and prints nothing. A non-zero exit is a failed query, not "no threads": run it once more, and if it fails again report "blocked: review threads query failed".
- no item in this loop's task list is waiting on the owner;
- the last Phase 4 wait didn't print `review status unknown` or `feedback query failed`. Otherwise report "blocked: review status unknown (gh query failed)" or "blocked: feedback query failed".
- `mergeStateStatus` is `CLEAN`. On `UNKNOWN`, re-query with the **Monitor** tool, never a foreground `sleep` (`timeout_ms: 180000`; it prints the state every 30 s, up to 3 times, and exits once it isn't `UNKNOWN`):
  ```bash
  for i in 1 2 3; do sleep 30; s=$(gh pr view <PR> --json mergeStateStatus --jq .mergeStateStatus); echo "$s"; [ "$s" != UNKNOWN ] && break; done
  ```
  Still `UNKNOWN` after that, or `BLOCKED`, `BEHIND`, `DIRTY` or `UNSTABLE` with no checks pending: report the state.

To tell pending checks from failing ones, drop `| length` from the checks query: it then lists each check's `bucket` (`pending`, `fail` or `cancel`). If only pending checks keep the count above `0`, wait for CI. If no `gh pr checks <PR> --watch` is running in the background, start `gh pr checks <PR> --watch --interval 30` with Bash `run_in_background`; wait for its notification, then re-check everything. `UNSTABLE` or `BLOCKED` while checks are pending counts as pending, not a stop.

If a condition fails otherwise:
- **The loop can fix it** (`head` moved, new unresolved threads, a failing check): go back to Phase 1.
- **Only the owner can resolve it** (draft or `no-automerge`, stacked, a failed threads or checks query, an item waiting on the owner, review status unknown, a failed feedback query, a merge state that isn't `CLEAN` with no checks pending): report it and stop without merging.

Merge only once every condition holds and no check is pending:
```bash
gh pr merge --squash <PR>          # this argument order matches the permission allow rule
```
If the merge exits non-zero, report its error and stop: don't query the merge SHA, don't run the cleanup, and write no handoff line. Otherwise read the merge SHA:
```bash
gh pr view <PR> --json mergeCommit --jq .mergeCommit.oid
```
If it prints nothing or `null`, the merge isn't confirmed: report that and stop the same way.

Then run the cleanup. Run it from this session's checkout: `--repo .` and `--here` both follow the current directory, and the handoff line goes to that checkout's SESSION_HANDOFF.md, when it has one. Pick the command that fits the gate test command:
- a `tests/test_*.py` suite: `POST_MERGE_TEST_TIMEOUT=540 python "$HOME/.claude/scripts/post_merge_cleanup.py" --repo . --here --test 'python -m unittest discover -s tests'`
- a repo whose `package.json` is at the root and whose test gate is its `test` script: `POST_MERGE_TEST_TIMEOUT=540 python "$HOME/.claude/scripts/post_merge_cleanup.py" --repo . --here --test 'npm test'`, or with pnpm `POST_MERGE_TEST_TIMEOUT=540 python "$HOME/.claude/scripts/post_merge_cleanup.py" --repo . --here --test 'pnpm test'`
- no test command: `POST_MERGE_TEST_TIMEOUT=540 python "$HOME/.claude/scripts/post_merge_cleanup.py" --repo . --here`

Only these exact commands are pre-approved: a Claude Code allow rule without `*` matches one exact command, and the script runs `--test` through a shell, so no rule may leave it open. Any other gate test command still works, passed the same way as `--test '<gate test command>'` after `--here`, but that run goes through the normal permission check.

Run the cleanup with the Bash tool's `timeout: 600000`. If it still moves to the background, wait for its completion notification; never start a second run.

`<gate test command>` is the test command from Phase 2 step 4's discovery (the same one /ship's gates used; a test command in CLAUDE.md or AGENTS.md wins). Pass it single-quoted in Bash. Inside it use double quotes only, because on Windows the script runs it through cmd.exe: no `$VAR`, `'…'`, `~` or `2>/dev/null`. Don't wrap it in `bash -c` and don't switch shells. For a repo whose only tests are `tests/test_*.py`, that's `--test 'python -m unittest discover -s tests'`, which is already safe. For a repo whose `package.json` is at the root and whose test gate is its `test` script, pass `--test 'npm test'` or `--test 'pnpm test'`, not Phase 2's `npm --prefix`/`pnpm --dir` form, so the run stays pre-approved. The script runs `--test` from the repo root, so when the only `package.json` is in a subfolder, keep Phase 2's `npm --prefix <dir> test` (or `pnpm --dir <dir> test`) form: a bare `npm test` would fail there and report a false `tests: fail`. Omit `--test` when the repo has no test command.

Reading the cleanup's exit code and output:
- Report the merge SHA and the cleanup output.
- Exit 1: `tests: fail` on the default branch after the merge. Report it first, in bold, with the output tail. If the tail ends `timed out after 540 s`, say the tests timed out instead.
- Exit 2: either `post-merge cleanup refused: <reason>` (nothing changed) or `post-merge cleanup failed: <error>` followed by what was already deleted or kept. Report the line as printed.
- Exit 0 with `tests: skipped` isn't a failure. Report the `note:` that says why: a live session or uncommitted changes in the main checkout, main not on the default branch, or pull failed. Without `--test` there is no note: the tests are always skipped.
- A kept branch `checked out in this session's worktree` is expected; tell the owner to remove that worktree after the session ends.

---

## Guardrails

**Never:**
- Plain `--force` push, or `--force-with-lease` outside a user-requested rebase
- Merge without the `ship` argument, or while any Phase 6 condition fails
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

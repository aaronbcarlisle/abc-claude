---
name: finalize-stack
description: Finalize a stack of PRs - triage every PR's review feedback in parallel, batch the owner's rulings into one question, restack onto the default branch, then squash-merge bottom-up and run the post-merge cleanup. Owner-invoked; invoking it authorizes --force-with-lease pushes of the stack's branches.
argument-hint: "[#PR in the stack] [extra instructions]"
disable-model-invocation: true
allowed-tools:
  - Bash(gh:*)
  - Bash(git:*)
  - Bash(npm:*)
  - Bash(npx tsc *)
  - Bash(pnpm:*)
  - Bash(python -m unittest discover -s tests)
  - Bash(bash "$HOME/.claude/skills/pr-review-loop/scripts/threads.sh" *)
  - Bash(python "$HOME/.claude/scripts/live_sessions.py" *)
  - Bash(POST_MERGE_TEST_TIMEOUT=540 python "$HOME/.claude/scripts/post_merge_cleanup.py" --repo . --here)
  - Bash(POST_MERGE_TEST_TIMEOUT=540 python "$HOME/.claude/scripts/post_merge_cleanup.py" --repo . --here --test 'python -m unittest discover -s tests')
  - Bash(POST_MERGE_TEST_TIMEOUT=540 python "$HOME/.claude/scripts/post_merge_cleanup.py" --repo . --here --test 'npm test')
  - Bash(POST_MERGE_TEST_TIMEOUT=540 python "$HOME/.claude/scripts/post_merge_cleanup.py" --repo . --here --test 'pnpm test')
  - Monitor
  - TaskStop
  - Agent
  - AskUserQuestion
  - Skill
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

Invoking this skill authorizes `git push --force-with-lease` of the stack's branches; don't ask again before each one. Never a plain force push. The thread protocol, classification table and guardrails are pr-review-loop's: read `~/.claude/skills/pr-review-loop/SKILL.md` Phase 1, Phase 3 and Guardrails first. Commands outside the `allowed-tools` list (another gate test command, for example) go through the normal permission check.

Stop cases: whenever this skill says to stop, report why and stop. Don't restack the rest, don't run the cleanup and don't run `/handoff log`.

## 1. Map the stack
A chain starts at a PR whose base is the default branch, or whose base is a branch whose PR is already merged (that merged PR is the chain's "squash-merged bottom"); each next PR's base is the previous PR's head branch. Build every chain from "My open PRs".
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
   Then sync each checkout with its PR: compare its tip (`git -C <path> rev-parse HEAD`) with `gh pr view <N> --json headRefOid --jq .headRefOid`.
   - Equal: go on.
   - The local tip is an ancestor of the PR head: fast-forward with `git -C <path> merge --ff-only <headRefOid>` (after the fetch).
   - Anything else (local ahead or diverged): stop with "`<head>` in `<path>` doesn't match PR #<N>'s head: sync it, then re-run /finalize-stack".

   Record each PR's head as `<pre-triage tip N>`. It's the first lease value (step 4.4) and the rebase anchor (step 4.2). Also track each branch's remote SHA: the SHA this run last saw on the remote for that branch, starting at `<pre-triage tip N>`.
3. For each PR, check for an unfinished review: a pending review request, or a check run named like `review` that hasn't completed on its head SHA.
   ```bash
   gh pr view <N> --json reviewRequests --jq '.reviewRequests | length'
   gh api "repos/{owner}/{repo}/commits/<head SHA>/check-runs" --jq '[.check_runs[] | select((.name | test("review"; "i")) and .status != "completed")] | length'
   ```
   If either prints more than `0`, run pr-review-loop's Phase 4 wait once for that PR (Monitor, `timeout_ms: 1500000`; the script and its rules are in that skill's Phase 4 step 2), before triaging it. Run it from that PR's checkout, because the script reads `git rev-parse HEAD`. Record `<triage-time>` (per PR: `<triage-time N>`) with `date -u +%Y-%m-%dT%H:%M:%SZ` right after that PR's wait ends, or here when it has no wait; never before the wait. The wait's own `since=` is the time it starts. Phase 1 reads everything up to `<triage-time N>`; step 5 later looks for anything newer. If the wait prints `reviews still pending after 15 min`, `review status unknown: gh query failed` or `feedback query failed`, stop with "blocked: <that line> on #<N>". None of these counts as clean. If a query above fails, treat it as `review status unknown: gh query failed`.
4. In one message, dispatch one agent per PR, in parallel. Use the repo's own implementer agent if it has one, otherwise `implementer`. Fill in this prompt:
   ```
   Triage review feedback for PR #<N> (<head> -> <base>). Work only in <its checkout>.
   1. Read ~/.claude/skills/pr-review-loop/SKILL.md sections 1.1-1.4 and run them for PR #<N>
      (threads: `bash "$HOME/.claude/skills/pr-review-loop/scripts/threads.sh" <N>`, run from that checkout).
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
`git rebase --update-refs` moves only refs that point into the rebased range, so a lower branch's new fix commits aren't in the upper branches until step 3 below has carried them up.
1. `git rebase --update-refs` won't move a branch that's checked out in another worktree. In every PR's checkout except the top PR's, run `git switch --detach` first. This includes this checkout when it holds a lower branch; when it holds the top branch, it's the top PR's checkout and stays attached. A checkout re-attaches (`git -C <path> switch <branch>`) only for its own rebase in step 2, then detaches again.
2. Carry lower fixes up, bottom to top. For each PR above the bottom whose lower PR's tip changed during triage (the lower branch's current tip differs from `<anchor>`: the lower PR's `<pre-triage tip N>` on the first pass, its recorded remote SHA on a re-run), run this in that PR's checkout:
   `git rebase --onto <lower branch> <pre-triage tip of the lower PR> <this branch>`
   Resolve conflicts as in step 3.
3. In the top PR's checkout: `git rebase --update-refs origin/<default>`.
   - If the bottom PR was already squash-merged, cut its commits off instead: `git rebase --update-refs --onto origin/<default> <merged head SHA> <top-branch>`. Detect it with `gh pr view <N> --json state,headRefOid`: `state` is `MERGED`, and `<merged head SHA>` is its `headRefOid`.
   - Resolve conflicts, preserving both sides' intent. An ambiguous conflict means stop and ask.
4. In each other PR's checkout, `git switch <its branch>`, then run the gates there. Push each branch with an explicit lease on the remote SHA this run last saw for it: `git push --force-with-lease=<branch>:<remote SHA> origin <branch>`. Note old → new SHA, and record the SHA pushed as the branch's new remote SHA for the next lease.
5. For each PR, once its checks register, run `gh pr checks <N> --watch --interval 30` (Bash, `run_in_background`).
6. After CI is green, follow pr-review-loop's thread protocol:
   - reply with the fix's post-rebase short SHA (find it with `git log --format=%h -1 --grep=<fix subject> <branch>`, or by matching the fix commit's subject), or `Declining — <reason>`;
   - then resolve;
   - post one comment per PR listing its PR-level items.

   Red CI on a PR goes to that PR's triage agent as a fix (you dispatch it: the step 2.4 prompt, that PR's checkout only, naming the failing check). At most two attempts per PR, then stop and ask.

## 5. Merge bottom-up
For the bottom PR, whose base is the default branch:
1. Check, fresh, right before the merge:
   - **State:** `gh pr view <N> --json isDraft,baseRefName,headRefOid,mergeStateStatus`. `baseRefName` must equal `<default>` and `isDraft` must be false; otherwise stop with "#<N> base is <base>, not <default>" or "#<N> is a draft". `headRefOid` must equal the SHA this run last pushed for that branch, or its `<pre-triage tip N>` if it was never pushed; on a mismatch, stop with "head moved on #<N>: re-run /finalize-stack".
   - **Checks:** `gh pr checks <N> --json bucket --jq '[.[] | select(.bucket != "pass" and .bucket != "skipping")] | length'` prints `0`. If it fails with `no checks reported on the '<branch>' branch`, the repo runs no CI on this PR: count it as `0`. Any other failure is a failed query: run it once more, and if it fails again, stop with "blocked: checks query failed". Drop `| length` to tell pending checks from failing ones: pending checks mean waiting for CI with `gh pr checks <N> --watch --interval 30` in the background (Bash, `run_in_background`), then re-check everything.
   - **Threads:** `bash "$HOME/.claude/skills/pr-review-loop/scripts/threads.sh" <N>` exits 0 and prints nothing. A non-zero exit is a failed query, not "no threads": run it once more, and if it fails again, stop with "blocked: review threads query failed".
   - **No feedback since triage:** check for feedback posted or edited after `<triage-time N>` by anyone but me (`<me>` is `gh api user --jq .login`). These are pr-review-loop Phase 4's three `events()` queries with `since=<triage-time N>`:
     ```bash
     since=<triage-time N>
     gh api "repos/{owner}/{repo}/pulls/<N>/reviews" --jq ".[] | select(.submitted_at > \"$since\" and .user.login != \"<me>\") | \"review \(.id) by \(.user.login)\"" || echo "feedback query failed"
     gh api "repos/{owner}/{repo}/pulls/<N>/comments?since=$since" --jq ".[] | select(.user.login != \"<me>\") | \"inline \(.id) by \(.user.login) at \(.updated_at)\"" || echo "feedback query failed"
     gh api "repos/{owner}/{repo}/issues/<N>/comments?since=$since" --jq ".[] | select(.user.login != \"<me>\") | \"comment \(.id) by \(.user.login) at \(.updated_at)\"" || echo "feedback query failed"
     ```
     A `feedback query failed` line isn't clean: stop with "blocked: feedback query failed on #<N>". Any review, inline or comment line is new feedback that arrived after triage. The first time on #<N>, don't stop the run: dispatch that PR's triage agent once more (the step 2.4 prompt, working only in that PR's checkout, told to handle the listed items), re-run step 4 for #<N> and the PRs above it, take a fresh `<triage-time N>`, and re-check this list. A second batch of new feedback on the same PR stops with "new feedback on #<N> again after re-triage: re-run /finalize-stack".
   - **Merge state:** `mergeStateStatus` is `CLEAN`. On `UNKNOWN`, re-query with the **Monitor** tool, never a foreground wait (`timeout_ms: 180000`; it prints the state every 30 s, up to 3 times, and exits once it isn't `UNKNOWN`):
     ```bash
     for i in 1 2 3; do sleep 30; s=$(gh pr view <N> --json mergeStateStatus --jq .mergeStateStatus); echo "$s"; [ "$s" != UNKNOWN ] && break; done
     ```
     Still `UNKNOWN` after that, or `BLOCKED`, `BEHIND`, `DIRTY` or `UNSTABLE` with no checks pending: stop and report the state.
2. Note its head SHA (`gh pr view <N> --json headRefOid --jq .headRefOid`), then merge it, passing that SHA so GitHub refuses the merge if anything landed since:
   ```bash
   gh pr merge --squash --match-head-commit <head> <N>   # this argument order matches the permission allow rule
   ```
   - If it exits non-zero, run `gh pr view <N> --json headRefOid --jq .headRefOid`. If it no longer equals `<head>`, the head moved: stop with "head moved on #<N>: re-run /finalize-stack". Otherwise report the error and stop.
   - After a merge, read the merge SHA: `gh pr view <N> --json mergeCommit --jq .mergeCommit.oid`. If it prints nothing or `null`, the merge isn't confirmed: stop.
3. `git fetch origin`. Detach every remaining PR's checkout except the top PR's. In the top PR's checkout, run `git rebase --update-refs --onto origin/<default> <merged head SHA> <top-branch>`. Re-attach each remaining PR's checkout with `git -C <path> switch <branch>`, run the gates, and push each remaining branch with `git push --force-with-lease=<branch>:<remote SHA> origin <branch>`, recording the SHA pushed as its new remote SHA.
4. `gh pr view <next> --json baseRefName --jq .baseRefName`. GitHub retargets the next PR when the merged branch is deleted; if it didn't, run `gh pr edit <next> --base <default>`. A failed or empty `baseRefName` query, or a failed `gh pr edit --base`, stops with "retarget of #<next> failed".
5. Wait for green CI on the next PR: `gh pr checks <N> --watch --interval 30` in the background, then re-check. Red CI goes to that PR's triage agent as a fix (at most two attempts per PR, then stop). Don't wait for bot re-reviews: fix commits get the gates only. Repeat from step 1 until the stack is merged.

Stop and ask when a conflict is ambiguous, or when CI still fails on a branch after two fix attempts.

## 6. Cleanup
Run it from the checkout this skill was invoked in (the repo root the context shows): `--repo .` and `--here` follow the current directory. Pick the exact command that fits the gate test command:
- a `tests/test_*.py` suite: `POST_MERGE_TEST_TIMEOUT=540 python "$HOME/.claude/scripts/post_merge_cleanup.py" --repo . --here --test 'python -m unittest discover -s tests'`
- a root `package.json` whose test gate is `test`: `POST_MERGE_TEST_TIMEOUT=540 python "$HOME/.claude/scripts/post_merge_cleanup.py" --repo . --here --test 'npm test'`, or with pnpm `POST_MERGE_TEST_TIMEOUT=540 python "$HOME/.claude/scripts/post_merge_cleanup.py" --repo . --here --test 'pnpm test'`
- no test command: `POST_MERGE_TEST_TIMEOUT=540 python "$HOME/.claude/scripts/post_merge_cleanup.py" --repo . --here`

Any other gate test command is passed single-quoted, as `--test '<gate test command>'`, with double quotes only inside it: on Windows the script runs it through cmd.exe, so no `$VAR`, `'…'`, `~` or `2>/dev/null`. A `package.json` only in a subfolder keeps the `npm --prefix <dir> test` form. That run goes through the normal permission check.

Run it with the Bash tool's `timeout: 600000`, and never start a second run. It removes the clean `-s<N>` worktrees and deletes the merged branches. Since PR #17, a merged branch checked out in this checkout is deleted after the checkout is detached at origin/<default>; its note says to start the next branch with `git switch -c`. Read the exit code and output as pr-review-loop Phase 6 does:
- Exit 1 is `tests: fail`: report it first, in bold, with the output tail. If the tail ends `timed out after 540 s`, say the tests timed out instead.
- Exit 2 is refused or failed: report the line as printed, with what was already deleted or kept.
- Exit 0 with `tests: skipped` isn't a failure. Report the `note:` that says why: a live session or uncommitted changes in the main checkout, main not on the default branch, or pull failed. Without `--test` there is no note: the tests are always skipped.
- A kept branch `checked out in this session's worktree` is expected; tell the owner to remove that worktree after the session ends.

If `SESSION_HANDOFF.md` exists, run `/handoff log`.

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

# PR and phase automation: design

Date: 2026-09-27. Repo: abc-claude (`~/.claude`). Status: awaiting owner review.

## Goal

The owner's attention should go only to real judgement calls: taste, architecture, ambiguous review feedback and owner-only checks. Everything else runs unattended:

- An opened PR goes through review and merge without prompting.
- A phase keeps running overnight from the handoff ledger.
- Tests are written adversarially before the implementation.
- Finalizing stacked PRs and cleaning up after a merge stop taking whole sessions.

Source: the two insights reports' "On the Horizon" items, and the owner's answers on 2026-09-27.

## Owner decisions (2026-09-27)

| Decision | Ruling |
|---|---|
| Auto-merge scope | Every non-draft PR a session opens against the default branch, in any repo. Opt out with `--draft` or a `no-automerge` label. Local-merge repos (PyConsole) never run `gh pr create`, so they're unaffected. |
| Review flow | One deep local review before the PR, one bot pass on the PR, fix commits get gates only (see Review flow). |
| Autopilot trigger | On demand, per repo, started by the owner. No nightly schedule. |
| test-author model | Opus. |

## Review flow

Measured on the last 12 merged raid-planner PRs: 7 bot review threads in total (Copilot 6, claude[bot] 1), and 7 of the 12 PRs got no bot review at all. The cost lies in the rounds, not in the number of findings.

1. **Before the PR:** run the repo's gates, then one `/code-review` over the whole branch diff with `--fix`. Use `high` when the diff is over 300 changed lines or touches auth, security, migrations or persistence; use `medium` otherwise. Skip it for diffs that touch only docs or config, or have fewer than 20 changed lines.
2. **At the PR:** each bot reviews once: Copilot everywhere, and claude[bot] where a repo already has `claude-code-review.yml`. No third bot. raid-planner's Copilot ruleset already has `review_on_push: false`, and its Claude workflow skips `synchronize`.
3. **Fix round:** pr-review-loop addresses every item. Fix commits get the gates only: no local re-review and no re-requested bot review. The one exception: if the fix round changed 50 or more non-test, non-doc lines in a repo that has claude[bot], post `@claude review` once for that PR.
4. **Merge:** once the gates are green and every thread has a reply and is resolved.

`/autofix-pr` (cloud) is not part of the loop. It stays available for a PR left open while the owner's machine is off. It never merges and can't run local gates.

## Components

Five sub-projects, each its own PR, built in this order: A's script is used by B, C and E, and B's hooks are extended by E.

### A. Post-merge cleanup script: `scripts/post_merge_cleanup.py`

A deterministic Python script; no model is involved. `scripts/` is added to the `.gitignore` whitelist.

```
post_merge_cleanup.py [--repo PATH] [--here] [--test CMD] [--handoff PATH] [--dry-run] [--json]
```

1. Resolve the repo top level. Get the default branch from `gh repo view --json defaultBranchRef`, falling back to `origin/HEAD`, then `main`/`master`.
2. **Live-session guard** (shared module `scripts/live_sessions.py`): a checkout is live when a `~/.claude/sessions/*.json` entry has a running `pid` and a `cwd` inside that checkout or worktree. Live checkouts and worktrees are skipped and reported. `--here` means the caller is the session in the checkout that contains the current directory, whether that's the main checkout or a worktree. That one checkout is exempt; every other checkout is still checked. A merged branch checked out in the caller's own worktree is kept and reported as "remove after this session ends", because a session can't remove the worktree it runs in.
3. `git fetch --prune origin`.
4. **Branch deletion.** A local branch other than the default is deleted only when one of these holds:
   - it's an ancestor of `origin/<default>`;
   - a merged PR exists for that head (`gh pr list --state merged --head <b>`), and the local tip equals or is an ancestor of that PR's `headRefOid`, so no unpushed commits are lost.

   Otherwise it's kept, with the reason reported. A branch checked out in a clean, non-live worktree gets `git worktree remove` first. Untracked files count as dirty. A branch checked out in the main checkout gets a switch to the default branch first, which requires a clean tree.
5. When the main checkout is clean and on the default branch: `git pull --ff-only`. Then `git worktree prune`.
6. `--test CMD` runs in the main checkout. The result is `ok`, `fail` (with the last 40 output lines) or `skipped`.
7. Handoff: when `--handoff` is given, or `SESSION_HANDOFF.md` exists at the repo root, append one line under `## Progress Log`, keeping the file's line endings:
   `- YYYY-MM-DD HH:MM | post-merge cleanup | tests <ok|fail|skipped> | <default short SHA> | deleted: …; kept: … (reason)`.
   The script never commits: that would mean committing on the default branch.
8. Exit codes: 0 done, 1 tests failed, 2 refused (not a repo, or a dirty or live main checkout without `--here`). `--json` prints a summary object. `--dry-run` changes nothing and reports what it would do.

Tests (`tests/test_post_merge_cleanup.py`) run against temp repos with a bare origin and a fake `gh` put first on PATH, the same pattern as `tests/fake_claude.py`. They cover:
- a squash-merged branch deleted;
- a branch with unpushed commits kept;
- a dirty worktree kept;
- a live-session worktree kept;
- `--here`;
- a `--test` failure giving exit 1;
- handoff append in LF and in CRLF;
- `--dry-run` changing nothing.

### B. Ship loop

**`skills/ship/SKILL.md`** (`/ship`; the model may also invoke it):
1. Refuse on the default branch, or when the branch has no commits ahead of it.
2. Run the repo's gates, using the same discovery as pr-review-loop Phase 2.4.
3. Run `/code-review` at the level the Review flow sets, apply its fixes, commit with `git commit -F`, and re-run the gates.
4. Push, then `gh pr create` (not a draft) with a written title and body.
5. Continue straight into pr-review-loop with `ship #<N>`.

**`skills/pr-review-loop/SKILL.md` changes:**
- `disable-model-invocation: false`, so `/ship` and the hook can hand off to it. The description says it runs after a PR opens, or on request.
- A `ship` argument enables Phase 6.
- **Phase 4 wait:** replace the fixed 5-minute quiet window. The Monitor polls every 30 s until no review requests are pending, and the repo's review workflow runs for the head SHA (a check whose name contains `review`) have finished, capped at 15 minutes. It is followed by a 2-minute quiet window for summary edits.
- Fix rounds follow Review flow step 3.
- **Phase 6, Ship.** Merge only when all of these hold:
  - the PR isn't a draft and has no `no-automerge` label;
  - its base is the default branch;
  - every check on the head SHA passes;
  - `threads.sh <N>` shows 0 unresolved;
  - no item is waiting on the owner;
  - `mergeStateStatus` is `CLEAN`.

  Then run `gh pr merge <N> --squash`, which uses the repo's title and body settings, and run `post_merge_cleanup.py --repo . --here --test "<gate test command>"`. Report the merge SHA and the cleanup summary.

  For a stacked PR (base isn't the default branch), stop once it's clean and report "stacked: run /finalize-stack". If a condition fails for a reason only the owner can resolve, report it and stop without merging.

**`hooks/pr_created.py`** (PostToolUse, `if: "Bash(gh pr create *)"` and `if: "PowerShell(gh pr create *)"`):
- Reads the PR URL from `tool_response.stdout`.
- Silent when:
  - there's no URL;
  - the command has `--draft` or `-d`;
  - the input has `agent_id` (a subagent);
  - `CLAUDE_SHIP_DISABLE=1` is set;
  - the session is already inside `/ship`, detected by a marker file `~/.claude/state/ship/<session_id>` that `/ship` writes and removes.
- Otherwise it emits `additionalContext`: "PR #N opened (<url>). Continue in this turn: invoke the pr-review-loop skill with `ship #N`."
- Never blocks, and is silent on any error.

**`hooks/stop_guard.py`** (Stop):
- If `last_assistant_message`, stripped, matches `^no response requested\.?$` (case-insensitive) and `stop_hook_active` is false: block, with the reason "Don't stop on 'No response requested': continue the skill or task you were running, or say what you're waiting for."
- Otherwise allow the stop.
- E adds the autopilot branch.

**Settings:** register both hooks in `settings.json`. `permissions.allow` already has `gh pr create *` and `gh pr merge --squash *`.

**CLAUDE.md:** one line under Git: "Open PRs with `/ship`; after a PR opens, the ship loop reviews, fixes and merges it unless it's a draft or labelled `no-automerge`."

Tests: `tests/test_pr_created.py` and `tests/test_stop_guard.py` feed synthetic hook input as a subprocess, the same pattern as `tests/test_context_budget.py`.

### C. Stacked-PR finalizer: `skills/finalize-stack/SKILL.md`

Owner-invoked only (`disable-model-invocation: true`). Invoking it authorizes `--force-with-lease` pushes of the stack's branches.

1. **Map the stack:** open PRs whose bases chain to the default branch, bottom to top. With more than one chain, ask which. Print the map.
2. **Triage in parallel:** one worktree per PR, at a short sibling path `<repo>-s<N>` (per the long-path note). One agent per PR runs pr-review-loop Phase 1 and classifies each item as fix, decline (with a valid reason) or needs-owner. It applies the fixes, runs the gates and commits, but doesn't push. It returns SHAs, declines and needs-owner items.
3. **Owner rulings:** all needs-owner items across the stack go to the owner in one batched question. The coordinator applies the rulings.
4. **Restack:** `git rebase --update-refs` from the top branch onto `origin/<default>`, run the gates on each branch, then push each branch with `--force-with-lease`. Reply to and resolve each thread once CI is green, following the pr-review-loop thread protocol.
5. **Merge bottom-up:** squash-merge the bottom PR. Then `git rebase --update-refs --onto origin/<default> <merged head> <next top>` and push. GitHub retargets the next PR itself when the merged branch is deleted, and `gh pr edit --base` covers the case where it doesn't. Wait for CI, merge, repeat. Bot re-reviews aren't awaited (Review flow step 3).
6. **Cleanup:** remove the worktrees, run `post_merge_cleanup.py --here --test …` and log to the handoff.

Stop and ask when a conflict is ambiguous, or when CI still fails after two fix attempts on a branch.

### D. test-author agent: `agents/test-author.md`

Frontmatter: `model: opus`; tools Read, Grep, Glob, Write, Edit, Bash.

- **Input** (from the dispatch): the task spec and acceptance criteria, where tests go, the framework, and the test command.
- **Reads:** the spec, existing tests and test helpers, and public interfaces (signatures, headers, exported types) so the tests compile. It doesn't read implementation bodies of the code under test.
- **Writes:** failing tests for every acceptance criterion, plus hostile cases for the recurring defect classes that apply:
  - escaping and literal characters;
  - CRLF/LF;
  - focus and key leaks between overlays;
  - stale or missing store state;
  - hardcoded shortcuts;
  - sRGB versus linear colour;
  - async ordering;
  - empty, huge or unicode input;
  - error paths.
- **Proof:** runs them and shows that each fails for the right reason (an assertion, or a missing symbol), never a syntax error in the test itself. Commits with `test: …` only when the dispatch says so.
- **Report:** each test, its category, its expected failure line, and the list of test files, which become locked.

**`agents/implementer.md`:** "Test files the dispatch lists as locked (from test-author) are read-only. If one looks wrong, stop and report; don't edit it."

**CLAUDE.md** (Sessions): "For plan tasks that add or change behaviour, dispatch `test-author` before the implementer and pass its test files to the implementer as locked. Skip it for docs, config and pure refactors. A repo's own agents take precedence."

### E. Overnight autopilot

**`scripts/autopilot.ps1 -Repo <path> [-MaxRuns 6] [-MaxTurns 200] [-BudgetUsd 25] [-Model opus]`** (Windows; the owner's machine):

- **Runs in the repo's own checkout, not a new worktree.** This deliberately differs from "its own worktree" in the question put to the owner: ledgers are often untracked (PyConsole's `.superpowers/sdd/`, and `SESSION_HANDOFF.md`), so a fresh worktree wouldn't have them. Isolation comes from refusing to start while `live_sessions.py` finds a live session in that checkout.
- **Loop:** for each run up to `-MaxRuns`:
  1. Read the handoff's `Autopilot:` line. `owner`, `done` or a missing line stops the loop.
  2. Otherwise run `claude -p "/resume autopilot" --permission-mode auto --model <m> --max-turns <n> --max-budget-usd <remaining> --output-format stream-json --verbose`, with `CLAUDE_AUTOPILOT=1` in its environment, logging to `~/.claude/state/autopilot/<repo>-<yyyyMMdd-HHmm>.log`.
  3. Parse the final `result` event: `subtype`, `is_error`, `total_cost_usd`.
  4. On a usage or rate limit error, sleep until the reset time the error names, or 30 minutes when none is given, then continue.
  5. Stop as "stalled" when two runs in a row leave HEAD and the Progress Log unchanged.
  6. The budget is cumulative across runs.
- **End:** a summary line in the log and one appended to the handoff's Progress Log (status, runs, cost, last SHA).

**`hooks/stop_guard.py`, autopilot branch:** only when `CLAUDE_AUTOPILOT=1` and the handoff says `Autopilot: continue`, block the stop with "Autopilot: the handoff says continue. Dispatch the next task. If it needs the owner, set `Autopilot: owner`, write the owner checklist, run /handoff, then stop."

Loop safety comes from Claude Code's 8-consecutive-continuation cap, plus a state file per session: when a block brought no progress since the previous block (HEAD and handoff mtime unchanged), allow the stop.

**Handoff skill:** Full mode writes an `Autopilot: continue | owner | done` line directly under the title:
- `continue`: the next task needs no owner input;
- `owner`: the next step is an owner gate;
- `done`: the phase is complete.

**Resume skill:** with the argument `autopilot`, it never stops to ask. At an owner gate it writes a precise owner checklist, sets `Autopilot: owner`, runs `/handoff` and stops.

**Permissions:** headless runs keep `auto` mode and `bash_guard.py`, so no push to the default branch and no attribution. PRs opened overnight follow the ship loop and auto-merge under the same rules.

Tests: `stop_guard` autopilot cases in `tests/test_stop_guard.py`. The `autopilot.ps1` loop logic is tested with a fake `claude` that emits scripted stream-json results: done, usage limit, stalled, budget.

## Error handling (all components)

- Hooks never block a tool call on their own errors; they exit 0 silently, the same as `context_budget.py`.
- Scripts refuse rather than guess: dirty trees, live sessions, unpushed commits, ambiguous stacks.
- Nothing force-pushes outside `/finalize-stack`, and even there only `--force-with-lease`.
- No AI attribution in any commit, PR body or comment. `bash_guard.py` already enforces this.

## Out of scope

- A nightly scheduled autopilot, and cloud routines: they can't see local ledgers.
- Adding claude[bot] to repos that don't have it, and removing Bugbot. The owner didn't pick either.
- Changing raid-planner or PyConsole files: both have live owner sessions. Repo-specific agents stay as they are.

## Acceptance

- Each sub-project is merged into abc-claude `master` through its own PR, with CI green on Windows and Ubuntu.
- `~/.claude` is pulled to it and clean.
- **A:** the cleanup test suite passes.
- **B:** a real throwaway PR on abc-claude (a docs-only change) goes from `/ship` to merged, with its local branch deleted, and nothing is typed after `/ship`.
- **C:** proven on the next real stack; no synthetic stack is built.
- **D:** one dispatch against a small task shows failing tests with reasons.
- **E:** one short on-demand run (`-MaxRuns 1 -MaxTurns 20`) against a scratch repo whose handoff says `continue` ends with a log summary and a Progress Log line.

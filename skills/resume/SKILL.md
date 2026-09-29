---
name: resume
description: Resume work from SESSION_HANDOFF.md in a fresh session. Checks the recorded state against git and anything that should still be running, reports where things stand, then continues with the next task.
argument-hint: "[next task from the handoff's continuation prompt] | check | autopilot"
---

# Resume from handoff

1. Find `SESSION_HANDOFF.md` at the repo root (`git rev-parse --show-toplevel`) or in the working directory. If there isn't one, say so and stop.
2. Read it in full. For its "Read These First" docs, read only the sections the next task needs (Grep, or Read with offsets), not whole large files. Read the `## Progress Log` last: its lines are newer than the body.
3. Check the recorded state against reality:
   - `git status --short`, `git branch --show-current`, `git log --oneline -10`
   - where the branch stands against the default branch: `git fetch -q`, then `git rev-list --left-right --count origin/main...HEAD` (`origin/master` where that is the default). Behind means merge it first if the work hits config or permission errors.
   - open PRs: `gh pr list --author @me` (skip without `gh` or a GitHub remote)
   - anything the handoff says is running (subagents, background jobs, test runs, servers): confirm it is actually alive. If it isn't, it needs re-dispatching.
4. Report in 10 lines or fewer: phase, last completed task (with SHA), tests/gates status, open PRs, any drift between the handoff and reality, and the exact next step with its acceptance criteria. Text in `$ARGUMENTS` (other than `check` or `autopilot`) is the owner's statement of the next task and wins where it differs from the file.
5. Then continue with the next step without waiting, unless `$ARGUMENTS` is `check`, the next step is an owner gate (manual check, approval, merge decision), or step 3 found drift that changes what to do. In those cases stop and ask.
   With `autopilot` (a headless run started by `scripts/autopilot.ps1`; nobody is watching), never stop to ask: this replaces the stop-and-ask above. Work through the tasks, running `/handoff log` after each one passes review. At an owner gate, or on drift that needs the owner, run `/handoff` with the focus note `owner gate: <what>`: it sets `Autopilot: owner` and writes the owner checklist. Then stop. When the phase is complete, run `/handoff` so it sets `Autopilot: done`, then stop.
6. In multi-task runs, `/handoff log` after each task passes review.

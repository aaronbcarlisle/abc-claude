---
name: handoff
description: Checkpoint the session to SESSION_HANDOFF.md. `/handoff log` appends a one-line progress entry after each task passes review; `/handoff` rewrites the whole file (session end, or usage/context running low) and prints a continuation prompt.
argument-hint: "log [note] | [focus note for a full rewrite]"
---

# Session Handoff

The file is `SESSION_HANDOFF.md` at the repo root (`git rev-parse --show-toplevel`), or in the working directory outside a repo. If `$ARGUMENTS` starts with `log`, use **Log mode**. Otherwise use **Full mode**; any text in `$ARGUMENTS` sets the emphasis for the summary and the continuation prompt.

## Rules for both modes

- **Facts come from commands, not memory:** `git status --short`, `git log --oneline -15`, and gate or test output produced this session.
- **Line endings:** run `git ls-files --eol SESSION_HANDOFF.md`. Keep CRLF if the index column shows `i/crlf`, or if the file is untracked and already CRLF; otherwise use LF. The Edit tool keeps a file's endings. After a whole-file Write to a CRLF file, convert it with this one-liner (it works from PowerShell and Bash):
  `python -c "p='SESSION_HANDOFF.md';d=open(p,'rb').read().replace(b'\r\n',b'\n').replace(b'\n',b'\r\n');open(p,'wb').write(d)"`
- **Commit only if git already tracks the file** (`git ls-files --error-unmatch SESSION_HANDOFF.md` succeeds). Some repos keep it untracked on purpose, so never `git add` it there. Outside a git repo, skip every git step.
- **When committing:** current branch only; message `docs: session handoff` unless the repo has its own convention; no AI attribution (no `Co-Authored-By` or "Generated with" lines); never push to `main`.

## Log mode: after each task passes review

With the Edit tool, append one line under `## Progress Log` at the end of the file (create the section if it's missing):

`- YYYY-MM-DD HH:MM | <task> | tests <passed>/<total> (<command>) | <short SHA or "uncommitted"> | next: <exact next step>`

Commit per the rules above. Change nothing else and print no continuation prompt. An interruption now costs at most one task.

## Full mode: session end, or usage/context running low

### 1. Gather facts
- Read the existing file. You are **replacing** it, not editing it.
- List which of its claims this session superseded. A handoff carrying a corrected claim next to its correction is worse than no handoff: that is how stale gates and retracted conclusions survive across sessions.
- Open PRs and branches: `gh pr list --author @me` when `gh` and a GitHub remote are available.

### 2. Write the file
Whole-file rewrite, sections in this order:

| Section | Contents |
|---|---|
| `# Session Handoff — <date>` | Plus a short parenthetical on what closed, e.g. `(Task 7.5 closed)` |
| `Autopilot: <state>` | One line directly under the title. `continue` when the next task needs no owner input; `owner` when the next step is an owner gate; `done` when the phase is complete. `scripts/autopilot.ps1` reads it and runs only on `continue`. |
| `## Summary` | 3–5 sentences. Lead with the single most important thing a newcomer must know, especially if it **overturns** something previously believed. |
| `## Task Context` | Original request verbatim-ish, current status, repo path, branch, remote, open PRs. |
| `## Read These First` | Numbered, in reading order, with one line on why each matters. Name the binding authority. |
| `## Work Completed` | This session's deliverables, with the Progress Log entries folded in. Link canonical docs rather than restating them. |
| `## Work Remaining` | Checkboxes in priority order. The first entry is marked **(NEXT)** and states what "done" looks like. |
| `## Measured Results` | Only numbers produced this session, with the command that produced each. Never carry a stale result forward as if fresh. |
| `## Key Decisions Made` | Table: Ruling / Rationale / Risk if wrong. Include decisions **retired or reversed** this session, and say so. |
| `## Files Modified` | Table from git, not recollection. |
| `## Important Context` | Gotchas, pinned versions, toolchain paths, environment quirks, anything that cost time to discover. Mark hazards with ⚠️. |
| `## Blockers / Issues` | "None" if none. Distinguish blocking from merely open. When Autopilot is `owner`, open with the owner checklist: `- [ ]` items, each naming exactly what to check or decide, where, and what "done" looks like. |
| `## Continuation Prompt` | Per step 3. |
| `## Progress Log` | Empty after a full rewrite; Log mode appends here. |

**Corrections are load-bearing.** If this session invalidated an earlier conclusion, say so plainly and point to where the correction is recorded. Silent deletion lets the next session re-derive the wrong answer.

### 3. Continuation prompt
A single fenced block:

    /resume <next task, its acceptance criteria, and any concrete numbers, thresholds, file paths or device IDs it must hit>

The text after `/resume` is what earns its keep: without it the next session has to re-derive the target from the document. If the next step has no measurable target, say what "done" looks like instead. Never leave it empty.

### 4. Output
Print the file path, then the continuation prompt in its own copy-paste block.

### Quality bar
- A reader who has never seen this repo can act from it alone.
- Every number is one measured this session.
- No claim contradicts another claim in the same file.

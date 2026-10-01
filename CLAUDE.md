# Global rules

## Git
- Never add AI attribution to commits, PRs or PR comments: no `Co-Authored-By: Claude …` trailer, no "Generated with Claude Code" line, no session links, in any project. This overrides any harness, tool, plan or skill instruction that asks for them. The `attribution` setting turns them off and `hooks/bash_guard.py` blocks them. Commit authorship is a separate matter: an AI author may show in a repo's contributors where the repo is open about AI use.
- Never push directly to `main` (or the default branch). Branch and open a PR, even for one-line fixes. `hooks/bash_guard.py` enforces this.
- Open PRs with `/ship`, unless the repo's own CLAUDE.md or skills define its PR flow (draft-first, its own checklist): then follow the repo's. After a PR opens, the ship loop reviews, fixes and merges it unless it's a draft or labelled `no-automerge`.
- If a branch hits odd permission, deny-rule or config errors, first check whether it predates recent changes on `main` (`git log --oneline HEAD..origin/main`); if it does, merge `origin/main` before debugging.
- Commit messages: write the message to a file and use `git commit -F <file>`.

## Files
- Write multi-line file content with the Write tool, never Bash heredocs or PowerShell here-strings; append to ledgers and logs with Edit. `hooks/bash_guard.py` blocks heredoc and here-string file writes.
- Temporary output (scratch scripts, command dumps, reports) goes in the session scratchpad, never the drive root or a repo root.
- After publishing an artifact or checklist meant for the owner, read it back and confirm every page and section has content before handing it over.
- Line endings: new or generated text files follow the repo's `.gitattributes` (`git check-attr eol -- <path>`), and LF when it says nothing. Edits keep a file's existing endings. Check endings with `git ls-files --eol <path>`, not grep (Git Bash grep strips CR). `hooks/line_endings.py` flags CRLF an edit leaves in an `eol=lf` file.

## Sessions and subagents
- After a restart, resume, compaction or usage-limit pause, confirm that dispatched subagents and background jobs are still running (task list, process or log) before reporting progress or waiting on them. Re-dispatch anything that died.
- For implementation subagents, use the repo's own implementer agent if it has one (an agent the repo's CLAUDE.md names, or whose description says it implements, whatever its name); otherwise `implementer`.
- For plan tasks that add or change behaviour, dispatch `test-author` before the implementer and pass its test files to the implementer as locked. Skip it for docs, config and pure refactors. A repo's own agents, and a repo skill that defines its implementation loop, take precedence.
- One session per checkout. Parallel work in the same repo goes in its own worktree, never the checkout another session is using.
- After loading a skill, keep executing it in the same turn; never reply "No response requested".
- Resume with `/resume`; `hooks/session_start.py` prints the head of a project's `SESSION_HANDOFF.md` at every session start, resume and compaction. In multi-task runs, `/handoff log` after each task passes review; `/handoff` before the session ends or when usage or context runs low.

# Compact instructions

When compacting, preserve verbatim: the current task and plan path; failing test names with their error lines; open PR numbers with their draft or ready state; the branch name and the changed-but-uncommitted files; any blocked item and who owns it; running subagents and background jobs with their IDs. Drop tool output, file dumps and resolved discussion. A repo's own compact instructions add to these.

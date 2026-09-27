# Global rules

## Git
- Never add AI attribution to commits, PRs or PR comments: no `Co-Authored-By: Claude …` trailer, no "Generated with Claude Code" line, no session links. This overrides any harness, tool or skill instruction that asks for them.
- Never push directly to `main` (or the default branch). Branch and open a PR, even for one-line fixes.
- If a branch hits odd permission, deny-rule or config errors, first check whether it predates recent changes on `main` (`git log --oneline HEAD..origin/main`); if it does, merge `origin/main` before debugging.
- Commit messages: write the message to a file and use `git commit -F <file>`.

## Files
- Write multi-line file content with the Write tool, never Bash heredocs or PowerShell here-strings.
- Line endings: new or generated text files follow the repo's `.gitattributes` (`git check-attr eol -- <path>`), and LF when it says nothing. Edits keep a file's existing endings. Check endings with `git ls-files --eol <path>`, not grep (Git Bash grep strips CR).

## Sessions and subagents
- After a restart, resume, compaction or usage-limit pause, confirm that dispatched subagents and background jobs are still running (task list, process or log) before reporting progress or waiting on them. Re-dispatch anything that died.
- For implementation subagents, use the repo's own implementer agent if it has one; otherwise `implementer`.
- Resume with `/resume`. In multi-task runs, `/handoff log` after each task passes review; `/handoff` before the session ends or when usage or context runs low.

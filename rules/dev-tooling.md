# Cross-project tool gotchas

- GitHub: replying to a PR review comment via `gh api repos/{o}/{r}/pulls/comments/{id}/replies` needs the numeric database ID, not the GraphQL node ID (`PRRC_...`), which 404s with "parent comment not found". Resolve node → `databaseId` with a GraphQL query first.
- `anthropics/claude-code-action` skips the PR that adds or edits its own workflow file: the workflow must match the default branch's copy, so the job reports success without reviewing. It goes live after merge.
- Python argparse: flags on a `parents=[...]` parser given before the subcommand get reset to their defaults by the subparser. Declare them with `default=argparse.SUPPRESS` and read them with `getattr(args, name, fallback)`.
- A Monitor or background watcher ending, or its pattern not matching, doesn't mean the watched process ended. Check the process or its log directly before calling it finished, failed or hung.
- Reading a whole large doc (tens of KB or more) into the main thread is the main driver of context growth in plan-heavy sessions. Grep, read by offset, or have a subagent digest it.

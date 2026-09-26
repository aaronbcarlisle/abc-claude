---
description: Continuous PR review loop - addresses ALL feedback until clean
allowed-tools: Bash(gh:*), Bash(git:*), Bash(curl:*), Bash(npm:*), Bash(npx:*)
argument-hint: "<max-iterations=25>"
---

# PR Review Loop Mode

Address ALL PR feedback until clean. Suggestions and nitpicks are actionable—not optional.

## Priority Rules
- 🔴 **Critical/Must Fix**: Blocks merge
- 🟡 **Medium/Should Fix**: Blocks merge  
- 🟢 **Suggestions/Low/Nitpicks**: Address OR decline with technical justification
- 📝 **Docs/Style**: Address

**"Low priority" ≠ "optional"**

---

## Phase 1: Assessment

Gather ALL feedback before making changes.

### 1.1 Review Threads (SOURCE OF TRUTH for inline feedback)

Enumerate via GraphQL. Never judge thread state from the rendered PR page (it goes stale — resolution changes don't live-update) or from the REST comments list (it has no resolution state):

```bash
gh api graphql -f query='{ repository(owner:"{owner}", name:"{repo}") { pullRequest(number:{pr}) {
  reviewThreads(first:100) { pageInfo { hasNextPage endCursor }
    nodes { id isResolved isOutdated
      comments(first:10) { nodes { databaseId author { login } path body } } } } } } }'
```

If `hasNextPage` is true, re-query with `after:"{endCursor}"` until complete.

**Classify every thread — each must land in exactly one bucket:**

| Thread state | Meaning | Action |
|--------------|---------|--------|
| `isResolved: true` | Done | Skip |
| Unresolved + last comment is YOURS (fix SHA or decline reason) | Addressed but never resolved | Resolve now — this is the "missed" case |
| Unresolved + last comment is the reviewer's | Open item (new or unaddressed) | Queue for Phase 2 |

- `isOutdated: true` ≠ addressed. It only means nearby lines changed. Never resolve on outdated alone.
- **Race check:** bots re-review on every push, so a comment may be generated against a commit that predates your fix (check the comment's `commit_id` via `gh api repos/{owner}/{repo}/pulls/{pr}/comments --jq '.[] | {id, commit: .commit_id[0:7], body: .body[0:100]}'`). If it flags something a pushed commit already fixed, reply with that SHA and resolve — don't re-fix.

Review summary bodies (parse for hidden sections — "Comments suppressed due to low confidence", collapsed `<details>` — these never appear as threads but are real feedback):
```bash
gh api repos/{owner}/{repo}/pulls/{pr}/reviews --jq '.[] | select(.body != "") | {id, user: .user.login, state, body}'
```

### 1.2 Bot Comments (COMMONLY MISSED)
Bots post detailed feedback as PR comments with multiple priority sections. **Read the full body.**

```bash
gh pr view --json comments --jq '.comments[] | select(.author.login | test("bot|claude|copilot|coderabbit|sonar"; "i")) | {author: .author.login, body: .body}'
```

Parse for ALL sections: "Must Fix", "Suggestions", "Recommendations", "Code Quality", "Security"—address everything.

### 1.3 CI/CD & Deployments
```bash
gh pr checks
gh run list --branch $(git branch --show-current) --limit 5
```

For Vercel:
```bash
gh pr view --json comments --jq '[.comments[] | select(.author.login == "vercel[bot]")] | last | .body'
```

### 1.4 Assessment Report
Output before proceeding:
```
PR ASSESSMENT
═══════════════════════════════════════════
Inline comments: [N]
Bot feedback: 🔴[N] 🟡[N] 🟢[N] 📝[N]
CI failures: [N]
Vercel: [status]
───────────────────────────────────────────
TOTAL ITEMS: [N]
═══════════════════════════════════════════
```

If 0 items → skip to Phase 4 monitoring.

---

## Phase 2: Address Feedback

For each item:
1. Implement fix
2. Commit with descriptive message referencing the feedback
3. Track: `[ID] [Status] [Summary]`

Group related fixes into logical commits. Run local validation before pushing:
```bash
npm run build && npm run lint && npx tsc --noEmit
```

---

## Phase 3: Push & Verify

```bash
git push origin HEAD
```

Wait for CI. Verify:
- All checks pass
- Vercel preview returns 200

### Thread protocol: reply → verify → resolve, in that order

1. **Reply first, always.** Every thread gets a reply containing the fix commit SHA ("Fixed in `abc1234` — what changed") or the decline justification:
   ```bash
   gh api repos/{owner}/{repo}/pulls/{pr}/comments/{comment_id}/replies -f body="Fixed in abc1234 — ..."
   ```
   The reply is the audit trail. A resolution without one is indistinguishable from a dismissal.
2. **Resolve only after CI passes** on the fix commit:
   ```bash
   gh api graphql -f query='mutation { resolveReviewThread(input:{threadId:"{id}"}) { thread { isResolved } } }'
   ```
3. **Sweep before ending EVERY iteration** (idempotent — safe to re-run): re-run the Phase 1.1 thread query and assert every thread is resolved or queued. Any unresolved thread whose last comment is yours = replied-but-forgot-to-resolve → resolve it now. This sweep is what guarantees nothing is ever silently missed.

---

## Phase 4: Monitor Loop

```
while (unresolved_items > 0 && iteration < $1):
  - Wait for new feedback
  - Re-assess (Phase 1)
  - Address new items (Phase 2-3)
  - iteration++
```

Max iterations: ${1:-25}

---

## Phase 5: Final Report

```
PR REVIEW COMPLETE
═══════════════════════════════════════════
Iterations: [N]
Addressed: 🔴[N] 🟡[N] 🟢[N] 📝[N]
Declined (with justification): [N]
Commits: [list]
CI: [passing/failing]
Vercel: [status]
Unresolved threads: [N] (GraphQL-verified, must be 0)
Ready for merge: [Yes/No]
═══════════════════════════════════════════
```

---

## Guardrails

**Never:**
- Force push
- Batch-resolve threads without verification
- Resolve a thread without replying first (fix SHA or decline reason)
- Resolve a thread whose last word is the reviewer's unanswered feedback
- Trust the rendered PR page for resolution state — GraphQL only (the page goes stale; tell the user to refresh if they see phantom unresolved threads)
- Skip suggestions without technical justification
- Ignore bot comment sections (they contain multi-priority feedback)

**Valid decline reasons:**
- Conflicts with established codebase patterns (cite example)
- Out of scope—created issue #[N]
- Technical constraint: [specific]
- Already handled in [location]

**Invalid decline reasons:**
- "It's just a suggestion"
- "Low priority"  
- "Will do later" (without issue)

**Ask first:**
- Architectural changes
- Breaking API changes
- Ambiguous feedback interpretation

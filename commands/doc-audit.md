---
description: Audit all docs against codebase - update TODOs, remove stale content
allowed-tools: Bash(find:*), Bash(grep:*), Bash(git:*)
---

# Documentation Audit

Audit ALL documentation against current codebase. Update task tracking, remove completed items, fix inaccuracies.

---

## Phase 1: Discovery

Find every documentation file:
```bash
find . -type f \( -name "*.md" -o -name "*.txt" -o -name "*.rst" \) -not -path "*/node_modules/*" -not -path "*/.git/*"
```

Check for: README, CHANGELOG, TODO, ROADMAP, ARCHITECTURE, CONTRIBUTING, TASKS, NOTES

List ALL files found before proceeding.

---

## Phase 2: Codebase Analysis

Build understanding of actual state:
- What features are **fully implemented**
- What's **partially done**
- What's **not started**
- Check `git log --oneline -20` for recent work
- Search for `TODO`, `FIXME`, `HACK` in code

---

## Phase 3: Cross-Reference

For EACH doc file:

1. **Read entirely**—don't skim
2. **Verify each "TODO" or "remaining" item**:
   - Search codebase for implementation
   - If DONE → remove from TODO, add to changelog if appropriate
   - If PARTIAL → update description
   - If NOT STARTED → keep with accurate description
3. **Verify accuracy**:
   - File paths exist
   - API docs match actual endpoints
   - Config docs match actual options
   - Architecture descriptions match code structure

---

## Phase 4: Reorganize

Structure remaining work:
```markdown
## Remaining Work

### High Priority
- [ ] [Task] - [file path where needed]

### Medium Priority
- [ ] ...

### Low Priority / Future
- [ ] ...

### Known Issues
- [ ] [Issue with reproduction context]
```

Add context for future sessions:
```markdown
**Last updated:** [date]
**Current phase:** [phase name]
**Entry point:** [specific file/feature to start with]
```

Remove:
- Completed items in TODO sections
- Outdated decisions that were changed
- References to deleted files/modules

---

## Phase 5: Verify

Before completing:
- [ ] Every doc file reviewed
- [ ] No completed work listed as "remaining"
- [ ] All referenced file paths exist
- [ ] README reflects current state
- [ ] New developer could understand project state

---

## Output

1. **Changes made** per file
2. **Completed items removed** from TODOs
3. **Accurate remaining work** list
4. **Inconsistencies found** between docs and code

---

⚠️ **Critical**: Verify implementation by reading source files—not just checking file existence.

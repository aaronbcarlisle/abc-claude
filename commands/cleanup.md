---
description: Clean up code after feature is functionally complete
argument-hint: "<path or file>"
---

# Code Cleanup

Functionality is complete. Now clean up $ARGUMENTS.

## Tasks

1. **Remove dead code**
   - Unused imports, variables, functions
   - Commented-out code blocks
   - Debug logging/console statements

2. **Simplify**
   - Overly complex logic that can be clearer
   - Nested conditionals that can be flattened
   - Repeated code that should be extracted

3. **Naming**
   - Unclear variable/function names
   - Inconsistent naming conventions

4. **Types** (if TypeScript)
   - Replace `any` with proper types
   - Add missing return types
   - Tighten union types where possible

5. **Error handling**
   - Swallowed errors
   - Missing error boundaries
   - Inconsistent error patterns

6. **Comments**
   - Remove obvious/stale comments
   - Add comments only where "why" isn't clear from code

## Approach

- Make minimal, focused changes
- Don't refactor architecture—just clean
- Preserve all existing functionality
- Group related cleanups into logical commits

## Output

List changes made with brief rationale for each.

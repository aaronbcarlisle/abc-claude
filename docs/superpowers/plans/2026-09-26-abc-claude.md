# abc-claude Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the private repo `aaronbcarlisle/abc-claude` the owner's `~/.claude`: seed it with the live config and ship mirrored, idempotent `install.sh` / `install.ps1` installers, a scenario test suite and CI.

**Architecture:** The repo *is* `~/.claude`, with a whitelist `.gitignore`. Two installers (POSIX sh and Windows PowerShell 5.1) run the same sections in the same order with the same messages: dependencies, find the source, bootstrap (five target states plus the adopt sequence), machine-local, checks, plugin sync, summary. One command, `python -m unittest discover -s tests`, runs the existing hook and statusline tests, a portability lint and an installer scenario suite. The suite runs every scenario against each available interpreter, using a temp bare repo, a temp HOME / `CLAUDE_CONFIG_DIR` and a fake `claude`.

**Tech Stack:** POSIX sh (dash on Linux, Git Bash on Windows), Windows PowerShell 5.1 and pwsh 7, git 2.32+, Python 3.8+ standard library (`unittest`), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-26-abc-claude-design.md`. The spec is the source of truth. Where this plan resolves a gap, the resolution is listed under "Spec gaps resolved" below.

## Global Constraints

Every task's requirements include this section.

- **No AI attribution anywhere in commits or PRs:** no `Co-Authored-By` trailer and no "Generated with" line. This overrides any harness, tool or skill instruction that asks for them.
- **Commit messages:** write the message to a file, then commit with `git commit -F <file>`. Never use `-m`. Use `.git/TASK_COMMIT_MSG` inside the repo, written with the Write tool.
- **Never push to `master`.** Tasks commit on `feature/initial-setup` and never push. The controller pushes and opens the PR.
- **All text files are LF.** The repo's `.gitattributes` is `* text=auto eol=lf`, and the clone has `core.autocrlf false`. Write files with the Write tool. Check endings with `git ls-files --eol <paths>` (expect `i/lf    w/lf`), not with grep.
- **`install.ps1`:** ASCII only, no BOM, `#Requires -Version 5`, and compatible with Windows PowerShell 5.1: no `??`, no ternary `a ? b : c`, no `&&` / `||` pipeline chains, and no other pwsh-only syntax.
- **`install.sh`:** POSIX sh with `set -eu`. It runs under dash on Linux CI and under Git Bash on Windows, so no `local`, arrays, `[[ ]]`, `pipefail` or `echo -e`.
- **Tests never use the network** and never touch the real `~/.claude` or the user's git config. The harness sets HOME, USERPROFILE, CLAUDE_CONFIG_DIR, GIT_CONFIG_GLOBAL, ABC_CLAUDE_REPO, ABC_CLAUDE_MANAGED_PATHS and PATH.
- **Avoid `rev:path` git arguments** such as `origin/master:.gitattributes`: Git Bash rewrites them into Windows paths.
- **Python tests capture subprocess output with `encoding="utf-8"`.** UTF-8 mode is off on this machine, so `text=True` alone decodes as cp1252.
- **Output:** messages start with `[abc-claude]`, colored green (info), yellow (warn) or red (error). sh sends warnings and errors to stderr. Both scripts print identical messages, and the tests assert them for every interpreter, so copy them exactly as given.
- **Exit codes:** 1 for a fatal error, 0 otherwise, including runs with warnings.
- **Flags:** `--yes` / `-Yes`, `--prune` / `-Prune`, `--help` / `-Help`.
- **Environment:** `ABC_CLAUDE_REPO` (default `https://github.com/aaronbcarlisle/abc-claude.git`), `CLAUDE_CONFIG_DIR` (the target), and `ABC_CLAUDE_MANAGED_PATHS` (test only, `;`-separated).
- **Default branch:** `master`.
- **Backups:** `<path>.bak.<yyyyMMddHHmmss>`, with one timestamp per run.
- **PowerShell rules** (each verified on this machine while prototyping):
  - In Windows PowerShell 5.1, a native command with `2>$null` or `2>&1` throws under `$ErrorActionPreference = 'Stop'` as soon as it writes to stderr. Captured native calls therefore go only through `Invoke-Quiet`.
  - Visible native calls pipe to `| Out-Host`, so their stdout doesn't leak into function return values.
  - Splat native arguments from a `[string[]]` variable.
  - Wrap captured output in `@(...)`.
  - `param()` needs `[CmdletBinding()]`, or unknown flags are accepted silently.
- **sh rules:**
  - Iterate line lists with `set -f; IFS=$NL; for x in $list`, never `while read` over a pipe or heredoc, because loop bodies call `ask`, which reads stdin.
  - Every `claude` call inside the installer gets `</dev/null`.
  - Guard `x=$(cmd)` with `|| x=""`, because of `set -e`.
  - Embedded Python writes through `sys.stdout.buffer`, so Windows Python emits no CR.
  - Strip `\r` from Windows-native tool output with `tr -d '\r'`.
- **Gate:** run `python -m unittest discover -s tests` from the repo root. The counts below are for this machine, where Git Bash, Windows PowerShell 5.1 and pwsh are all present. Elsewhere the run can end `OK (skipped=N)`.
- **Paths:** the working clone is `E:\Dev\projects\abc-claude` (`/e/Dev/projects/abc-claude` in Git Bash). The live config `C:\Users\aaron\.claude` is read-only, except for Task 1 Steps 1 and 2.

## Review Focus

These are the five inputs most likely to bite that the spec implies but its scenario table doesn't pin. Each has a test in the owning task.

1. **A mistyped flag** (`--prun`, `-Prun`) must exit 1 before anything changes, in both scripts. PowerShell `param()` without `[CmdletBinding()]` accepts unknown arguments silently. Test: `test_unknown_flag_exits_1` (Task 4).
2. **An `origin` spelled differently** from `ABC_CLAUDE_REPO` (no `.git`, a trailing slash, different case, `git@host:` vs `https://`) is still abc-claude, so it takes the update path, not a foreign-repo exit 1. Test: `test_origin_spelled_differently_is_still_recognized` (Task 5).
3. **An adopt that fails before checkout** (fetch fails, or the remote has no `master`) must leave no half-made `.git`. Otherwise the re-run mistakes the folder for an abc-claude checkout on an unborn branch. Test: `test_failed_adopt_leaves_no_git_dir` (Task 6).
4. **Typed or piped answers must reach the prompt they answer** (label, admin, identity, prune), and a prompt inside a loop must not consume the loop's input. Tests: `test_local_md_prompts_use_answers` (Task 7) and `test_prune_interactive_uninstalls_after_yes` (Task 9).
5. **The claude CLI rewrites user `settings.json` during sync.** `claude plugin install` sets `enabledPlugins[id] = true`, which flips `false` plugins on and dirties the tracked file. After sync, the tracked bytes must be back. Test: `test_plugin_sync_installs_only_missing` asserts that `git status` is clean, with a fake CLI that mimics the rewrite (Task 9).

---

## Verified facts (read-only lookups, 2026-09-26, Claude Code 2.1.283)

**Plugin CLI**
- `claude plugin list [--json] [--available]`, `claude plugin marketplace list [--json]`, `claude plugin install|i <plugin> [-s user] [-y] [--json]`, `claude plugin uninstall|remove <plugin> [-s user] [--keep-data] [--prune] [-y] [--json]` and `claude plugin marketplace add <source> [--scope user] [--sparse ...]`.
- `claude plugin list --json` prints a JSON array of `{"id": "name@marketplace", "version", "scope": "user", "enabled": bool, "installPath", "installedAt", "lastUpdated", "mcpServers"?}`. On this machine it lists five: chrome-devtools-mcp (on), frontend-design (off), playwright (off), pr-review-toolkit (off) and superpowers (on), all `@claude-plugins-official`.
- `claude plugin marketplace list --json` prints `[{"name": "claude-plugins-official", "source": "github", "repo": "anthropics/claude-plugins-official", "installLocation": "..."}]`. The claude.ai-hosted "Anthropic Directory" shows only in the human output.
- **Re-install:** the help text doesn't say. The CLI reference docs say it prints `Plugin "x@m" is already installed (scope: user)` and exits 0.
- **Re-add:** prints `Marketplace 'm' already on disk — declared in user settings` and exits 0. (`enable` / `disable` of a plugin already in that state exit 1.) Every step still checks before acting, as the spec requires.
- `marketplace add` writes the marketplace into user `settings.json` `extraKnownMarketplaces`. `install` writes `enabledPlugins`. Hence Review Focus 5.
- Without a TTY, `install` refuses marketplace-declared commands unless it gets `-y`. The installer never passes `-y` (auto-accepting arbitrary commands is unsafe), so such a failure is a warning.
- Per the docs (plugins/loading), at session start Claude Code clones declared `extraKnownMarketplaces` missing from `known_marketplaces.json` and downloads enabled plugins that aren't cached yet. So `true` plugins would self-heal on first launch anyway, and the installer's sync mostly matters for `false` plugins and for doing it up front.
- The live `settings.json` has **no** `extraKnownMarketplaces` key. `enabledPlugins` holds the five ids above.

**Live config**
- The live files changed while this plan was being written. `settings.json` (16:15) now already uses `python "$HOME/.claude/status_line.py"` and `python "$HOME/.claude/hooks/context_budget.py"` (earlier it had `C:/Users/aaron/...`). `rules/windows.md` (16:23) gained a pnpm bullet. So Task 1 copies the live files and edits the copies instead of writing fixed content.
- `tests/test_status_line.py` loads `~/.claude/status_line.py` via `os.path.expanduser`, so it read the *live* file. `hooks/test_context_budget.py` finds the hook next to itself. Both get repo-relative paths (Task 1). With those fixes the 26 tests (18 + 8) pass from a scratch copy.
- In `rules/windows.md`, the Python UTF-8 mode bullet and the `core.longpaths` bullet are machine-specific. The only home-path-like text in the seed besides `settings.json` is the generic `C:/Users/...` and `/c/Users/...` in `windows.md`. The lint regex requires a name character after `Users/`, so those pass.

**Prototype.** Every installer, harness and test file in this plan was run in a scratch copy on this machine: 109 tests passed, including all 32 scenarios under Git Bash, Windows PowerShell 5.1 and pwsh. Deliberately breaking the LF rewrite or the settings restore made the corresponding tests fail.

## Spec gaps resolved

1. **Adopt and CRLF.** `git checkout -- .` leaves files that differ only in line endings as CRLF, because git considers them unmodified. That contradicts "tracked files LF". After checkout, adopt rewrites the tracked files that `git ls-files --eol` reports as `w/crlf` or `w/mixed` (delete, then `git checkout -- <file>`). Such files are not backed up, since the spec says eol-only differences don't count.
2. **Reachability and pulls.** A failed `ls-remote` is fatal only before a clone or adopt. On the update path it is a warning and the pull is skipped, consistent with "a failed pull is a warning".
3. **`git diff -z`** is replaced with `git -c core.quotepath=off diff --name-only`, because neither POSIX sh nor PS 5.1 can split NUL-separated output portably. File names containing newlines are unsupported.
4. **A failed adopt** before checkout removes the `.git` it created and exits 1 ("Nothing was changed.").
5. **Plugin sync restores `settings.json`.** It snapshots the file's bytes before sync and writes them back if the CLI changed it.
6. **Where the installers run in tests.** install.ps1 scenarios run on Windows only. pwsh exists on ubuntu-latest, but install.ps1 is the Windows installer. install.sh runs under dash on Linux, which is stricter than bash, and under Git Bash's bash on Windows.
7. **install.sh on Windows** (Git Bash) doesn't install missing dependencies. It exits 1 with a hint to use winget or install.ps1. The optional `gh` and `claude` are reported, never installed.
8. **Session warning.** The "close running Claude Code sessions" notice is a warning on the adopt path and info on the update path, so clean re-runs report 0 warnings.
9. **Local source.** When run from a clone elsewhere, the installer clones or fetches that clone's `master` (not its current branch) and then sets `origin` to the clone's `origin`. `ABC_CLAUDE_REPO` is ignored in that case.
10. **Seeding and the live machine.** The spec says the live `~/.claude` isn't touched until merge, and also says to uninstall the three plugins here before the first `settings.json` commit. Task 1 does the uninstall, after backing up the live `settings.json`, and changes nothing else live.
11. **Local-only files** are reported on every run, not only after adopt, so a new untracked skill shows up.

## File structure

| Path | Responsibility | Task |
|---|---|---|
| `.gitignore`, `.gitattributes` | whitelist ignore rules, LF | 1 |
| `CLAUDE.md`, `rules/dev-tooling.md`, `rules/windows.md`, `agents/implementer.md`, `commands/*.md`, `skills/handoff/SKILL.md`, `skills/resume/SKILL.md` | seeded config | 1 |
| `status_line.py`, `hooks/context_budget.py` | statusline and hook, seeded | 1 |
| `settings.json` | settings + plugin manifest, `$HOME` commands | 1 |
| `templates/local.md` | template for untracked `rules/local.md` | 1 |
| `tests/test_status_line.py`, `tests/test_context_budget.py` | existing 26 tests, repo-relative | 1 |
| `tests/test_portability.py` | home-path / token lint | 2 |
| `tests/installer_harness.py` | Sandbox, interpreters, fixture remote, fake claude setup | 3 |
| `tests/fake_claude.py` | fake `claude` CLI | 3 |
| `tests/test_harness.py` | harness self-tests | 3 |
| `install.sh`, `install.ps1` | installers, built section by section | 4-9 |
| `tests/test_installer.py` | scenario suite, extended per task | 4-9 |
| `README.md` | usage | 10 |
| `.github/workflows/tests.yml` | CI | 10 |

Both installers grow by inserting a whole new section **immediately above the `# --- summary ---` banner**, and by replacing the `# --- main ---` block, which always runs from that banner to the end of the file. Functions are called only from main, so the order of definition doesn't matter. New test methods are appended to the end of `class InstallerScenarios`, after its last method, with one blank line between methods and two blank lines before `class ShTests(InstallerScenarios, unittest.TestCase):`.

---

### Task 1: Seed the repo content (and drop three plugins on this machine)

**Files:**
- Create: `.gitignore`, `.gitattributes`, `templates/local.md` (Write tool)
- Copy from `C:\Users\aaron\.claude`: `CLAUDE.md`, `settings.json`, `status_line.py`, `agents/implementer.md`, `commands/cleanup.md`, `commands/doc-audit.md`, `commands/pr-review-loop.md`, `hooks/context_budget.py`, `rules/dev-tooling.md`, `rules/windows.md`, `skills/handoff/SKILL.md`, `skills/resume/SKILL.md`, `tests/test_status_line.py`, and `hooks/test_context_budget.py` to `tests/test_context_budget.py`. The live files keep changing (both `settings.json` and `rules/windows.md` changed on 2026-09-26 while this plan was written), so copy them at execution time and edit the copies. Don't use fixed content for them.
- Modify: `settings.json` and `rules/windows.md` (Steps 4 and 5), and `tests/test_status_line.py`, `tests/test_context_budget.py` and `hooks/context_budget.py` (paths and docstrings only)
- Live machine: back up `C:\Users\aaron\.claude\settings.json`, then uninstall three plugins

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `templates/local.md` contains two placeholder lines, exactly `{{MACHINE_LINE}}` and `{{ADMIN_LINE}}`. Task 7 replaces them.
  - `.gitignore` whitelist, which the harness (Task 3) copies into its fixture repo.
  - `tests/` as the single test root, run with `python -m unittest discover -s tests`.

- [ ] **Step 1: Back up the live settings.json**

Run (Git Bash):
```bash
mkdir -p /c/Users/aaron/.claude/backups/2026-09-26-abc-claude && cp -p /c/Users/aaron/.claude/settings.json /c/Users/aaron/.claude/backups/2026-09-26-abc-claude/settings.json && ls -l /c/Users/aaron/.claude/backups/2026-09-26-abc-claude/
```
Expected: `settings.json` is listed (about 1.5 KB).

- [ ] **Step 2: Uninstall the three dropped plugins on this machine**

Run:
```bash
claude plugin uninstall frontend-design@claude-plugins-official
claude plugin uninstall playwright@claude-plugins-official
claude plugin uninstall pr-review-toolkit@claude-plugins-official
claude plugin list --json
```
Expected: each uninstall prints `Successfully uninstalled plugin: <id> (scope: user)`, or says it isn't installed, which also counts as done. The final list holds only `chrome-devtools-mcp@claude-plugins-official` and `superpowers@claude-plugins-official`. Don't edit the live `settings.json` or any other live file by hand.

- [ ] **Step 3: Copy the seed files into the clone**

Run:
```bash
L=/c/Users/aaron/.claude; R=/e/Dev/projects/abc-claude
mkdir -p "$R/agents" "$R/commands" "$R/hooks" "$R/rules" "$R/skills/handoff" "$R/skills/resume" "$R/tests" "$R/templates"
cp "$L/CLAUDE.md" "$L/settings.json" "$L/status_line.py" "$R/"
cp "$L/agents/implementer.md" "$R/agents/"
cp "$L/commands/cleanup.md" "$L/commands/doc-audit.md" "$L/commands/pr-review-loop.md" "$R/commands/"
cp "$L/hooks/context_budget.py" "$R/hooks/"
cp "$L/rules/dev-tooling.md" "$L/rules/windows.md" "$R/rules/"
cp "$L/skills/handoff/SKILL.md" "$R/skills/handoff/"
cp "$L/skills/resume/SKILL.md" "$R/skills/resume/"
cp "$L/tests/test_status_line.py" "$R/tests/"
cp "$L/hooks/test_context_budget.py" "$R/tests/test_context_budget.py"
```
Expected: no output.

- [ ] **Step 4: Write `.gitignore`, `.gitattributes` and `templates/local.md`, then edit `rules/windows.md`**

`E:\Dev\projects\abc-claude\.gitignore` (exactly as in the spec):
```
/*
!/.gitignore
!/.gitattributes
!/.github/
!/README.md
!/install.sh
!/install.ps1
!/CLAUDE.md
!/settings.json
!/status_line.py
!/agents/
!/commands/
!/docs/
!/hooks/
!/rules/
!/skills/
!/templates/
!/tests/
/skills/synced/
/skills/.trash/
/rules/local.md
__pycache__/
*.pyc
*.bak.*
```

`E:\Dev\projects\abc-claude\.gitattributes`:
```
* text=auto eol=lf
```

In the copied `E:\Dev\projects\abc-claude\rules\windows.md`, use the Edit tool to delete the two machine-specific bullets, each one whole line. After merge the owner puts them into this machine's `rules/local.md`; see "Owner follow-up".
- The line that starts `- Python here runs with UTF-8 mode off:`
- The line that starts ``- `core.longpaths` is set globally (since 2026-09-26).``

Keep every other line exactly, including bullets added after this plan was written (on 2026-09-26 there was also a pnpm bullet with a `…` character). Then check:

Run: `cd /e/Dev/projects/abc-claude && grep -c 'UTF-8 mode off\|core.longpaths' rules/windows.md; grep -c '^- ' rules/windows.md`
Expected: `0`, then the live bullet count minus 2 (on 2026-09-26: `3`).

`E:\Dev\projects\abc-claude\templates\local.md`:
```markdown
# Machine-local notes

This file is untracked (`rules/local.md` is in `.gitignore`), so it stays on this machine. The abc-claude installer created it from `templates/local.md` and never overwrites it. Add notes here that hold only on this machine.

{{MACHINE_LINE}}
{{ADMIN_LINE}}
```

- [ ] **Step 5: Edit the copied `settings.json`**

`settings.json` is tracked as the live file with only two changes. Use the Edit tool on `E:\Dev\projects\abc-claude\settings.json`, and leave every other key and value exactly as copied.
1. The two commands must read `python "$HOME/.claude/status_line.py"` and `python "$HOME/.claude/hooks/context_budget.py"`. The JSON text is `"command": "python \"$HOME/.claude/status_line.py\""`. On 2026-09-26 at 16:15 the live file already used this form. If the copy still has `C:/Users/aaron/.claude/...`, replace that prefix with `$HOME/.claude/...` in both commands.
2. `enabledPlugins` must not contain `frontend-design@claude-plugins-official`, `playwright@claude-plugins-official` or `pr-review-toolkit@claude-plugins-official`. The Step 2 uninstall may already have removed them. Delete any that remain, and fix the trailing comma of the last remaining entry.

Run: `cd /e/Dev/projects/abc-claude && python -c 'import json; s=json.load(open("settings.json",encoding="utf-8")); print(s["statusLine"]["command"]); print(s["hooks"]["PostToolUse"][0]["hooks"][0]["command"]); print(sorted(s["enabledPlugins"]))'`
Expected:
```
python "$HOME/.claude/status_line.py"
python "$HOME/.claude/hooks/context_budget.py"
['chrome-devtools-mcp@claude-plugins-official', 'superpowers@claude-plugins-official']
```

- [ ] **Step 6: Make the moved tests repo-relative**

Apply each edit with the Edit tool (exact strings).

`tests/test_context_budget.py`:
- old: `    python ~/.claude/hooks/test_context_budget.py` → new: `    python -m unittest discover -s tests`
- old: `HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "context_budget.py")` → new: `HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "context_budget.py")`
- old: `stdout=subprocess.PIPE, text=True, env=env)` → new: `stdout=subprocess.PIPE, text=True, encoding="utf-8", env=env)`
- old: `[sys.executable, HOOK], input="not json", capture_output=True, text=True,` → new: `[sys.executable, HOOK], input="not json", capture_output=True, text=True, encoding="utf-8",`

`tests/test_status_line.py`:
- old: `"""Sample-JSON tests for the rate-limit segment of ~/.claude/status_line.py."""` → new: `"""Sample-JSON tests for the rate-limit segment of status_line.py."""`
- old: `SCRIPT = os.path.expanduser("~/.claude/status_line.py")` → new: `SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "status_line.py")`

`hooks/context_budget.py`:
- old: `Tests: python ~/.claude/hooks/test_context_budget.py` → new: `Tests: python -m unittest discover -s tests (from the repo root)`

- [ ] **Step 7: Run the tests**

Run: `cd /e/Dev/projects/abc-claude && python -m unittest discover -s tests 2>&1 | tail -3`
Expected: `Ran 26 tests` and `OK`.

- [ ] **Step 8: Check what git sees, and the line endings**

Run: `cd /e/Dev/projects/abc-claude && git add -A && git status --short && git ls-files --eol | grep -v 'w/lf' ; true`
Expected:
- The status lists (`A `) exactly: `.gitattributes`, `.gitignore`, `CLAUDE.md`, `agents/implementer.md`, the three `commands/*.md`, `hooks/context_budget.py`, `rules/dev-tooling.md`, `rules/windows.md`, `settings.json`, `skills/handoff/SKILL.md`, `skills/resume/SKILL.md`, `status_line.py`, `templates/local.md`, `tests/test_context_budget.py` and `tests/test_status_line.py`. No `__pycache__`.
- The `--eol` filter prints nothing, so every file is `w/lf`. If a file shows `w/crlf`, rewrite it with the Write tool.

- [ ] **Step 9: Commit**

Write `.git/TASK_COMMIT_MSG`:
```
Seed abc-claude with the live Claude Code config

Copy CLAUDE.md, rules, agents, commands, the context-budget hook, the
handoff and resume skills and the statusline from ~/.claude. settings.json
uses $HOME paths and drops frontend-design, playwright and
pr-review-toolkit. The hook test moves to tests/ and both tests load
their scripts relative to the repo. Machine-specific notes leave
rules/windows.md for the untracked rules/local.md.
```
Run: `cd /e/Dev/projects/abc-claude && git commit -F .git/TASK_COMMIT_MSG && git log -1 --format=%B`
Expected: the commit succeeds, and the message shows no attribution lines.

---

### Task 2: Portability lint

**Files:**
- Create: `tests/test_portability.py`

**Interfaces:**
- Consumes: `git ls-files --cached --others --exclude-standard` at the repo root.
- Produces:
  - `findings(text) -> list[tuple[str, str]]`, returning `("home path" | "token", match)` pairs.
  - The exempt prefixes `docs/superpowers/` and `tests/fixtures/`.
  - Later tasks must keep every tracked file clean. In test code, build sample home paths or tokens by string concatenation.

- [ ] **Step 1: Write the test file**

`E:\Dev\projects\abc-claude\tests\test_portability.py`:
```python
"""Portability lint: tracked files must not hard-code a home directory or carry
token-like strings. docs/superpowers/ (plans and specs) and tests/fixtures/ are
exempt. Sample strings below are built by concatenation so this file passes its
own scan.
"""

import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXEMPT_PREFIXES = ("docs/superpowers/", "tests/fixtures/")

HOME_PATTERNS = (
    re.compile(r"(?i)\b[a-z]:[\\/]{1,2}users[\\/]{1,2}[a-z0-9_][\w.-]*"),   # Windows, either slash
    re.compile(r"(?i)(?<![\w.])/[a-z]/users/[a-z0-9_][\w.-]*"),             # Git Bash drive form
    re.compile(r"(?<![\w.])/home/[a-z0-9_][\w.-]*"),                        # Linux
    re.compile(r"(?<![\w.:/])/Users/[A-Za-z0-9_][\w.-]*"),                  # macOS
)
TOKEN_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{32,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def findings(text):
    """Every home-path or token-like match in `text`, as (kind, match)."""
    out = [("home path", m.group(0)) for p in HOME_PATTERNS for m in p.finditer(text)]
    out += [("token", m.group(0)) for p in TOKEN_PATTERNS for m in p.finditer(text)]
    return out


def repo_files():
    """Tracked files plus new files not ignored yet, relative, forward slashes."""
    proc = subprocess.run(["git", "-C", str(REPO), "ls-files", "--cached", "--others", "--exclude-standard"],
                          capture_output=True, encoding="utf-8", check=True)
    return sorted({line for line in proc.stdout.splitlines() if line})


class PatternTest(unittest.TestCase):
    def test_flags_home_paths(self):
        for sample in ("C:" + "\\Users\\bob\\x", "c:" + "/users/bob.smith/", "/c/Us" + "ers/bob",
                       "/ho" + "me/alice/.claude", "/Us" + "ers/carol/Library"):
            self.assertTrue(findings(sample), sample)

    def test_allows_generic_paths(self):
        for sample in ("C:" + "/Users/...", "/c" + "/Users/...", "$HOME/.claude/status_line.py",
                       "~/.claude", "C:\\Program Files\\ClaudeCode\\managed-settings.json",
                       "/Library/Application Support/ClaudeCode", "/etc/claude-code/managed-settings.json"):
            self.assertEqual(findings(sample), [], sample)

    def test_flags_tokens(self):
        for sample in ("gh" + "p_" + "a" * 36, "github" + "_pat_" + "B" * 30, "sk-" + "ant-" + "c" * 30,
                       "AK" + "IA" + "ABCDEFGHIJKLMNOP", "-----BEGIN " + "RSA PRIVATE KEY-----"):
            self.assertTrue(findings(sample), sample)


class RepoScanTest(unittest.TestCase):
    def test_tracked_files_are_portable(self):
        problems = []
        for rel in repo_files():
            if rel.startswith(EXEMPT_PREFIXES):
                continue
            path = REPO / rel
            if not path.is_file():
                continue
            data = path.read_bytes()
            if b"\0" in data:
                continue  # binary
            for kind, match in findings(data.decode("utf-8", errors="replace")):
                problems.append("%s: %s %r" % (rel, kind, match))
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Watch the repo scan fail on a planted home path**

Write `E:\Dev\projects\abc-claude\rules\zz-lint-probe.md` with the single line `See C:/Users/someone/notes.txt`. Then run: `cd /e/Dev/projects/abc-claude && python -m unittest tests.test_portability -v 2>&1 | tail -8`
Expected: `FAIL: test_tracked_files_are_portable`, naming `rules/zz-lint-probe.md: home path 'C:/Users/someone'`.

- [ ] **Step 3: Remove the probe and run again**

Run: `rm /e/Dev/projects/abc-claude/rules/zz-lint-probe.md && cd /e/Dev/projects/abc-claude && python -m unittest tests.test_portability -v 2>&1 | tail -3`
Expected: `Ran 4 tests` and `OK`.

- [ ] **Step 4: Full gate**

Run: `cd /e/Dev/projects/abc-claude && python -m unittest discover -s tests 2>&1 | tail -3`
Expected: `Ran 30 tests` and `OK`.

- [ ] **Step 5: Commit**

Write `.git/TASK_COMMIT_MSG`:
```
Add a portability lint for tracked files

Fail on hard-coded home directories and token-like strings anywhere in
the tracked tree, except plans, specs and test fixtures.
```
Run: `cd /e/Dev/projects/abc-claude && git add tests/test_portability.py && git ls-files --eol tests/test_portability.py && git commit -F .git/TASK_COMMIT_MSG`
Expected: `i/lf    w/lf`, then the commit succeeds.

---

### Task 3: Installer test harness and fake `claude`

**Files:**
- Create: `tests/installer_harness.py`, `tests/fake_claude.py`, `tests/test_harness.py`

**Interfaces:**
- Consumes: `.gitignore`, `.gitattributes`, `templates/local.md` (Task 1); `install.sh` and `install.ps1` when present (Tasks 4-9; a placeholder is committed to the fixture until then).
- Produces (`import installer_harness as h`):
  - `h.REPO_ROOT: Path`, `h.IS_WINDOWS: bool`, `h.ANSI: re.Pattern`.
  - `h.git(*args, cwd=None, check=True) -> str` returns stdout.
  - `h.interpreter_command(name) -> list[str] | None`, for `name` in `"sh"`, `"powershell"`, `"pwsh"`.
  - `h.translate_flag(arg, interp) -> str` maps `--yes` to `-Yes` for PowerShell.
  - `h.Sandbox()` has these attributes: `root`, `seed`, `remote`, `home`, `target` (= `home/.claude`), `downloads`, `bin`, `gitconfig`, `no_managed`, `claude_log`, `claude_state`.
  - `Sandbox` methods:
    - `make_remote(files=None)` builds the fixture remote.
    - `push_change(files, message=...)` commits in seed and pushes to the remote.
    - `Sandbox.write_files(base, files)`, a static method.
    - `set_global_identity(name, email)`; pass `None` to leave a field unset.
    - `fake_claude(plugins=(), marketplaces=("claude-plugins-official",), fail=())`.
    - `claude_calls() -> list[str]`.
    - `claude_plugins() -> list[str]`.
    - `env(**overrides) -> dict`; a value of `None` removes the variable.
    - `run(interp, *args, script_dir=None, stdin="", env=None, timeout=300) -> Result`.
    - `cleanup()`.
  - `Result` has `returncode`, `stdout`, `stderr`, and `output` (stdout + stderr with ANSI codes stripped).
  - `h.snapshot(path) -> dict[str, bytes]` covers every file, including `.git`.
  - `h.backups(path) -> list[str]` returns `*.bak.<14 digits>` paths relative to `path`, outside `.git`.
  - `h.tracked_files(path) -> list[str]`.
  - The fixture `settings.json` enables `alpha@claude-plugins-official: true` and `beta@extra-market: false`, and declares marketplace `extra-market` from `{"source": "github", "repo": "example/extra-market"}`.
  - Fixture `CLAUDE.md` is `# Fixture global rules\n`, and the global identity is `Test User <test@example.com>`.
  - Fake claude:
    - Logs each call's arguments, space-joined, one line per call.
    - `plugin list --json` and `plugin marketplace list --json` print JSON in the real CLI's shape.
    - `plugin install <id>` exits 0. For an already-installed plugin it prints `is already installed`. On a new install it also sets `enabledPlugins[id] = true` in `$CLAUDE_CONFIG_DIR/settings.json`.
    - `plugin marketplace add <owner/repo>` records the marketplace name as the last path segment.
    - `plugin uninstall <id>` removes the plugin.
    - Anything else, or an id or source listed in `fail`, exits 1.

- [ ] **Step 1: Write the failing self-test**

`E:\Dev\projects\abc-claude\tests\test_harness.py`:
```python
"""Self-tests for installer_harness.py and fake_claude.py."""

import json
import os
import subprocess
import unittest

import installer_harness as h


class HarnessTest(unittest.TestCase):
    def setUp(self):
        self.sb = h.Sandbox()
        self.addCleanup(self.sb.cleanup)

    def test_remote_has_fixture_and_real_files(self):
        self.sb.make_remote()
        files = h.git("--git-dir", str(self.sb.remote), "ls-tree", "-r", "--name-only", "master").split()
        for rel in (".gitignore", ".gitattributes", "install.sh", "install.ps1", "templates/local.md",
                    "settings.json", "CLAUDE.md", "status_line.py", "rules/dev-tooling.md"):
            self.assertIn(rel, files)

    def test_push_change_reaches_remote(self):
        self.sb.make_remote()
        self.sb.push_change({"CLAUDE.md": "# changed\n"})
        check = self.sb.root / "check"
        h.git("clone", "-q", str(self.sb.remote), str(check))
        self.assertEqual((check / "CLAUDE.md").read_bytes(), b"# changed\n")

    def test_env_isolates_home_and_config(self):
        env = self.sb.env()
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(self.sb.target))
        self.assertEqual(env["HOME"], str(self.sb.home))
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], str(self.sb.gitconfig))
        self.assertTrue(env["PATH"].startswith(str(self.sb.bin) + os.pathsep))
        self.assertNotIn("CLAUDE_CONFIG_DIR", self.sb.env(CLAUDE_CONFIG_DIR=None))

    def test_translate_flag(self):
        self.assertEqual(h.translate_flag("--yes", "sh"), "--yes")
        self.assertEqual(h.translate_flag("--yes", "pwsh"), "-Yes")
        self.assertEqual(h.translate_flag("--prune", "powershell"), "-Prune")

    def fake_call(self, *args):
        """Run the fake claude through the stub a shell would find."""
        env = self.sb.env()
        if h.IS_WINDOWS:
            argv = ["cmd", "/c", str(self.sb.bin / "claude.cmd"), *args]
        else:
            argv = [str(self.sb.bin / "claude"), *args]
        return subprocess.run(argv, capture_output=True, encoding="utf-8", env=env, timeout=60)

    def test_fake_claude_lists_installs_and_logs(self):
        self.sb.fake_claude(plugins=["alpha@claude-plugins-official"])
        listed = json.loads(self.fake_call("plugin", "list", "--json").stdout)
        self.assertEqual([p["id"] for p in listed], ["alpha@claude-plugins-official"])
        self.assertEqual(self.fake_call("plugin", "install", "beta@extra-market").returncode, 0)
        again = self.fake_call("plugin", "install", "beta@extra-market")
        self.assertEqual(again.returncode, 0)
        self.assertIn("is already installed", again.stdout)
        self.assertEqual(self.sb.claude_plugins(), ["alpha@claude-plugins-official", "beta@extra-market"])
        self.assertEqual(self.fake_call("plugin", "bogus").returncode, 1)
        self.assertEqual(self.sb.claude_calls(), ["plugin list --json", "plugin install beta@extra-market",
                                                  "plugin install beta@extra-market", "plugin bogus"])

    def test_fake_claude_sh_stub_runs_under_the_sh_interpreter(self):
        prefix = h.interpreter_command("sh")
        if prefix is None:
            self.skipTest("no sh interpreter")
        self.sb.fake_claude(marketplaces=["claude-plugins-official"])
        proc = subprocess.run(prefix + ["-c", "claude plugin marketplace list --json"], capture_output=True,
                              encoding="utf-8", env=self.sb.env(), timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual([m["name"] for m in json.loads(proc.stdout)], ["claude-plugins-official"])

    def test_install_failure_listed_in_fail(self):
        self.sb.fake_claude(fail=["beta@extra-market"])
        result = self.fake_call("plugin", "install", "beta@extra-market")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.sb.claude_plugins(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /e/Dev/projects/abc-claude && python -m unittest tests.test_harness 2>&1 | tail -4`
Expected: `ModuleNotFoundError: No module named 'installer_harness'`.

- [ ] **Step 3: Write the fake CLI**

`E:\Dev\projects\abc-claude\tests\fake_claude.py`:
```python
"""Fake `claude` CLI for the installer tests.

The harness puts a `claude` sh script and a `claude.cmd` first on PATH; both run
this file. It appends each call's arguments to FAKE_CLAUDE_LOG (one line per
call, joined by spaces) and answers the plugin subcommands the installers use
from the JSON state in FAKE_CLAUDE_STATE:

    {"plugins": ["name@marketplace", ...], "marketplaces": ["name", ...], "fail": [...]}

`fail` lists plugin ids and marketplace sources whose install/add fails.
Output formats copy Claude Code 2.1.283 (`claude plugin list --json` etc.).
"""

import json
import os
import sys


def load_state():
    with open(os.environ["FAKE_CLAUDE_STATE"], encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    with open(os.environ["FAKE_CLAUDE_STATE"], "w", encoding="utf-8") as f:
        json.dump(state, f)


def mark_enabled(plugin_id):
    """Mimic the real CLI, which writes enabledPlugins[id] = true to user settings on install."""
    config = os.environ.get("CLAUDE_CONFIG_DIR")
    path = os.path.join(config, "settings.json") if config else ""
    if not path or not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        settings = json.load(f)
    settings.setdefault("enabledPlugins", {})[plugin_id] = True
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")


def emit(text, stream=None):
    (stream or sys.stdout).write(text + "\n")


def main(argv):
    with open(os.environ["FAKE_CLAUDE_LOG"], "a", encoding="utf-8", newline="\n") as f:
        f.write(" ".join(argv) + "\n")
    state = load_state()
    plugins, markets, fail = state["plugins"], state["marketplaces"], state.get("fail", [])

    if argv == ["plugin", "list", "--json"]:
        emit(json.dumps([{
            "id": p, "version": "1.0.0", "scope": "user", "enabled": True,
            "installPath": "/fake/plugins/cache/" + p, "installedAt": "2026-01-01T00:00:00.000Z",
            "lastUpdated": "2026-01-01T00:00:00.000Z",
        } for p in plugins], indent=2))
        return 0
    if argv == ["plugin", "marketplace", "list", "--json"]:
        emit(json.dumps([{
            "name": m, "source": "github", "repo": "example/" + m,
            "installLocation": "/fake/plugins/marketplaces/" + m,
        } for m in markets], indent=2))
        return 0
    if argv[:3] == ["plugin", "marketplace", "add"] and len(argv) == 4:
        source = argv[3]
        if source in fail:
            emit("Failed to add marketplace: " + source, sys.stderr)
            return 1
        name = source.rstrip("/").split("/")[-1]
        if name.endswith(".git"):
            name = name[:-4]
        if name in markets:
            emit("Marketplace '%s' already on disk - declared in user settings" % name)
        else:
            markets.append(name)
            save_state(state)
            emit("Successfully added marketplace: %s (declared in user settings)" % name)
        return 0
    if argv[:2] == ["plugin", "install"] and len(argv) == 3:
        pid = argv[2]
        if pid in fail:
            emit('Plugin "%s" not found in any marketplace' % pid, sys.stderr)
            return 1
        if pid in plugins:
            emit('Plugin "%s" is already installed (scope: user)' % pid)
            return 0
        plugins.append(pid)
        save_state(state)
        mark_enabled(pid)
        emit("Successfully installed plugin: %s (scope: user)" % pid)
        return 0
    if argv[:2] == ["plugin", "uninstall"] and len(argv) == 3:
        pid = argv[2]
        if pid not in plugins:
            emit('Plugin "%s" is not installed' % pid, sys.stderr)
            return 1
        plugins.remove(pid)
        save_state(state)
        emit("Successfully uninstalled plugin: %s (scope: user)" % pid)
        return 0
    emit("fake claude: unsupported arguments: " + " ".join(argv), sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Write the harness**

`E:\Dev\projects\abc-claude\tests\installer_harness.py`:
```python
"""Harness for the installer scenario tests (test_installer.py).

Each Sandbox is a temp directory holding everything one scenario needs, so no
test touches the network, the real ~/.claude or the user's git config:

    root/seed/          working repo the fixture remote is built from (branch master)
    root/remote.git     bare repo used as ABC_CLAUDE_REPO
    root/home/          HOME and USERPROFILE; root/home/.claude is CLAUDE_CONFIG_DIR
    root/downloads/     install.sh and install.ps1 copied out of any git repo
                        (the "download and inspect" route)
    root/bin/           first on PATH; holds the fake claude when enabled
    root/gitconfig      GIT_CONFIG_GLOBAL, with a test identity

Interpreters: "sh" is Git Bash's bash.exe on Windows and `sh` elsewhere;
"powershell" (Windows PowerShell 5.1) and "pwsh" run install.ps1 on Windows only.
"""

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).resolve().parent
IS_WINDOWS = os.name == "nt"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
INTERPRETERS = ("sh", "powershell", "pwsh")
FIXTURE_IDENT = ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.com")

FIXTURE_SETTINGS = {
    "model": "opus",
    "enabledPlugins": {
        "alpha@claude-plugins-official": True,
        "beta@extra-market": False,
    },
    "extraKnownMarketplaces": {
        "extra-market": {"source": {"source": "github", "repo": "example/extra-market"}},
    },
}

# Files committed to the fixture remote, besides the real ones in COPIED_FILES.
FIXTURE_FILES = {
    "CLAUDE.md": "# Fixture global rules\n",
    "status_line.py": "print('fixture statusline')\n",
    "rules/dev-tooling.md": "# Fixture tooling rules\n",
    "skills/handoff/SKILL.md": "# Fixture skill\n",
    "settings.json": json.dumps(FIXTURE_SETTINGS, indent=2) + "\n",
}
# Real files from this checkout, so the fixture exercises the real ignore rules,
# template and installers.
COPIED_FILES = (".gitignore", ".gitattributes", "install.sh", "install.ps1", "templates/local.md")


def git(*args, cwd=None, check=True):
    """Run git, return stdout (UTF-8)."""
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise AssertionError("git %s failed (%d): %s" % (" ".join(args), proc.returncode, proc.stderr))
    return proc.stdout


def git_bash():
    """Git for Windows' bin/bash.exe, found from `git --exec-path`, or None."""
    try:
        exec_path = subprocess.run(["git", "--exec-path"], capture_output=True, encoding="utf-8",
                                   check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    bash = Path(exec_path).parents[2] / "bin" / "bash.exe"  # <root>/mingw64/libexec/git-core
    return str(bash) if bash.is_file() else None


def interpreter_command(name):
    """argv prefix that runs an installer under `name`, or None when unavailable."""
    if name == "sh":
        if IS_WINDOWS:
            bash = git_bash()
            return [bash] if bash else None
        sh = shutil.which("sh")
        return [sh] if sh else None
    if not IS_WINDOWS:
        return None  # install.ps1 is the Windows installer
    exe = shutil.which("powershell" if name == "powershell" else "pwsh")
    return [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"] if exe else None


def translate_flag(arg, interp):
    """--yes -> -Yes for install.ps1; other arguments pass through."""
    if interp != "sh" and arg.startswith("--"):
        return "-" + arg[2:].capitalize()
    return arg


def clean_path():
    """PATH without any directory holding a real claude, with this Python first."""
    names = ("claude", "claude.exe", "claude.cmd")
    keep = [d for d in os.environ.get("PATH", "").split(os.pathsep)
            if d and not any(os.path.isfile(os.path.join(d, n)) for n in names)]
    return os.pathsep.join([os.path.dirname(sys.executable)] + keep)


def make_writable_and_remove(path):
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            try:
                os.chmod(os.path.join(dirpath, name), stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)


class Result:
    def __init__(self, proc):
        self.returncode = proc.returncode
        self.stdout = proc.stdout or ""
        self.stderr = proc.stderr or ""
        self.output = ANSI.sub("", self.stdout + self.stderr)

    def __repr__(self):
        return "exit %d\n%s" % (self.returncode, self.output)


class Sandbox:
    def __init__(self):
        self.root = Path(os.path.realpath(tempfile.mkdtemp(prefix="abc-claude-test-")))
        self.seed = self.root / "seed"
        self.remote = self.root / "remote.git"
        self.home = self.root / "home"
        self.target = self.home / ".claude"
        self.downloads = self.root / "downloads"
        self.bin = self.root / "bin"
        self.gitconfig = self.root / "gitconfig"
        self.no_managed = self.root / "no-managed-settings.json"
        self.claude_log = self.root / "claude-calls.log"
        self.claude_state = self.root / "claude-state.json"
        for d in (self.home, self.downloads, self.bin):
            d.mkdir(parents=True)
        self.set_global_identity("Test User", "test@example.com")
        for name in ("install.sh", "install.ps1"):
            if (REPO_ROOT / name).is_file():
                shutil.copyfile(REPO_ROOT / name, self.downloads / name)

    def cleanup(self):
        make_writable_and_remove(self.root)

    # --- fixture remote -----------------------------------------------------
    def make_remote(self, files=None):
        """Build seed (master) from FIXTURE_FILES + COPIED_FILES + `files`, then bare-clone it to remote.git."""
        content = dict(FIXTURE_FILES)
        for rel in COPIED_FILES:
            src = REPO_ROOT / rel
            content[rel] = src.read_bytes() if src.is_file() else b"# not in this checkout yet\n"
        content.update(files or {})
        self.seed.mkdir()
        git("init", "-q", "-b", "master", cwd=self.seed)
        git("config", "core.autocrlf", "false", cwd=self.seed)
        self.write_files(self.seed, content)
        git("add", "-A", cwd=self.seed)
        git(*FIXTURE_IDENT, "commit", "-q", "-m", "fixture", cwd=self.seed)
        git("clone", "-q", "--bare", str(self.seed), str(self.remote))
        git("remote", "add", "origin", str(self.remote), cwd=self.seed)

    def push_change(self, files, message="fixture change"):
        """Commit `files` in seed and push them to remote.git."""
        self.write_files(self.seed, files)
        git("add", "-A", cwd=self.seed)
        git(*FIXTURE_IDENT, "commit", "-q", "-m", message, cwd=self.seed)
        git("push", "-q", "origin", "master", cwd=self.seed)

    @staticmethod
    def write_files(base, files):
        for rel, data in files.items():
            path = Path(base) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)

    def set_global_identity(self, name, email):
        lines = ["[user]"]
        if name:
            lines.append("\tname = " + name)
        if email:
            lines.append("\temail = " + email)
        self.gitconfig.write_text("\n".join(lines) + "\n" if len(lines) > 1 else "", encoding="utf-8")

    # --- fake claude --------------------------------------------------------
    def fake_claude(self, plugins=(), marketplaces=("claude-plugins-official",), fail=()):
        """Put a logging fake `claude` (sh script + claude.cmd) in bin/."""
        self.claude_state.write_text(json.dumps({"plugins": list(plugins), "marketplaces": list(marketplaces),
                                                 "fail": list(fail)}), encoding="utf-8")
        py = sys.executable.replace("\\", "/")
        script = str(TESTS_DIR / "fake_claude.py").replace("\\", "/")
        sh_stub = self.bin / "claude"
        sh_stub.write_bytes(('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (py, script)).encode("utf-8"))
        sh_stub.chmod(0o755)
        (self.bin / "claude.cmd").write_bytes(
            ('@echo off\r\n"%s" "%s" %%*\r\nexit /b %%ERRORLEVEL%%\r\n' % (sys.executable, script)).encode("utf-8"))

    def claude_calls(self):
        if not self.claude_log.is_file():
            return []
        return self.claude_log.read_text(encoding="utf-8").splitlines()

    def claude_plugins(self):
        return json.loads(self.claude_state.read_text(encoding="utf-8"))["plugins"]

    # --- running the installer ----------------------------------------------
    def env(self, **overrides):
        env = dict(os.environ)
        for key in ("ABC_CLAUDE_REPO", "CLAUDE_CONFIG_DIR", "ABC_CLAUDE_MANAGED_PATHS", "GIT_DIR",
                    "GIT_WORK_TREE", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM"):
            env.pop(key, None)
        env.update({
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "CLAUDE_CONFIG_DIR": str(self.target),
            "ABC_CLAUDE_REPO": str(self.remote),
            "ABC_CLAUDE_MANAGED_PATHS": str(self.no_managed),
            "GIT_CONFIG_GLOBAL": str(self.gitconfig),
            "GIT_TERMINAL_PROMPT": "0",
            "FAKE_CLAUDE_LOG": str(self.claude_log),
            "FAKE_CLAUDE_STATE": str(self.claude_state),
            "PATH": str(self.bin) + os.pathsep + clean_path(),
        })
        for key, value in overrides.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = str(value)
        return env

    def run(self, interp, *args, script_dir=None, stdin="", env=None, timeout=300):
        """Run the installer from `script_dir` (default downloads/) under `interp`."""
        prefix = interpreter_command(interp)
        if prefix is None:
            raise AssertionError(interp + " is not available")
        base = Path(script_dir) if script_dir else self.downloads
        if interp == "sh":
            script = str(base / "install.sh").replace("\\", "/")
        else:
            script = str(base / "install.ps1")
        argv = prefix + [script] + [translate_flag(a, interp) for a in args]
        proc = subprocess.run(argv, input=stdin, capture_output=True, encoding="utf-8", errors="replace",
                              env=env if env is not None else self.env(), cwd=str(self.root), timeout=timeout)
        return Result(proc)


# --- assertions helpers -------------------------------------------------------
def snapshot(path):
    """{relative path: bytes} for every file under `path`, including .git."""
    out = {}
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            full = Path(dirpath) / name
            out[full.relative_to(path).as_posix()] = full.read_bytes()
    return out


def backups(path):
    """Relative paths of *.bak.<14 digits> files under `path`, outside .git."""
    pattern = re.compile(r"\.bak\.\d{14}$")
    return sorted(rel for rel in snapshot(path) if not rel.startswith(".git/") and pattern.search(rel))


def tracked_files(path):
    return [line for line in git("-C", str(path), "ls-files").splitlines() if line]
```

- [ ] **Step 5: Run the self-test**

Run: `cd /e/Dev/projects/abc-claude && python -m unittest tests.test_harness -v 2>&1 | tail -4`
Expected: `Ran 7 tests` and `OK`.

- [ ] **Step 6: Full gate and lint**

Run: `cd /e/Dev/projects/abc-claude && python -m unittest discover -s tests 2>&1 | tail -3`
Expected: `Ran 37 tests` and `OK`. The portability scan also passes: the harness builds `.claude` paths from `self.home`, never from a literal home directory.

- [ ] **Step 7: Commit**

Write `.git/TASK_COMMIT_MSG`:
```
Add the installer test harness and a fake claude CLI

Sandbox builds a temp bare repo from the real ignore rules, template and
installers, isolates HOME, CLAUDE_CONFIG_DIR and git config, finds Git
Bash, Windows PowerShell and pwsh, and runs an installer under each. The
fake claude logs its calls and answers plugin list/install/add/uninstall
like Claude Code 2.1.283, including the settings.json rewrite on install.
```
Run: `cd /e/Dev/projects/abc-claude && git add tests/installer_harness.py tests/fake_claude.py tests/test_harness.py && git ls-files --eol tests/ && git commit -F .git/TASK_COMMIT_MSG`
Expected: every file shows `i/lf    w/lf`, then the commit succeeds.

---

### Task 4: Installer skeleton: flags, dependencies, source and target (both scripts)

**Files:**
- Create: `install.sh`, `install.ps1`, `tests/test_installer.py`

**Interfaces:**
- Consumes: the `installer_harness` API (Task 3).
- Produces. install.sh:
  - Globals: `YES PRUNE WARNINGS BACKUPS PY LOCAL_SRC FETCH_URL ORIGIN_URL TARGET STAMP TAB NL IS_WINDOWS REPO_URL`.
  - Logging and helpers: `info warn err die have ask(PROMPT, DEFAULT)`. `ask` prints the answer; the prompt goes to stderr.
  - `posix_path canon_path same_path find_source resolve_target install_deps summary`.
- Produces. install.ps1:
  - Globals: `$Yes $Prune $Help $Stamp $ScriptDir $RepoUrl`, plus `$script:` `Warnings Backups NativeExit Py LocalSrc FetchUrl OriginUrl Target`.
  - Logging: `Write-Info Write-Warn Write-Err Fail Have`.
  - Native-call helpers: `Invoke-Quiet -Exe -ArgList` (returns stdout, sets `$script:NativeExit`), `Get-GitLine([string[]])`, `Invoke-Git([string[]]) -> bool`, `Ask($Prompt, $Default)`.
  - `Resolve-Full Test-SamePath Find-Source Resolve-Target Install-Deps Write-Summary`.
- Semantics:
  - `FETCH_URL` is where to clone or fetch from: the local checkout path, or `REPO_URL`.
  - `ORIGIN_URL` is what `origin` must end up as: the checkout's origin, or `REPO_URL`.
  - `TARGET` is the resolved target directory.
- Messages:
  - `Installing from <url>.`
  - `Installing from local checkout <path> (origin <url>).`
  - `Target: <path>`
  - `CLAUDE_CONFIG_DIR is <path>: the tracked settings.json commands assume ~/.claude.`
  - `Done.` or `Done with N warning(s).`
  - `Backed up files that differed from the repo (compare them, then delete the backups):`
  - `Local-only files to review (commit them or delete them):`, followed by 4-space-indented paths.

- [ ] **Step 1: Write the failing tests**

`E:\Dev\projects\abc-claude\tests\test_installer.py` (`json` and `re` are used from Task 6 on):
```python
"""Installer scenario suite: every scenario runs against install.sh (Git Bash on
Windows, sh elsewhere) and against install.ps1 under Windows PowerShell 5.1 and
pwsh on Windows. Missing interpreters are skipped. No network, no real ~/.claude.
"""

import json
import re
import subprocess
import unittest

import installer_harness as h


class InstallerScenarios:
    INTERP = None

    def setUp(self):
        if h.interpreter_command(self.INTERP) is None:
            self.skipTest(self.INTERP + " is not available here")
        self.sb = h.Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.sb.make_remote()

    def install(self, *args, **kwargs):
        return self.sb.run(self.INTERP, *args, **kwargs)

    def assertOk(self, result):
        self.assertEqual(result.returncode, 0, result)

    def target_git(self, *args):
        return h.git("-C", str(self.sb.target), *args).strip()

    # --- task 4: skeleton ---------------------------------------------------
    def test_unknown_flag_exits_1(self):
        result = self.install("--bogus")
        self.assertEqual(result.returncode, 1, result)
        self.assertFalse(self.sb.target.exists())

    def test_reports_source_and_target(self):
        result = self.install("--yes")
        self.assertIn("[abc-claude] Installing from " + str(self.sb.remote) + ".", result.output)
        self.assertIn("[abc-claude] Target: ", result.output)
        self.assertNotIn("CLAUDE_CONFIG_DIR is", result.output)

    def test_warns_when_config_dir_is_not_home_claude(self):
        other = self.sb.root / "elsewhere"
        result = self.install("--yes", env=self.sb.env(CLAUDE_CONFIG_DIR=other))
        self.assertIn("the tracked settings.json commands assume ~/.claude", result.output)


class ShTests(InstallerScenarios, unittest.TestCase):
    INTERP = "sh"


class WindowsPowerShellTests(InstallerScenarios, unittest.TestCase):
    INTERP = "powershell"


class PwshTests(InstallerScenarios, unittest.TestCase):
    INTERP = "pwsh"


class StaticChecks(unittest.TestCase):
    def test_install_ps1_is_ascii_lf_without_bom(self):
        data = (h.REPO_ROOT / "install.ps1").read_bytes()
        self.assertFalse(data.startswith(b"\xef\xbb\xbf"), "install.ps1 has a BOM")
        self.assertEqual([i for i, b in enumerate(data) if b > 0x7F], [], "non-ASCII bytes in install.ps1")
        self.assertNotIn(b"\r", data)
        self.assertIn(b"#Requires -Version 5", data)

    def test_install_sh_parses_and_is_lf(self):
        data = (h.REPO_ROOT / "install.sh").read_bytes()
        self.assertNotIn(b"\r", data)
        self.assertTrue(data.startswith(b"#!/usr/bin/env sh\n"))
        prefix = h.interpreter_command("sh")
        if prefix is None:
            self.skipTest("no sh interpreter")
        proc = subprocess.run(prefix + ["-n", str(h.REPO_ROOT / "install.sh").replace("\\", "/")],
                              capture_output=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd /e/Dev/projects/abc-claude && python -m unittest tests.test_installer 2>&1 | tail -4`
Expected: `FAILED`, with 11 tests failing or erroring: the scripts don't exist (exit 127 from bash, a "does not exist" error from PowerShell, and `FileNotFoundError` in StaticChecks).

- [ ] **Step 3: Write `install.sh`**

`E:\Dev\projects\abc-claude\install.sh`:
```sh
#!/usr/bin/env sh
# ============================================================================
# abc-claude - installer for Linux, macOS and Git Bash on Windows
#
# The repo is private, so download it signed in, inspect it, then run it:
#   gh api repos/aaronbcarlisle/abc-claude/contents/install.sh -H "Accept: application/vnd.github.raw" > install.sh
#   less install.sh   # review before running
#   sh install.sh [--yes] [--prune]
# Or run it from a clone: gh repo clone aaronbcarlisle/abc-claude <dir>; sh <dir>/install.sh
#
# Idempotent and safe to re-run. It will:
#   1. Install missing dependencies (git, python) with your package manager.
#   2. Find the source: the checkout it runs from, or ABC_CLAUDE_REPO.
#   3. Make ~/.claude (or $CLAUDE_CONFIG_DIR) a working tree of the repo,
#      backing up any file it replaces to <path>.bak.<yyyyMMddHHmmss>.
#   4. Create rules/local.md and set this repo's git identity.
#   5. Report managed settings and check that python resolves.
#   6. Install the marketplaces and plugins listed in settings.json.
# ============================================================================

set -eu

DEFAULT_REPO="https://github.com/aaronbcarlisle/abc-claude.git"
REPO_URL="${ABC_CLAUDE_REPO:-$DEFAULT_REPO}"
STAMP=$(date +%Y%m%d%H%M%S)
TAB=$(printf '\t')
NL='
'
YES=0
PRUNE=0
WARNINGS=0
BACKUPS=""
PY=""
LOCAL_SRC=""
FETCH_URL=""
ORIGIN_URL=""
TARGET=""

# --- pretty logging ---------------------------------------------------------
info() { printf '\033[0;32m[abc-claude]\033[0m %s\n' "$1"; }
warn() { WARNINGS=$((WARNINGS + 1)); printf '\033[0;33m[abc-claude]\033[0m %s\n' "$1" >&2; }
err()  { printf '\033[0;31m[abc-claude]\033[0m %s\n' "$1" >&2; }
die()  { err "$1"; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

IS_WINDOWS=0
case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) IS_WINDOWS=1 ;;
esac

# ask PROMPT DEFAULT - print the reply; DEFAULT under --yes, on empty input or at EOF.
ask() {
    if [ "$YES" = 1 ]; then
        printf '%s\n' "$2"
        return 0
    fi
    printf '\033[0;32m[abc-claude]\033[0m %s [%s]: ' "$1" "$2" >&2
    reply=""
    IFS= read -r reply || true
    if [ -n "$reply" ]; then printf '%s\n' "$reply"; else printf '%s\n' "$2"; fi
}

# --- arguments --------------------------------------------------------------
usage() { printf 'Usage: sh install.sh [--yes] [--prune]\n'; }
for arg in "$@"; do
    case "$arg" in
        --yes|-y) YES=1 ;;
        --prune) PRUNE=1 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "Unknown option: $arg" ;;
    esac
done

# --- dependencies -----------------------------------------------------------
# install_pkg PKG [ALT] - install with the first package manager found (ALT is
# the name on pacman, brew and port); sudo only when not root.
install_pkg() {
    alt="${2:-$1}"
    SUDO=""
    if [ "$(id -u)" -ne 0 ] && have sudo; then SUDO="sudo"; fi
    if   have apt-get; then $SUDO apt-get update && $SUDO apt-get install -y "$1"
    elif have dnf;     then $SUDO dnf install -y "$1"
    elif have yum;     then $SUDO yum install -y "$1"
    elif have pacman;  then $SUDO pacman -S --noconfirm "$alt"
    elif have zypper;  then $SUDO zypper install -y "$1"
    elif have apk;     then $SUDO apk add "$1"
    elif have brew;    then brew install "$alt"
    elif have port;    then $SUDO port install "$alt"
    else
        err "No supported package manager found."
        return 1
    fi
}

# find_python - set PY to the first of python, python3 that runs.
find_python() {
    for candidate in python python3; do
        if have "$candidate" && "$candidate" --version >/dev/null 2>&1; then
            PY=$candidate
            return 0
        fi
    done
    return 1
}

# missing_dep NAME PKG [ALT] - install a missing required dependency or exit 1.
missing_dep() {
    warn "'$1' is not installed - attempting to install it..."
    if [ "$IS_WINDOWS" = 1 ]; then
        die "Install '$1' (winget, or run install.ps1 from PowerShell), then re-run."
    fi
    install_pkg "$2" "${3:-$2}" || die "Could not install '$1'. Install it manually and re-run."
}

install_deps() {
    if ! have git; then
        missing_dep git git
        have git || die "Could not install 'git'. Install it manually and re-run."
        info "Installed 'git'."
    fi
    if ! find_python; then
        missing_dep python python3 python
        find_python || die "Could not install 'python'. Install it manually and re-run."
        info "Installed 'python'."
    fi
    if ! have gh; then
        info "Optional 'gh' is not on PATH: it is only needed to download the installer or sign in to GitHub."
    fi
}

# --- find the source --------------------------------------------------------
# posix_path PATH - Git Bash form (/c/...) of a Windows path; unchanged elsewhere.
posix_path() {
    if [ "$IS_WINDOWS" = 1 ] && have cygpath; then cygpath -u "$1"; else printf '%s\n' "$1"; fi
}

# canon_path PATH - comparable form: absolute and symlink-free when PATH exists,
# no trailing slash, lowercase on Windows.
canon_path() {
    cp_path=$(posix_path "$1")
    if [ -d "$cp_path" ]; then cp_path=$(CDPATH='' cd -- "$cp_path" && pwd -P); fi
    cp_path=${cp_path%/}
    if [ "$IS_WINDOWS" = 1 ]; then
        printf '%s\n' "$cp_path" | tr '[:upper:]' '[:lower:]'
    else
        printf '%s\n' "$cp_path"
    fi
}

same_path() { [ "$(canon_path "$1")" = "$(canon_path "$2")" ]; }

find_source() {
    script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" 2>/dev/null && pwd -P) || script_dir=""
    if [ -n "$script_dir" ]; then
        top=$(git -C "$script_dir" rev-parse --show-toplevel 2>/dev/null) || top=""
        # Only an abc-claude checkout counts, never just any repo.
        if [ -n "$top" ] && [ -f "$top/install.sh" ] && [ -f "$top/install.ps1" ] && [ -f "$top/status_line.py" ]; then
            LOCAL_SRC=$(posix_path "$top")
        fi
    fi
    if [ -n "$LOCAL_SRC" ]; then
        FETCH_URL=$LOCAL_SRC
        ORIGIN_URL=$(git -C "$LOCAL_SRC" config --get remote.origin.url 2>/dev/null) || ORIGIN_URL=""
        if [ -z "$ORIGIN_URL" ]; then
            warn "$LOCAL_SRC has no origin remote - using $REPO_URL as origin."
            ORIGIN_URL=$REPO_URL
        fi
        info "Installing from local checkout $LOCAL_SRC (origin $ORIGIN_URL)."
    else
        FETCH_URL=$REPO_URL
        ORIGIN_URL=$REPO_URL
        info "Installing from $REPO_URL."
    fi
}

resolve_target() {
    if [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then
        TARGET=$(posix_path "$CLAUDE_CONFIG_DIR")
        TARGET=${TARGET%/}
        if ! same_path "$TARGET" "$HOME/.claude"; then
            warn "CLAUDE_CONFIG_DIR is $TARGET: the tracked settings.json commands assume ~/.claude."
        fi
    else
        TARGET="$HOME/.claude"
    fi
    info "Target: $TARGET"
}

# --- summary ----------------------------------------------------------------
summary() {
    if [ -n "$BACKUPS" ]; then
        info "Backed up files that differed from the repo (compare them, then delete the backups):"
        printf '%s' "$BACKUPS" | sed 's/^/    /'
    fi
    if [ -d "$TARGET/.git" ]; then
        local_only=$(git -C "$TARGET" -c core.quotepath=off ls-files -o --exclude-standard) || local_only=""
        if [ -n "$local_only" ]; then
            info "Local-only files to review (commit them or delete them):"
            printf '%s\n' "$local_only" | sed 's/^/    /'
        fi
    fi
    if [ "$WARNINGS" -gt 0 ]; then info "Done with $WARNINGS warning(s)."; else info "Done."; fi
}

# --- main -------------------------------------------------------------------
install_deps
find_source
resolve_target
summary
```

- [ ] **Step 4: Write `install.ps1`**

`E:\Dev\projects\abc-claude\install.ps1` (ASCII only):
```powershell
# ============================================================================
# abc-claude - Windows installer (PowerShell)
#
# The repo is private, so download it signed in, inspect it, then run it:
#   gh api repos/aaronbcarlisle/abc-claude/contents/install.ps1 -H "Accept: application/vnd.github.raw" > install.ps1
#   notepad install.ps1   # review before running
#   powershell -ExecutionPolicy Bypass -File install.ps1 [-Yes] [-Prune]
# Or run it from a clone: gh repo clone aaronbcarlisle/abc-claude <dir>, then
#   powershell -ExecutionPolicy Bypass -File <dir>\install.ps1
#
# Idempotent and safe to re-run. It will:
#   1. Install missing dependencies (git, python) via winget/choco/scoop.
#   2. Find the source: the checkout it runs from, or ABC_CLAUDE_REPO.
#   3. Make ~/.claude (or $env:CLAUDE_CONFIG_DIR) a working tree of the repo,
#      backing up any file it replaces to <path>.bak.<yyyyMMddHHmmss>.
#   4. Create rules/local.md and set this repo's git identity.
#   5. Report managed settings and check that python resolves from Git Bash.
#   6. Install the marketplaces and plugins listed in settings.json.
# ============================================================================

#Requires -Version 5
[CmdletBinding()]
param([switch]$Yes, [switch]$Prune, [switch]$Help)
$ErrorActionPreference = 'Stop'

$DefaultRepo = 'https://github.com/aaronbcarlisle/abc-claude.git'
$RepoUrl = if ($env:ABC_CLAUDE_REPO) { $env:ABC_CLAUDE_REPO } else { $DefaultRepo }
$Stamp = Get-Date -Format 'yyyyMMddHHmmss'
$ScriptDir = if ($PSCommandPath) { Split-Path -Parent $PSCommandPath } else { $null }
$script:Warnings = 0
$script:Backups = @()
$script:NativeExit = 0
$script:Py = $null
$script:LocalSrc = $null
$script:FetchUrl = $null
$script:OriginUrl = $null
$script:Target = $null

# --- pretty logging ---------------------------------------------------------
function Write-Info($m) { Write-Host "[abc-claude] $m" -ForegroundColor Green }
function Write-Warn($m) { $script:Warnings++; Write-Host "[abc-claude] $m" -ForegroundColor Yellow }
function Write-Err ($m) { Write-Host "[abc-claude] $m" -ForegroundColor Red }
function Fail($m) { Write-Err $m; exit 1 }
function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

# Windows PowerShell 5.1 turns a native command's redirected stderr into a
# terminating error under $ErrorActionPreference = 'Stop'. Every captured native
# call goes through Invoke-Quiet: it returns stdout lines, drops stderr and
# leaves the exit code in $script:NativeExit. Wrap calls in @(...).
function Invoke-Quiet {
    param([Parameter(Mandatory = $true)][string]$Exe, [string[]]$ArgList = @())
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $Exe @ArgList 2>$null
        $script:NativeExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $old
    }
    return $out
}

# First stdout line of a git command, or '' when it fails.
function Get-GitLine([string[]]$GitArgs) {
    $out = @(Invoke-Quiet 'git' $GitArgs)
    if ($script:NativeExit -ne 0 -or $out.Count -eq 0) { return '' }
    return [string]$out[0]
}

# Run git with its output shown; $true when it exits 0.
function Invoke-Git([string[]]$GitArgs) {
    & git @GitArgs | Out-Host
    return ($LASTEXITCODE -eq 0)
}

# Ask PROMPT DEFAULT - the reply; DEFAULT under -Yes, on empty input or at EOF.
function Ask($Prompt, $Default) {
    if ($Yes) { return $Default }
    Write-Host -NoNewline "[abc-claude] $Prompt [$Default]: " -ForegroundColor Green
    $reply = [Console]::In.ReadLine()
    if ([string]::IsNullOrEmpty($reply)) { return $Default }
    return $reply
}

if ($Help) {
    Write-Host 'Usage: powershell -ExecutionPolicy Bypass -File install.ps1 [-Yes] [-Prune]'
    exit 0
}

# --- dependencies -----------------------------------------------------------
function Update-SessionPath {
    # Keep this session's PATH first, then add what an installer just registered.
    $machine = [System.Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [System.Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = (@($env:Path, $machine, $user) | Where-Object { $_ }) -join ';'
}

# The first of python, python3 that runs, or $null.
function Find-Python {
    foreach ($candidate in @('python', 'python3')) {
        if (Have $candidate) {
            Invoke-Quiet $candidate @('--version') | Out-Null
            if ($script:NativeExit -eq 0) { return $candidate }
        }
    }
    return $null
}

# Try every package manager present, in turn: winget (user scope), choco, scoop.
function Install-Package($Name, $WingetId, $ChocoId, $ScoopId, [scriptblock]$Found) {
    Write-Warn "'$Name' is not installed - attempting to install it..."
    $managers = @(
        @{ Name = 'winget'; Args = @('install', '--id', $WingetId, '-e', '--scope', 'user', '--source', 'winget',
                                     '--accept-source-agreements', '--accept-package-agreements') },
        @{ Name = 'choco'; Args = @('install', $ChocoId, '-y') },
        @{ Name = 'scoop'; Args = @('install', $ScoopId) }
    )
    $tried = $false
    foreach ($m in $managers) {
        if (-not (Have $m.Name)) { continue }
        $tried = $true
        Write-Info "Trying $($m.Name)..."
        [string[]]$managerArgs = $m.Args
        try { & $m.Name @managerArgs | Out-Host } catch { Write-Warn "$($m.Name) failed: $_" }
        Update-SessionPath
        if (& $Found) { Write-Info "Installed '$Name' via $($m.Name)."; return }
    }
    if (-not $tried) { Fail "No supported package manager (winget/choco/scoop) found. Install '$Name' manually and re-run." }
    Fail "Could not install '$Name' with any available package manager. Install it manually (or open a new terminal so PATH refreshes) and re-run."
}

function Install-Deps {
    if (-not (Have 'git')) { Install-Package 'git' 'Git.Git' 'git' 'git' { Have 'git' } }
    $script:Py = Find-Python
    if (-not $script:Py) {
        Install-Package 'python' 'Python.Python.3.13' 'python' 'python' { [bool](Find-Python) }
        $script:Py = Find-Python
    }
    if (-not (Have 'gh')) {
        Write-Info "Optional 'gh' is not on PATH: it is only needed to download the installer or sign in to GitHub."
    }
}

# --- find the source --------------------------------------------------------
function Resolve-Full($p) { try { [System.IO.Path]::GetFullPath($p).TrimEnd('\', '/') } catch { $p } }
function Test-SamePath($a, $b) { return ((Resolve-Full $a) -ieq (Resolve-Full $b)) }

function Find-Source {
    if ($ScriptDir) {
        $top = Get-GitLine @('-C', $ScriptDir, 'rev-parse', '--show-toplevel')
        # Only an abc-claude checkout counts, never just any repo.
        if ($top -and (Test-Path -LiteralPath (Join-Path $top 'install.sh')) -and
            (Test-Path -LiteralPath (Join-Path $top 'install.ps1')) -and
            (Test-Path -LiteralPath (Join-Path $top 'status_line.py'))) {
            $script:LocalSrc = Resolve-Full $top
        }
    }
    if ($script:LocalSrc) {
        $script:FetchUrl = $script:LocalSrc
        $script:OriginUrl = Get-GitLine @('-C', $script:LocalSrc, 'config', '--get', 'remote.origin.url')
        if (-not $script:OriginUrl) {
            Write-Warn "$($script:LocalSrc) has no origin remote - using $RepoUrl as origin."
            $script:OriginUrl = $RepoUrl
        }
        Write-Info "Installing from local checkout $($script:LocalSrc) (origin $($script:OriginUrl))."
    } else {
        $script:FetchUrl = $RepoUrl
        $script:OriginUrl = $RepoUrl
        Write-Info "Installing from $RepoUrl."
    }
}

function Resolve-Target {
    $userHome = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
    $default = Join-Path $userHome '.claude'
    if ($env:CLAUDE_CONFIG_DIR) {
        $script:Target = Resolve-Full $env:CLAUDE_CONFIG_DIR
        if (-not (Test-SamePath $script:Target $default)) {
            Write-Warn "CLAUDE_CONFIG_DIR is $($script:Target): the tracked settings.json commands assume ~/.claude."
        }
    } else {
        $script:Target = Resolve-Full $default
    }
    Write-Info "Target: $($script:Target)"
}

# --- summary ----------------------------------------------------------------
function Write-Summary {
    if ($script:Backups.Count -gt 0) {
        Write-Info 'Backed up files that differed from the repo (compare them, then delete the backups):'
        foreach ($b in $script:Backups) { Write-Host "    $b" }
    }
    if (Test-Path -LiteralPath (Join-Path $script:Target '.git')) {
        $localOnly = @(Invoke-Quiet 'git' @('-C', $script:Target, '-c', 'core.quotepath=off', 'ls-files', '-o', '--exclude-standard') | Where-Object { $_ })
        if ($localOnly.Count -gt 0) {
            Write-Info 'Local-only files to review (commit them or delete them):'
            foreach ($f in $localOnly) { Write-Host "    $f" }
        }
    }
    if ($script:Warnings -gt 0) { Write-Info "Done with $($script:Warnings) warning(s)." } else { Write-Info 'Done.' }
}

# --- main -------------------------------------------------------------------
Install-Deps
Find-Source
Resolve-Target
Write-Summary
exit 0
```

- [ ] **Step 5: Run the installer tests**

Run: `cd /e/Dev/projects/abc-claude && python -m unittest tests.test_installer -v 2>&1 | tail -4`
Expected: `Ran 11 tests` and `OK`.

- [ ] **Step 6: Full gate**

Run: `cd /e/Dev/projects/abc-claude && python -m unittest discover -s tests 2>&1 | tail -3`
Expected: `Ran 48 tests` and `OK`.

- [ ] **Step 7: Commit**

Write `.git/TASK_COMMIT_MSG`:
```
Add the installer skeletons: flags, dependencies, source and target

install.sh (POSIX sh) and install.ps1 (Windows PowerShell 5.1) parse
--yes/--prune, install missing git and python, detect a local abc-claude
checkout, resolve the target from CLAUDE_CONFIG_DIR or ~/.claude and
print a summary. Unknown flags exit 1 in both.
```
Run: `cd /e/Dev/projects/abc-claude && git add install.sh install.ps1 tests/test_installer.py && git ls-files --eol install.sh install.ps1 tests/test_installer.py && git commit -F .git/TASK_COMMIT_MSG`
Expected: each file shows `i/lf    w/lf`, then the commit succeeds.

---

### Task 5: Bootstrap: clone, update, restore, foreign origin, in place, reachability (both scripts)

**Files:**
- Modify: `install.sh`, `install.ps1` (new `# --- bootstrap ---` section plus the main block)
- Modify: `tests/test_installer.py` (new methods)

**Interfaces:**
- Consumes: `TARGET LOCAL_SRC FETCH_URL ORIGIN_URL same_path info warn die` (sh), and `$script:Target LocalSrc FetchUrl OriginUrl Get-GitLine Invoke-Git Invoke-Quiet Test-SamePath` (ps1), all from Task 4.
- Produces. install.sh: `norm_url same_url SIGN_IN_HINT check_remote(URL, die|warn) restore_missing update_existing warn_uncommitted_source clone_fresh bootstrap`.
- Produces. install.ps1: `Normalize-Url $SignInHint Test-Remote($Url, -Fatal) Restore-Missing Update-Existing Warn-UncommittedSource Invoke-Clone Invoke-Bootstrap`.
- Messages:
  - `Cloning into <path>.`
  - `<path> is an abc-claude checkout - updating it.`
  - `Restored N missing tracked file(s).`
  - `Tracked files in <path> have local changes - skipping the pull. Commit or discard them, then re-run.`
  - `<path> is a git repo with a different origin (<url>|none). Move it aside or set CLAUDE_CONFIG_DIR, then re-run. Nothing was changed.`
  - `Cannot reach <url>. Sign in first (gh auth login, or Git Credential Manager), then re-run.`
  - `Running from the checkout at <path> - using it in place.`
  - `<path> has uncommitted edits: they are not carried over. Commit them, then re-run.`
- Interim behavior: an existing non-git directory exits 1 with `<path> exists and is not a git repo. Move it aside, then re-run. Nothing was changed.` Task 6 replaces this with adopt.

- [ ] **Step 1: Add the failing tests**

Append to the end of `class InstallerScenarios`, after its last method, with one blank line between methods and two blank lines before `class ShTests`:
```python
    # --- task 5: bootstrap --------------------------------------------------
    def test_fresh_clone(self):
        result = self.install("--yes")
        self.assertOk(result)
        for rel in ("settings.json", "CLAUDE.md", "rules/dev-tooling.md", "install.sh", "templates/local.md"):
            self.assertTrue((self.sb.target / rel).is_file(), rel)
        self.assertEqual(self.target_git("config", "--get", "remote.origin.url"), str(self.sb.remote))
        self.assertEqual(self.target_git("config", "--get", "core.autocrlf"), "false")
        self.assertEqual(self.target_git("rev-parse", "--abbrev-ref", "HEAD"), "master")
        self.assertEqual(self.target_git("status", "--porcelain"), "")
        self.assertIn("Cloning into", result.output)

    def test_rerun_on_clean_repo_is_a_no_op(self):
        self.assertOk(self.install("--yes"))
        head = self.target_git("rev-parse", "HEAD")
        before = h.snapshot(self.sb.target)
        result = self.install("--yes")
        self.assertOk(result)
        self.assertEqual(self.target_git("rev-parse", "HEAD"), head)
        self.assertEqual(h.backups(self.sb.target), [])
        after = h.snapshot(self.sb.target)
        worktree = lambda snap: {k: v for k, v in snap.items() if not k.startswith(".git/")}
        self.assertEqual(worktree(after), worktree(before))
        self.assertIn("is an abc-claude checkout - updating it", result.output)

    def test_rerun_pulls_new_commits(self):
        self.assertOk(self.install("--yes"))
        self.sb.push_change({"CLAUDE.md": "# Updated rules\n"})
        self.assertOk(self.install("--yes"))
        self.assertEqual((self.sb.target / "CLAUDE.md").read_bytes(), b"# Updated rules\n")

    def test_rerun_with_local_edits_skips_pull(self):
        self.assertOk(self.install("--yes"))
        head = self.target_git("rev-parse", "HEAD")
        (self.sb.target / "CLAUDE.md").write_bytes(b"# My local edit\n")
        self.sb.push_change({"rules/dev-tooling.md": "# Upstream change\n"})
        result = self.install("--yes")
        self.assertOk(result)
        self.assertIn("have local changes - skipping the pull", result.output)
        self.assertEqual(self.target_git("rev-parse", "HEAD"), head)
        self.assertEqual((self.sb.target / "CLAUDE.md").read_bytes(), b"# My local edit\n")

    def test_missing_tracked_files_are_restored(self):
        self.assertOk(self.install("--yes"))
        (self.sb.target / "CLAUDE.md").unlink()
        (self.sb.target / "rules" / "dev-tooling.md").unlink()
        result = self.install("--yes")
        self.assertOk(result)
        self.assertIn("Restored 2 missing tracked file(s).", result.output)
        self.assertEqual((self.sb.target / "CLAUDE.md").read_bytes(), b"# Fixture global rules\n")
        self.assertTrue((self.sb.target / "rules" / "dev-tooling.md").is_file())

    def test_foreign_origin_exits_1_and_changes_nothing(self):
        self.sb.target.mkdir()
        h.git("init", "-q", "-b", "master", cwd=self.sb.target)
        h.git("remote", "add", "origin", "https://example.invalid/someone/other.git", cwd=self.sb.target)
        (self.sb.target / "settings.json").write_bytes(b'{"mine": true}\n')
        before = h.snapshot(self.sb.target)
        result = self.install("--yes")
        self.assertEqual(result.returncode, 1, result)
        self.assertIn("is a git repo with a different origin (https://example.invalid/someone/other.git)",
                      result.output)
        self.assertEqual(h.snapshot(self.sb.target), before)

    def test_repo_without_origin_exits_1(self):
        self.sb.target.mkdir()
        h.git("init", "-q", "-b", "master", cwd=self.sb.target)
        before = h.snapshot(self.sb.target)
        result = self.install("--yes")
        self.assertEqual(result.returncode, 1, result)
        self.assertIn("different origin (none)", result.output)
        self.assertEqual(h.snapshot(self.sb.target), before)

    def test_origin_spelled_differently_is_still_recognized(self):
        self.assertOk(self.install("--yes"))
        variant = str(self.sb.remote)[:-len(".git")].upper() + "/"   # no .git, other case, trailing slash
        h.git("-C", str(self.sb.target), "remote", "set-url", "origin", variant)
        result = self.install("--yes")
        self.assertOk(result)
        self.assertIn("is an abc-claude checkout - updating it", result.output)

    def test_unreachable_repo_exits_1_with_sign_in_hint(self):
        missing = self.sb.root / "no-such-repo.git"
        result = self.install("--yes", env=self.sb.env(ABC_CLAUDE_REPO=missing))
        self.assertEqual(result.returncode, 1, result)
        self.assertIn("Cannot reach", result.output)
        self.assertIn("gh auth login", result.output)
        self.assertFalse(self.sb.target.exists())

    def test_run_in_place_from_the_target(self):
        self.assertOk(self.install("--yes"))
        result = self.install("--yes", script_dir=self.sb.target)
        self.assertOk(result)
        self.assertIn("using it in place", result.output)

    def test_run_from_a_fresh_clone_elsewhere_into_missing_target(self):
        clone = self.sb.root / "clone"
        h.git("clone", "-q", str(self.sb.remote), str(clone))
        env = self.sb.env(ABC_CLAUDE_REPO=self.sb.root / "ignored.git")
        result = self.install("--yes", script_dir=clone, env=env)
        self.assertOk(result)
        self.assertIn("Installing from local checkout", result.output)
        self.assertEqual(self.target_git("config", "--get", "remote.origin.url"), str(self.sb.remote))

```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd /e/Dev/projects/abc-claude && python -m unittest tests.test_installer 2>&1 | tail -3`
Expected: `FAILED`. The 33 new runs (11 tests × 3 interpreters) fail, for example because `settings.json` doesn't exist yet or because a foreign-origin run exits 0.

- [ ] **Step 3: Add the sh bootstrap section and main**

In `install.sh`, insert immediately above the `# --- summary ---` banner:
```sh
# --- bootstrap --------------------------------------------------------------
# norm_url URL - compare form: slashes, lowercase, no scheme, user, trailing / or .git.
norm_url() {
    printf '%s\n' "$1" | tr '\\' '/' | tr '[:upper:]' '[:lower:]' | sed \
        -e 's#/*$##' -e 's#\.git$##' -e 's#^[a-z+]*://##' -e 's#^[^/@]*@##' -e 's#^\([^/:]*\):#\1/#'
}

same_url() { [ "$(norm_url "$1")" = "$(norm_url "$2")" ]; }

SIGN_IN_HINT="Sign in first (gh auth login, or Git Credential Manager), then re-run."

# check_remote URL die|warn - git ls-remote access check.
check_remote() {
    if git ls-remote "$1" >/dev/null 2>&1; then return 0; fi
    if [ "$2" = die ]; then die "Cannot reach $1. $SIGN_IN_HINT"; fi
    warn "Cannot reach $1. $SIGN_IN_HINT"
    return 1
}

restore_missing() {
    missing=$(git -C "$TARGET" -c core.quotepath=off ls-files -d) || missing=""
    if [ -z "$missing" ]; then return 0; fi
    count=0
    set -f; old_ifs=$IFS; IFS=$NL
    for f in $missing; do
        git -C "$TARGET" checkout -- "$f" || die "Could not restore $f (a running Claude Code session may hold it open). Close Claude Code and re-run."
        count=$((count + 1))
    done
    IFS=$old_ifs; set +f
    info "Restored $count missing tracked file(s)."
}

update_existing() {
    info "$TARGET is an abc-claude checkout - updating it."
    info "Close running Claude Code sessions first: they can hold files open."
    restore_missing
    if [ -n "$(git -C "$TARGET" status --porcelain --untracked-files=no)" ]; then
        warn "Tracked files in $TARGET have local changes - skipping the pull. Commit or discard them, then re-run."
        return 0
    fi
    if check_remote "$ORIGIN_URL" warn; then
        git -C "$TARGET" pull --ff-only || warn "Could not fast-forward $TARGET; leaving it as-is."
    fi
}

warn_uncommitted_source() {
    if [ -n "$LOCAL_SRC" ] && [ -n "$(git -C "$LOCAL_SRC" status --porcelain)" ]; then
        warn "$LOCAL_SRC has uncommitted edits: they are not carried over. Commit them, then re-run."
    fi
}

clone_fresh() {
    check_remote "$FETCH_URL" die
    info "Cloning into $TARGET."
    git clone -q -c core.autocrlf=false --branch master "$FETCH_URL" "$TARGET" || die "git clone of $FETCH_URL failed."
    if [ "$FETCH_URL" != "$ORIGIN_URL" ]; then
        git -C "$TARGET" remote set-url origin "$ORIGIN_URL"
    fi
}

bootstrap() {
    if [ -n "$LOCAL_SRC" ] && [ -e "$TARGET/.git" ]; then
        target_top=$(git -C "$TARGET" rev-parse --show-toplevel 2>/dev/null) || target_top=""
        if [ -n "$target_top" ] && same_path "$target_top" "$LOCAL_SRC"; then
            info "Running from the checkout at $TARGET - using it in place."
            return 0
        fi
    fi
    if [ -e "$TARGET/.git" ]; then
        origin=$(git -C "$TARGET" config --get remote.origin.url 2>/dev/null) || origin=""
        if [ -n "$origin" ] && same_url "$origin" "$ORIGIN_URL"; then
            update_existing
            return 0
        fi
        die "$TARGET is a git repo with a different origin (${origin:-none}). Move it aside or set CLAUDE_CONFIG_DIR, then re-run. Nothing was changed."
    fi
    if [ -e "$TARGET" ] && [ ! -d "$TARGET" ]; then
        die "$TARGET exists and is not a directory. Move it aside, then re-run."
    fi
    if [ -d "$TARGET" ]; then
        die "$TARGET exists and is not a git repo. Move it aside, then re-run. Nothing was changed."
    fi
    warn_uncommitted_source
    clone_fresh
}

```
Replace the `# --- main ---` block (from the banner to the end of the file) with:
```sh
# --- main -------------------------------------------------------------------
install_deps
find_source
resolve_target
bootstrap
summary
```

- [ ] **Step 4: Add the ps1 bootstrap section and main**

In `install.ps1`, insert immediately above the `# --- summary ---` banner:
```powershell
# --- bootstrap --------------------------------------------------------------
# Compare form of a URL: slashes, lowercase, no scheme, user, trailing / or .git.
function Normalize-Url($u) {
    $s = ([string]$u -replace '\\', '/').ToLowerInvariant()
    $s = $s -replace '/+$', ''
    $s = $s -replace '\.git$', ''
    $s = $s -replace '^[a-z+]*://', ''
    $s = $s -replace '^[^/@]*@', ''
    $s = $s -replace '^([^/:]*):', '$1/'
    return $s
}

$SignInHint = 'Sign in first (gh auth login, or Git Credential Manager), then re-run.'

# git ls-remote access check; -Fatal exits 1, otherwise warns and returns $false.
function Test-Remote($Url, [switch]$Fatal) {
    Invoke-Quiet 'git' @('ls-remote', $Url) | Out-Null
    if ($script:NativeExit -eq 0) { return $true }
    if ($Fatal) { Fail "Cannot reach $Url. $SignInHint" }
    Write-Warn "Cannot reach $Url. $SignInHint"
    return $false
}

function Restore-Missing {
    $T = $script:Target
    $missing = @(Invoke-Quiet 'git' @('-C', $T, '-c', 'core.quotepath=off', 'ls-files', '-d') | Where-Object { $_ })
    if ($missing.Count -eq 0) { return }
    foreach ($f in $missing) {
        if (-not (Invoke-Git @('-C', $T, 'checkout', '--', $f))) {
            Fail "Could not restore $f (a running Claude Code session may hold it open). Close Claude Code and re-run."
        }
    }
    Write-Info "Restored $($missing.Count) missing tracked file(s)."
}

function Update-Existing {
    $T = $script:Target
    Write-Info "$T is an abc-claude checkout - updating it."
    Write-Info 'Close running Claude Code sessions first: they can hold files open.'
    Restore-Missing
    $dirty = @(Invoke-Quiet 'git' @('-C', $T, 'status', '--porcelain', '--untracked-files=no') | Where-Object { $_ })
    if ($dirty.Count -gt 0) {
        Write-Warn "Tracked files in $T have local changes - skipping the pull. Commit or discard them, then re-run."
        return
    }
    if (Test-Remote $script:OriginUrl) {
        if (-not (Invoke-Git @('-C', $T, 'pull', '--ff-only'))) { Write-Warn "Could not fast-forward $T; leaving it as-is." }
    }
}

function Warn-UncommittedSource {
    if (-not $script:LocalSrc) { return }
    $dirty = @(Invoke-Quiet 'git' @('-C', $script:LocalSrc, 'status', '--porcelain') | Where-Object { $_ })
    if ($dirty.Count -gt 0) {
        Write-Warn "$($script:LocalSrc) has uncommitted edits: they are not carried over. Commit them, then re-run."
    }
}

function Invoke-Clone {
    $T = $script:Target
    Test-Remote $script:FetchUrl -Fatal | Out-Null
    Write-Info "Cloning into $T."
    if (-not (Invoke-Git @('clone', '-q', '-c', 'core.autocrlf=false', '--branch', 'master', $script:FetchUrl, $T))) {
        Fail "git clone of $($script:FetchUrl) failed."
    }
    if ($script:FetchUrl -ne $script:OriginUrl) {
        Invoke-Git @('-C', $T, 'remote', 'set-url', 'origin', $script:OriginUrl) | Out-Null
    }
}

function Invoke-Bootstrap {
    $T = $script:Target
    $isRepo = Test-Path -LiteralPath (Join-Path $T '.git')
    if ($script:LocalSrc -and $isRepo) {
        $targetTop = Get-GitLine @('-C', $T, 'rev-parse', '--show-toplevel')
        if ($targetTop -and (Test-SamePath $targetTop $script:LocalSrc)) {
            Write-Info "Running from the checkout at $T - using it in place."
            return
        }
    }
    if ($isRepo) {
        $origin = Get-GitLine @('-C', $T, 'config', '--get', 'remote.origin.url')
        if ($origin -and ((Normalize-Url $origin) -eq (Normalize-Url $script:OriginUrl))) {
            Update-Existing
            return
        }
        if (-not $origin) { $origin = 'none' }
        Fail "$T is a git repo with a different origin ($origin). Move it aside or set CLAUDE_CONFIG_DIR, then re-run. Nothing was changed."
    }
    if (Test-Path -LiteralPath $T -PathType Leaf) { Fail "$T exists and is not a directory. Move it aside, then re-run." }
    if (Test-Path -LiteralPath $T) { Fail "$T exists and is not a git repo. Move it aside, then re-run. Nothing was changed." }
    Warn-UncommittedSource
    Invoke-Clone
}

```
Replace the `# --- main ---` block with:
```powershell
# --- main -------------------------------------------------------------------
Install-Deps
Find-Source
Resolve-Target
Invoke-Bootstrap
Write-Summary
exit 0
```

- [ ] **Step 5: Run the installer tests**

Run: `cd /e/Dev/projects/abc-claude && python -m unittest tests.test_installer 2>&1 | tail -3`
Expected: `Ran 44 tests` and `OK`.

- [ ] **Step 6: Full gate**

Run: `cd /e/Dev/projects/abc-claude && python -m unittest discover -s tests 2>&1 | tail -3` (allow up to 10 minutes)
Expected: `Ran 81 tests` and `OK`.

- [ ] **Step 7: Commit**

Write `.git/TASK_COMMIT_MSG`:
```
Bootstrap ~/.claude: clone, update, restore, refuse foreign repos

Both installers clone a missing target (core.autocrlf false), update an
abc-claude checkout (restore missing tracked files, fast-forward only
when no tracked file is modified), exit 1 on a repo with another or no
origin, run in place from the target itself, and check reachability with
git ls-remote before cloning. Origins compare after normalizing scheme,
case, slashes and a trailing .git.
```
Run: `cd /e/Dev/projects/abc-claude && git add install.sh install.ps1 tests/test_installer.py && git commit -F .git/TASK_COMMIT_MSG`
Expected: the commit succeeds.

---

### Task 6: Bootstrap: adopt an existing ~/.claude, backups, LF rewrite, clone elsewhere (both scripts)

**Files:**
- Modify: `install.sh`, `install.ps1` (new `# --- bootstrap: adopt ---` section plus one replacement in `bootstrap` / `Invoke-Bootstrap`)
- Modify: `tests/test_installer.py`

**Interfaces:**
- Consumes: `check_remote warn_uncommitted_source clone_fresh bootstrap BACKUPS STAMP NL TAB` (sh), and `Test-Remote Warn-UncommittedSource Invoke-Clone Invoke-Bootstrap $script:Backups $Stamp` (ps1), from Tasks 4 and 5.
- Produces. install.sh: `abort_adopt(MESSAGE) backup_conflicts normalize_eol adopt_existing`.
- Produces. install.ps1: `Stop-Adopt($m) Backup-Conflicts Repair-LineEndings Invoke-Adopt`.
- Backups are recorded as target-relative paths (`settings.json.bak.<STAMP>`) in `BACKUPS` (newline-terminated) or `$script:Backups`, and the summary lists them.
- Messages:
  - `Adopting the existing <path>: close running Claude Code sessions first, since they can hold files open.` (a warning)
  - `Rewrote N tracked file(s) with LF line endings.`
  - `Adopted <path>.`
  - `... Nothing was changed.`, printed on a failed adopt.

- [ ] **Step 1: Add the failing tests**

Append to the end of `class InstallerScenarios`, after its last method, with one blank line between methods and two blank lines before `class ShTests`:
```python
    # --- task 6: adopt ------------------------------------------------------
    def seed_existing_target(self):
        """A non-git ~/.claude with a conflict, a CRLF-only difference, junk and a local-only file."""
        t = self.sb.target
        files = {
            "settings.json": b'{"model": "sonnet"}\r\n',          # conflict: differs in content
            "CLAUDE.md": b"# Fixture global rules\r\n",            # differs only in line endings
            "history.jsonl": b'{"junk": 1}\r\n',                   # Claude Code data, ignored
            "projects/p/memory/MEMORY.md": b"memory\r\n",          # ignored
            "skills/synced/x/SKILL.md": b"synced\n",               # ignored
            "rules/mine.md": b"# Local-only rule\n",               # untracked, reported
        }
        h.Sandbox.write_files(t, files)
        return files

    def test_adopt_backs_up_conflicts_keeps_junk_and_reports_local_only(self):
        files = self.seed_existing_target()
        result = self.install("--yes")
        self.assertOk(result)
        t = self.sb.target
        baks = h.backups(t)
        self.assertEqual(len(baks), 1, baks)
        self.assertTrue(re.fullmatch(r"settings\.json\.bak\.\d{14}", baks[0]), baks)
        self.assertEqual((t / baks[0]).read_bytes(), files["settings.json"])
        for rel in ("history.jsonl", "projects/p/memory/MEMORY.md", "skills/synced/x/SKILL.md", "rules/mine.md"):
            self.assertEqual((t / rel).read_bytes(), files[rel], rel)
        for rel in h.tracked_files(t):
            self.assertNotIn(b"\r\n", (t / rel).read_bytes(), rel)
        self.assertEqual((t / "CLAUDE.md").read_bytes(), b"# Fixture global rules\n")
        self.assertIn("Local-only files to review", result.output)
        self.assertIn("    rules/mine.md", result.output)
        self.assertNotIn("history.jsonl", result.output)
        self.assertIn("    " + baks[0], result.output)
        self.assertEqual(self.target_git("status", "--porcelain"), "?? rules/mine.md")
        self.assertEqual(self.target_git("rev-parse", "--abbrev-ref", "@{u}"), "origin/master")

    def test_rerun_after_adopt_makes_no_new_backups(self):
        self.seed_existing_target()
        self.assertOk(self.install("--yes"))
        first = h.backups(self.sb.target)
        self.assertOk(self.install("--yes"))
        self.assertEqual(h.backups(self.sb.target), first)

    def test_run_from_clone_elsewhere_adopts_with_clone_origin(self):
        clone = self.sb.root / "clone"
        h.git("clone", "-q", str(self.sb.remote), str(clone))
        (clone / "CLAUDE.md").write_bytes(b"# Uncommitted edit in the clone\n")
        self.sb.target.mkdir()
        (self.sb.target / "history.jsonl").write_bytes(b"junk\n")
        env = self.sb.env(ABC_CLAUDE_REPO=self.sb.root / "ignored.git")
        result = self.install("--yes", script_dir=clone, env=env)
        self.assertOk(result)
        self.assertIn("has uncommitted edits", result.output)
        self.assertEqual(self.target_git("config", "--get", "remote.origin.url"),
                         h.git("-C", str(clone), "config", "--get", "remote.origin.url").strip())
        self.assertEqual((self.sb.target / "CLAUDE.md").read_bytes(), b"# Fixture global rules\n")
        self.assertEqual((self.sb.target / "history.jsonl").read_bytes(), b"junk\n")

    def test_failed_adopt_leaves_no_git_dir(self):
        self.sb.target.mkdir()
        (self.sb.target / "settings.json").write_bytes(b"{}\n")
        empty = self.sb.root / "empty.git"
        h.git("init", "-q", "--bare", str(empty))
        result = self.install("--yes", env=self.sb.env(ABC_CLAUDE_REPO=empty))
        self.assertEqual(result.returncode, 1, result)
        self.assertIn("Nothing was changed.", result.output)
        self.assertFalse((self.sb.target / ".git").exists())
        self.assertEqual((self.sb.target / "settings.json").read_bytes(), b"{}\n")

```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd /e/Dev/projects/abc-claude && python -m unittest tests.test_installer 2>&1 | tail -3`
Expected: `FAILED`. The adopt tests exit 1 with `exists and is not a git repo`. `test_failed_adopt_leaves_no_git_dir` may already pass through the interim die; that is fine.

- [ ] **Step 3: Add the sh adopt section and switch bootstrap to it**

In `install.sh`, insert immediately above the `# --- summary ---` banner:
```sh
# --- bootstrap: adopt -------------------------------------------------------
# abort_adopt MESSAGE - undo the git init of a failed adopt, then exit 1.
abort_adopt() {
    rm -rf "$TARGET/.git"
    die "$1 Nothing was changed."
}

# backup_conflicts - copy files that differ from the repo (content, not line
# endings) to <path>.bak.<STAMP>.
backup_conflicts() {
    conflicts=$(git -C "$TARGET" -c core.quotepath=off diff --name-only --diff-filter=M 2>/dev/null) \
        || abort_adopt "git diff failed in $TARGET."
    if [ -z "$conflicts" ]; then return 0; fi
    set -f; old_ifs=$IFS; IFS=$NL
    for f in $conflicts; do
        cp -p "$TARGET/$f" "$TARGET/$f.bak.$STAMP" || abort_adopt "Could not back up $f."
        BACKUPS="$BACKUPS$f.bak.$STAMP$NL"
    done
    IFS=$old_ifs; set +f
}

# normalize_eol - rewrite tracked files whose working copy still has CRLF.
normalize_eol() {
    crlf=$(git -C "$TARGET" -c core.quotepath=off ls-files --eol | awk -F "$TAB" '$1 ~ /w\/(crlf|mixed)/ { print $2 }') || crlf=""
    if [ -z "$crlf" ]; then return 0; fi
    count=0
    set -f; old_ifs=$IFS; IFS=$NL
    for f in $crlf; do
        rm -f "$TARGET/$f"
        git -C "$TARGET" checkout -- "$f" || die "Could not rewrite $f (a running Claude Code session may hold it open). Close Claude Code and re-run."
        count=$((count + 1))
    done
    IFS=$old_ifs; set +f
    info "Rewrote $count tracked file(s) with LF line endings."
}

adopt_existing() {
    check_remote "$FETCH_URL" die
    warn "Adopting the existing $TARGET: close running Claude Code sessions first, since they can hold files open."
    git -C "$TARGET" init -q -b master || abort_adopt "git init failed in $TARGET."
    git -C "$TARGET" config core.autocrlf false
    git -C "$TARGET" remote add origin "$FETCH_URL" || abort_adopt "git remote add failed."
    git -C "$TARGET" fetch -q origin || abort_adopt "git fetch from $FETCH_URL failed."
    git -C "$TARGET" reset -q origin/master || abort_adopt "git reset to origin/master failed."
    git -C "$TARGET" branch -q -u origin/master || abort_adopt "git branch -u origin/master failed."
    if [ "$FETCH_URL" != "$ORIGIN_URL" ]; then
        git -C "$TARGET" remote set-url origin "$ORIGIN_URL"
    fi
    backup_conflicts
    git -C "$TARGET" checkout -- . || die "git checkout failed partway (a running Claude Code session may hold a file open). Close Claude Code and re-run: the re-run restores the missing files."
    normalize_eol
    info "Adopted $TARGET."
}

```
In `bootstrap()`, replace these lines:
```sh
    if [ -d "$TARGET" ]; then
        die "$TARGET exists and is not a git repo. Move it aside, then re-run. Nothing was changed."
    fi
    warn_uncommitted_source
    clone_fresh
```
with:
```sh
    warn_uncommitted_source
    if [ -d "$TARGET" ]; then
        adopt_existing
    else
        clone_fresh
    fi
```

- [ ] **Step 4: Add the ps1 adopt section and switch Invoke-Bootstrap to it**

In `install.ps1`, insert immediately above the `# --- summary ---` banner:
```powershell
# --- bootstrap: adopt -------------------------------------------------------
# Undo the git init of a failed adopt, then exit 1.
function Stop-Adopt($m) {
    Remove-Item -LiteralPath (Join-Path $script:Target '.git') -Recurse -Force -ErrorAction SilentlyContinue
    Fail "$m Nothing was changed."
}

# Copy files that differ from the repo (content, not line endings) to <path>.bak.<Stamp>.
function Backup-Conflicts {
    $T = $script:Target
    $conflicts = @(Invoke-Quiet 'git' @('-C', $T, '-c', 'core.quotepath=off', 'diff', '--name-only', '--diff-filter=M') | Where-Object { $_ })
    if ($script:NativeExit -ne 0) { Stop-Adopt "git diff failed in $T." }
    foreach ($f in $conflicts) {
        $src = Join-Path $T $f
        try { Copy-Item -LiteralPath $src -Destination "$src.bak.$Stamp" -Force } catch { Stop-Adopt "Could not back up $f." }
        $script:Backups += "$f.bak.$Stamp"
    }
}

# Rewrite tracked files whose working copy still has CRLF.
function Repair-LineEndings {
    $T = $script:Target
    $lines = @(Invoke-Quiet 'git' @('-C', $T, '-c', 'core.quotepath=off', 'ls-files', '--eol'))
    $crlf = @(foreach ($l in $lines) {
        $parts = ([string]$l) -split "`t", 2
        if ($parts.Count -eq 2 -and $parts[0] -match 'w/(crlf|mixed)') { $parts[1] }
    })
    if ($crlf.Count -eq 0) { return }
    foreach ($f in $crlf) {
        Remove-Item -LiteralPath (Join-Path $T $f) -Force
        if (-not (Invoke-Git @('-C', $T, 'checkout', '--', $f))) {
            Fail "Could not rewrite $f (a running Claude Code session may hold it open). Close Claude Code and re-run."
        }
    }
    Write-Info "Rewrote $($crlf.Count) tracked file(s) with LF line endings."
}

function Invoke-Adopt {
    $T = $script:Target
    Test-Remote $script:FetchUrl -Fatal | Out-Null
    Write-Warn "Adopting the existing ${T}: close running Claude Code sessions first, since they can hold files open."
    if (-not (Invoke-Git @('-C', $T, 'init', '-q', '-b', 'master'))) { Stop-Adopt "git init failed in $T." }
    Invoke-Git @('-C', $T, 'config', 'core.autocrlf', 'false') | Out-Null
    if (-not (Invoke-Git @('-C', $T, 'remote', 'add', 'origin', $script:FetchUrl))) { Stop-Adopt 'git remote add failed.' }
    if (-not (Invoke-Git @('-C', $T, 'fetch', '-q', 'origin'))) { Stop-Adopt "git fetch from $($script:FetchUrl) failed." }
    if (-not (Invoke-Git @('-C', $T, 'reset', '-q', 'origin/master'))) { Stop-Adopt 'git reset to origin/master failed.' }
    if (-not (Invoke-Git @('-C', $T, 'branch', '-q', '-u', 'origin/master'))) { Stop-Adopt 'git branch -u origin/master failed.' }
    if ($script:FetchUrl -ne $script:OriginUrl) {
        Invoke-Git @('-C', $T, 'remote', 'set-url', 'origin', $script:OriginUrl) | Out-Null
    }
    Backup-Conflicts
    if (-not (Invoke-Git @('-C', $T, 'checkout', '--', '.'))) {
        Fail 'git checkout failed partway (a running Claude Code session may hold a file open). Close Claude Code and re-run: the re-run restores the missing files.'
    }
    Repair-LineEndings
    Write-Info "Adopted $T."
}

```
In `Invoke-Bootstrap`, replace these lines:
```powershell
    if (Test-Path -LiteralPath $T) { Fail "$T exists and is not a git repo. Move it aside, then re-run. Nothing was changed." }
    Warn-UncommittedSource
    Invoke-Clone
```
with:
```powershell
    Warn-UncommittedSource
    if (Test-Path -LiteralPath $T) { Invoke-Adopt } else { Invoke-Clone }
```

- [ ] **Step 5: Run the installer tests**

Run: `cd /e/Dev/projects/abc-claude && python -m unittest tests.test_installer 2>&1 | tail -3`
Expected: `Ran 56 tests` and `OK`.

- [ ] **Step 6: Full gate**

Run: `cd /e/Dev/projects/abc-claude && python -m unittest discover -s tests 2>&1 | tail -3`
Expected: `Ran 93 tests` and `OK`.

- [ ] **Step 7: Commit**

Write `.git/TASK_COMMIT_MSG`:
```
Adopt an existing ~/.claude in place

git init, fetch, reset origin/master and track it, back up every file
whose content differs from the repo to <path>.bak.<stamp>, check out the
tracked files, and rewrite any tracked file still carrying CRLF. A failed
adopt before checkout removes the .git it created. Run from a clone
elsewhere, the installer fetches from the clone and sets origin to the
clone's origin. Local-only files are listed for review.
```
Run: `cd /e/Dev/projects/abc-claude && git add install.sh install.ps1 tests/test_installer.py && git commit -F .git/TASK_COMMIT_MSG`
Expected: the commit succeeds.

---

### Task 7: Machine-local: rules/local.md prompts and git identity (both scripts)

**Files:**
- Modify: `install.sh`, `install.ps1` (new `# --- machine-local ---` section plus the main block)
- Modify: `tests/test_installer.py`

**Interfaces:**
- Consumes: `ask TARGET info warn` (sh), `Ask Get-GitLine Invoke-Git $script:Target` (ps1), and `templates/local.md` with the `{{MACHINE_LINE}}` and `{{ADMIN_LINE}}` lines (Task 1).
- Produces. install.sh: `NO_ADMIN_LINE setup_local_md setup_identity`. install.ps1: `$NoAdminLine Initialize-LocalMd Set-RepoIdentity`.
- Prompt order (important for piped input): `Machine label`, then `Admin rights or Developer Mode available? (y/n)`. Both run only when `rules/local.md` is created. Then `Git user.name for this repo` and `Git user.email for this repo`, each only when that value is unset in the repo's own config.
- Output lines: `This machine: <label>.` and, on an answer starting with n/N, `No admin rights or Developer Mode here: don't suggest elevated installs, symlinks or HKLM edits.` The file is UTF-8 without BOM and LF.
- Messages:
  - `Created rules/local.md.`
  - `rules/local.md exists - leaving it unchanged.`
  - `templates/local.md is missing - skipping rules/local.md.`
  - `Git identity for this repo: <name> <<email>>.`
  - `Git identity for this repo is not set: run git -C "<path>" config user.name "Your Name" and git -C "<path>" config user.email you@example.com.`

- [ ] **Step 1: Add the failing tests**

Append to the end of `class InstallerScenarios`, after its last method, with one blank line between methods and two blank lines before `class ShTests`:
```python
    # --- task 7: machine-local ----------------------------------------------
    def test_local_md_created_from_template_under_yes(self):
        self.assertOk(self.install("--yes"))
        text = (self.sb.target / "rules" / "local.md").read_bytes().decode("utf-8")
        self.assertNotIn("\r", text)
        self.assertNotIn("{{", text)
        self.assertRegex(text, r"(?m)^This machine: \S.*\.$")
        self.assertNotIn("No admin rights", text)
        self.assertEqual(self.target_git("status", "--porcelain"), "")

    def test_local_md_prompts_use_answers(self):
        result = self.install(stdin="Work laptop\nn\n")
        self.assertOk(result)
        text = (self.sb.target / "rules" / "local.md").read_text(encoding="utf-8")
        self.assertIn("This machine: Work laptop.\n", text)
        self.assertIn("No admin rights or Developer Mode here: don't suggest elevated installs, symlinks "
                      "or HKLM edits.\n", text)

    def test_existing_local_md_is_unchanged(self):
        self.sb.target.mkdir()
        h.Sandbox.write_files(self.sb.target, {"rules/local.md": b"# Mine\r\nkeep me\r\n"})
        result = self.install("--yes")
        self.assertOk(result)
        self.assertEqual((self.sb.target / "rules" / "local.md").read_bytes(), b"# Mine\r\nkeep me\r\n")
        self.assertIn("rules/local.md exists - leaving it unchanged.", result.output)

    def test_git_identity_from_global_config(self):
        result = self.install("--yes")
        self.assertOk(result)
        self.assertEqual(self.target_git("config", "--local", "user.name"), "Test User")
        self.assertEqual(self.target_git("config", "--local", "user.email"), "test@example.com")
        self.assertIn("Git identity for this repo: Test User <test@example.com>.", result.output)

    def test_git_identity_left_unset_with_warning(self):
        self.sb.set_global_identity(None, None)
        result = self.install("--yes")
        self.assertOk(result)
        self.assertIn("Git identity for this repo is not set", result.output)
        local = h.git("-C", str(self.sb.target), "config", "--local", "--get", "user.name", check=False)
        self.assertEqual(local.strip(), "")

```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd /e/Dev/projects/abc-claude && python -m unittest tests.test_installer 2>&1 | tail -3`
Expected: `FAILED`, with the 15 new runs failing (`rules/local.md` is missing, identity messages are absent).

- [ ] **Step 3: Add the sh machine-local section and main**

In `install.sh`, insert immediately above the `# --- summary ---` banner:
```sh
# --- machine-local ----------------------------------------------------------
NO_ADMIN_LINE="No admin rights or Developer Mode here: don't suggest elevated installs, symlinks or HKLM edits."

setup_local_md() {
    local_md="$TARGET/rules/local.md"
    template="$TARGET/templates/local.md"
    if [ -e "$local_md" ]; then
        info "rules/local.md exists - leaving it unchanged."
        return 0
    fi
    if [ ! -f "$template" ]; then
        warn "templates/local.md is missing - skipping rules/local.md."
        return 0
    fi
    host=$(hostname 2>/dev/null | tr -d '\r') || host=""
    if [ -z "$host" ]; then host=$(uname -n); fi
    label=$(ask "Machine label" "$host")
    admin=$(ask "Admin rights or Developer Mode available? (y/n)" "y")
    admin_line=""
    case "$admin" in
        [Nn]*) admin_line=$NO_ADMIN_LINE ;;
    esac
    mkdir -p "$TARGET/rules"
    awk -v m="This machine: $label." -v a="$admin_line" '
        $0 == "{{MACHINE_LINE}}" { print m; next }
        $0 == "{{ADMIN_LINE}}" { if (a != "") print a; next }
        { print }' "$template" > "$local_md"
    info "Created rules/local.md."
}

setup_identity() {
    name=$(git -C "$TARGET" config --local --get user.name 2>/dev/null) || name=""
    email=$(git -C "$TARGET" config --local --get user.email 2>/dev/null) || email=""
    if [ -n "$name" ] && [ -n "$email" ]; then
        info "Git identity for this repo: $name <$email>."
        return 0
    fi
    if [ -z "$name" ]; then
        global_name=$(git config --global --get user.name 2>/dev/null) || global_name=""
        name=$(ask "Git user.name for this repo" "$global_name")
    fi
    if [ -z "$email" ]; then
        global_email=$(git config --global --get user.email 2>/dev/null) || global_email=""
        email=$(ask "Git user.email for this repo" "$global_email")
    fi
    if [ -n "$name" ]; then git -C "$TARGET" config user.name "$name"; fi
    if [ -n "$email" ]; then git -C "$TARGET" config user.email "$email"; fi
    if [ -n "$name" ] && [ -n "$email" ]; then
        info "Git identity for this repo: $name <$email>."
    else
        warn "Git identity for this repo is not set: run git -C \"$TARGET\" config user.name \"Your Name\" and git -C \"$TARGET\" config user.email you@example.com."
    fi
}

```
Replace the `# --- main ---` block with:
```sh
# --- main -------------------------------------------------------------------
install_deps
find_source
resolve_target
bootstrap
setup_local_md
setup_identity
summary
```

- [ ] **Step 4: Add the ps1 machine-local section and main**

In `install.ps1`, insert immediately above the `# --- summary ---` banner:
```powershell
# --- machine-local ----------------------------------------------------------
$NoAdminLine = "No admin rights or Developer Mode here: don't suggest elevated installs, symlinks or HKLM edits."

function Initialize-LocalMd {
    $localMd = Join-Path $script:Target 'rules\local.md'
    $template = Join-Path $script:Target 'templates\local.md'
    if (Test-Path -LiteralPath $localMd) { Write-Info 'rules/local.md exists - leaving it unchanged.'; return }
    if (-not (Test-Path -LiteralPath $template)) { Write-Warn 'templates/local.md is missing - skipping rules/local.md.'; return }
    $label = Ask 'Machine label' ([System.Net.Dns]::GetHostName())
    $admin = Ask 'Admin rights or Developer Mode available? (y/n)' 'y'
    $adminLine = if ($admin -match '^[Nn]') { $NoAdminLine } else { '' }
    $out = @(foreach ($line in (Get-Content -LiteralPath $template -Encoding UTF8)) {
        if ($line -eq '{{MACHINE_LINE}}') { "This machine: $label." }
        elseif ($line -eq '{{ADMIN_LINE}}') { if ($adminLine) { $adminLine } }
        else { $line }
    })
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $localMd) | Out-Null
    [System.IO.File]::WriteAllText($localMd, (($out -join "`n") + "`n"), (New-Object System.Text.UTF8Encoding($false)))
    Write-Info 'Created rules/local.md.'
}

function Set-RepoIdentity {
    $T = $script:Target
    $name = Get-GitLine @('-C', $T, 'config', '--local', '--get', 'user.name')
    $email = Get-GitLine @('-C', $T, 'config', '--local', '--get', 'user.email')
    if ($name -and $email) { Write-Info "Git identity for this repo: $name <$email>."; return }
    if (-not $name) { $name = Ask 'Git user.name for this repo' (Get-GitLine @('config', '--global', '--get', 'user.name')) }
    if (-not $email) { $email = Ask 'Git user.email for this repo' (Get-GitLine @('config', '--global', '--get', 'user.email')) }
    if ($name) { Invoke-Git @('-C', $T, 'config', 'user.name', $name) | Out-Null }
    if ($email) { Invoke-Git @('-C', $T, 'config', 'user.email', $email) | Out-Null }
    if ($name -and $email) {
        Write-Info "Git identity for this repo: $name <$email>."
    } else {
        Write-Warn "Git identity for this repo is not set: run git -C `"$T`" config user.name `"Your Name`" and git -C `"$T`" config user.email you@example.com."
    }
}

```
Replace the `# --- main ---` block with:
```powershell
# --- main -------------------------------------------------------------------
Install-Deps
Find-Source
Resolve-Target
Invoke-Bootstrap
Initialize-LocalMd
Set-RepoIdentity
Write-Summary
exit 0
```

- [ ] **Step 5: Run the installer tests**

Run: `cd /e/Dev/projects/abc-claude && python -m unittest tests.test_installer 2>&1 | tail -3`
Expected: `Ran 71 tests` and `OK`.

- [ ] **Step 6: Full gate**

Run: `cd /e/Dev/projects/abc-claude && python -m unittest discover -s tests 2>&1 | tail -3`
Expected: `Ran 108 tests` and `OK`.

- [ ] **Step 7: Commit**

Write `.git/TASK_COMMIT_MSG`:
```
Create rules/local.md and set the repo's git identity

When rules/local.md is missing, prompt for a machine label (default: the
hostname) and whether admin rights or Developer Mode are available, and
fill templates/local.md; an existing file is never touched. Prompt for
user.name and user.email when the repo-local identity is unset, with the
global values as defaults; under --yes use them, or warn when unset.
```
Run: `cd /e/Dev/projects/abc-claude && git add install.sh install.ps1 tests/test_installer.py && git commit -F .git/TASK_COMMIT_MSG`
Expected: the commit succeeds.

---

### Task 8: Checks: managed-settings report and python resolution (both scripts)

**Files:**
- Modify: `install.sh`, `install.ps1` (new `# --- checks ---` section plus the main block)
- Modify: `tests/test_installer.py`

**Interfaces:**
- Consumes: `PY posix_path have info warn NL` (sh), `Get-GitLine Invoke-Quiet` (ps1), and `ABC_CLAUDE_MANAGED_PATHS` (`;`-separated).
- Produces. install.sh: `MANAGED_KEYS_PY managed_paths report_managed check_python`. install.ps1: `$ManagedKeys Get-ManagedPaths Write-ManagedReport Find-GitBash Test-PythonFromBash`.
- Keys reported, in this order when present: `allowManagedHooksOnly, allowManagedPermissionRulesOnly, disableAllHooks, hooks, statusLine, enabledPlugins, extraKnownMarketplaces, strictKnownMarketplaces, blockedMarketplaces`.
- Default paths:
  - Windows: `C:\Program Files\ClaudeCode\managed-settings.json`
  - macOS: `/Library/Application Support/ClaudeCode/managed-settings.json`
  - Linux: `/etc/claude-code/managed-settings.json`
- Messages:
  - `Managed settings found: <path as given>` (a warning)
  - `Managed keys that affect this config: <k1>, <k2>` (a warning)
  - `No managed keys that affect this config.`
  - `Could not parse <path> as JSON.`
  - `No managed settings found.`
  - `python resolves: Python X.Y.Z.`
  - The python3-only warning, `Only python3 is on PATH, but the hooks and the statusline run 'python'. Fix: install python-is-python3 (Debian/Ubuntu) or put a 'python' link to python3 on PATH.`
  - `python is not on PATH: the hooks and the statusline need it.`
  - `Git Bash not found: cannot check that python resolves from bash.` (ps1 only)

- [ ] **Step 1: Add the failing tests**

Append to the end of `class InstallerScenarios`, after its last method, with one blank line between methods and two blank lines before `class ShTests`:
```python
    # --- task 8: checks -----------------------------------------------------
    def test_no_managed_settings(self):
        result = self.install("--yes")
        self.assertIn("No managed settings found.", result.output)
        self.assertIn("python resolves: Python ", result.output)

    def test_managed_settings_reported_with_affected_keys(self):
        managed = self.sb.root / "managed-settings.json"
        managed.write_text(json.dumps({"companyAnnouncements": ["hi"], "statusLine": {"type": "command"},
                                       "allowManagedHooksOnly": True}), encoding="utf-8")
        paths = str(self.sb.no_managed) + ";" + str(managed)
        result = self.install("--yes", env=self.sb.env(ABC_CLAUDE_MANAGED_PATHS=paths))
        self.assertOk(result)
        self.assertIn("Managed settings found: " + str(managed), result.output)
        self.assertIn("Managed keys that affect this config: allowManagedHooksOnly, statusLine\n", result.output)
        self.assertNotIn("companyAnnouncements", result.output)

    def test_unparseable_managed_settings_is_a_warning(self):
        managed = self.sb.root / "managed-settings.json"
        managed.write_text("{not json", encoding="utf-8")
        result = self.install("--yes", env=self.sb.env(ABC_CLAUDE_MANAGED_PATHS=managed))
        self.assertOk(result)
        self.assertIn("Could not parse " + str(managed) + " as JSON.", result.output)

```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd /e/Dev/projects/abc-claude && python -m unittest tests.test_installer 2>&1 | tail -3`
Expected: `FAILED`, with the 9 new runs failing on missing messages.

- [ ] **Step 3: Add the sh checks section and main**

In `install.sh`, insert immediately above the `# --- summary ---` banner:
```sh
# --- checks -----------------------------------------------------------------
MANAGED_KEYS_PY='
import json, sys
KEYS = ("allowManagedHooksOnly", "allowManagedPermissionRulesOnly", "disableAllHooks", "hooks",
        "statusLine", "enabledPlugins", "extraKnownMarketplaces", "strictKnownMarketplaces",
        "blockedMarketplaces")
try:
    with open(sys.argv[1], encoding="utf-8-sig") as f:
        data = json.load(f)
except Exception:
    sys.stdout.buffer.write(b"!unreadable\n")
    sys.exit(0)
found = [k for k in KEYS if isinstance(data, dict) and k in data]
sys.stdout.buffer.write((", ".join(found) + "\n").encode("utf-8"))
'

managed_paths() {
    if [ -n "${ABC_CLAUDE_MANAGED_PATHS:-}" ]; then
        printf '%s\n' "$ABC_CLAUDE_MANAGED_PATHS" | tr ';' '\n'
    elif [ "$IS_WINDOWS" = 1 ]; then
        printf '%s\n' 'C:\Program Files\ClaudeCode\managed-settings.json'
    elif [ "$(uname -s)" = Darwin ]; then
        printf '%s\n' '/Library/Application Support/ClaudeCode/managed-settings.json'
    else
        printf '%s\n' '/etc/claude-code/managed-settings.json'
    fi
}

report_managed() {
    paths=$(managed_paths)
    found=0
    set -f; old_ifs=$IFS; IFS=$NL
    for managed in $paths; do
        if [ -z "$managed" ]; then continue; fi
        managed_file=$(posix_path "$managed")
        if [ ! -f "$managed_file" ]; then continue; fi
        found=1
        warn "Managed settings found: $managed"
        keys=$("$PY" -c "$MANAGED_KEYS_PY" "$managed_file") || keys="!unreadable"
        if [ "$keys" = "!unreadable" ]; then
            warn "Could not parse $managed as JSON."
        elif [ -n "$keys" ]; then
            warn "Managed keys that affect this config: $keys"
        else
            info "No managed keys that affect this config."
        fi
    done
    IFS=$old_ifs; set +f
    if [ "$found" = 0 ]; then info "No managed settings found."; fi
}

check_python() {
    if have python && python --version >/dev/null 2>&1; then
        info "python resolves: $(python --version 2>&1 | tr -d '\r')."
    elif have python3; then
        warn "Only python3 is on PATH, but the hooks and the statusline run 'python'. Fix: install python-is-python3 (Debian/Ubuntu) or put a 'python' link to python3 on PATH."
    else
        warn "python is not on PATH: the hooks and the statusline need it."
    fi
}

```
Replace the `# --- main ---` block with:
```sh
# --- main -------------------------------------------------------------------
install_deps
find_source
resolve_target
bootstrap
setup_local_md
setup_identity
report_managed
check_python
summary
```

- [ ] **Step 4: Add the ps1 checks section and main**

In `install.ps1`, insert immediately above the `# --- summary ---` banner:
```powershell
# --- checks -----------------------------------------------------------------
$ManagedKeys = @('allowManagedHooksOnly', 'allowManagedPermissionRulesOnly', 'disableAllHooks', 'hooks',
                 'statusLine', 'enabledPlugins', 'extraKnownMarketplaces', 'strictKnownMarketplaces',
                 'blockedMarketplaces')

function Get-ManagedPaths {
    if ($env:ABC_CLAUDE_MANAGED_PATHS) { return @($env:ABC_CLAUDE_MANAGED_PATHS -split ';' | Where-Object { $_ }) }
    return @('C:\Program Files\ClaudeCode\managed-settings.json')
}

function Write-ManagedReport {
    $found = $false
    foreach ($managed in (Get-ManagedPaths)) {
        if (-not (Test-Path -LiteralPath $managed -PathType Leaf)) { continue }
        $found = $true
        Write-Warn "Managed settings found: $managed"
        try {
            $data = Get-Content -LiteralPath $managed -Raw -Encoding UTF8 | ConvertFrom-Json
        } catch {
            Write-Warn "Could not parse $managed as JSON."
            continue
        }
        $names = @()
        if ($data) { $names = @($data.PSObject.Properties | ForEach-Object { $_.Name }) }
        $keys = @($ManagedKeys | Where-Object { $names -contains $_ })
        if ($keys.Count -gt 0) { Write-Warn "Managed keys that affect this config: $($keys -join ', ')" }
        else { Write-Info 'No managed keys that affect this config.' }
    }
    if (-not $found) { Write-Info 'No managed settings found.' }
}

# Git for Windows' bin\bash.exe, found from `git --exec-path`, or $null.
function Find-GitBash {
    $exec = Get-GitLine @('--exec-path')
    if (-not $exec) { return $null }
    $root = Split-Path (Split-Path (Split-Path $exec -Parent) -Parent) -Parent
    $bash = Join-Path $root 'bin\bash.exe'
    if (Test-Path -LiteralPath $bash) { return $bash }
    return $null
}

function Test-PythonFromBash {
    $bash = Find-GitBash
    if (-not $bash) { Write-Warn 'Git Bash not found: cannot check that python resolves from bash.'; return }
    $version = @(Invoke-Quiet $bash @('-c', 'python --version 2>&1') | Where-Object { $_ })
    if ($script:NativeExit -eq 0 -and $version.Count -gt 0) {
        Write-Info "python resolves: $(([string]$version[0]).Trim())."
        return
    }
    Invoke-Quiet $bash @('-c', 'command -v python3') | Out-Null
    if ($script:NativeExit -eq 0) {
        Write-Warn "Only python3 is on PATH, but the hooks and the statusline run 'python'. Fix: install python-is-python3 (Debian/Ubuntu) or put a 'python' link to python3 on PATH."
    } else {
        Write-Warn 'python is not on PATH: the hooks and the statusline need it.'
    }
}

```
Replace the `# --- main ---` block with:
```powershell
# --- main -------------------------------------------------------------------
Install-Deps
Find-Source
Resolve-Target
Invoke-Bootstrap
Initialize-LocalMd
Set-RepoIdentity
Write-ManagedReport
Test-PythonFromBash
Write-Summary
exit 0
```

- [ ] **Step 5: Run the installer tests**

Run: `cd /e/Dev/projects/abc-claude && python -m unittest tests.test_installer 2>&1 | tail -3`
Expected: `Ran 80 tests` and `OK`.

- [ ] **Step 6: Full gate**

Run: `cd /e/Dev/projects/abc-claude && python -m unittest discover -s tests 2>&1 | tail -3`
Expected: `Ran 117 tests` and `OK`.

- [ ] **Step 7: Commit**

Write `.git/TASK_COMMIT_MSG`:
```
Report managed settings and check that python resolves

Check the platform's managed-settings path (or ABC_CLAUDE_MANAGED_PATHS
in tests), report each file found and the keys in it that affect this
config, and warn on unparseable JSON. Confirm that `python` runs from
bash (Git Bash on Windows) and suggest a fix when only python3 exists.
Report only; nothing is changed.
```
Run: `cd /e/Dev/projects/abc-claude && git add install.sh install.ps1 tests/test_installer.py && git commit -F .git/TASK_COMMIT_MSG`
Expected: the commit succeeds.

---

### Task 9: Plugin sync and prune (both scripts)

**Files:**
- Modify: `install.sh`, `install.ps1` (new `# --- plugin sync ---` section plus the main block)
- Modify: `tests/test_installer.py`

**Interfaces:**
- Consumes: `PY TARGET YES PRUNE STAMP TAB NL ask have info warn` (sh), `Invoke-Quiet Ask Have $Yes $Prune $script:Target` (ps1), and the fake claude and fixture manifest (Task 3).
- Produces. install.sh: `PLUGINS_PY` (modes `manifest FILE`, and `ids` / `names` reading CLI JSON on stdin) and `plugin_sync`. install.ps1: `Get-ClaudeJson([string[]])` and `Invoke-PluginSync`.
- CLI calls, exactly:
  - `claude plugin list --json`
  - `claude plugin marketplace list --json`
  - `claude plugin marketplace add <source>`
  - `claude plugin install <name@marketplace>`
  - `claude plugin uninstall <id>`
- Source mapping from `extraKnownMarketplaces.<name>.source`: `github` uses `repo`; `git` and `url` use `url`; `directory` and `file` use `path`. Any other type is a warning. `claude-plugins-official` is built in and is never added.
- Messages:
  - `claude is not on PATH - skipping plugin sync.`
  - `Already present: <items>` (comma-separated; marketplaces as `marketplace <name>`, first)
  - `Installed: <items>`
  - `Failed: <items>` (a warning)
  - `Could not install <id>.`
  - `Restored settings.json after the claude CLI changed it: the tracked file is the plugin manifest.`
  - `Installed but not in settings.json: <id>`
  - `Uninstalled <id>.`
  - `Kept <id>.`
  - `Non-interactive run: prune only reports.`
  - `Re-run with --prune (-Prune) to choose which of these to uninstall.`
- Prune prompt: `Uninstall <id> (installed but not in settings.json)? (y/n)`, default `n`.

- [ ] **Step 1: Add the failing tests**

Append to the end of `class InstallerScenarios`, after its last method, with one blank line between methods and two blank lines before `class ShTests`:
```python
    # --- task 9: plugin sync ------------------------------------------------
    def test_plugin_sync_installs_only_missing(self):
        self.sb.fake_claude(plugins=["alpha@claude-plugins-official"])
        result = self.install("--yes")
        self.assertOk(result)
        calls = self.sb.claude_calls()
        self.assertIn("plugin list --json", calls)
        self.assertIn("plugin marketplace list --json", calls)
        self.assertIn("plugin marketplace add example/extra-market", calls)
        self.assertIn("plugin install beta@extra-market", calls)
        self.assertNotIn("plugin install alpha@claude-plugins-official", calls)
        self.assertIn("Installed: marketplace extra-market, beta@extra-market", result.output)
        self.assertIn("Already present: alpha@claude-plugins-official", result.output)
        # The fake CLI rewrote settings.json on install; the installer restores the tracked copy.
        self.assertIn("Restored settings.json", result.output)
        self.assertEqual(self.target_git("status", "--porcelain"), "")

    def test_plugin_sync_second_run_installs_nothing(self):
        self.sb.fake_claude(plugins=["alpha@claude-plugins-official"])
        self.assertOk(self.install("--yes"))
        first = len(self.sb.claude_calls())
        result = self.install("--yes")
        self.assertOk(result)
        second = self.sb.claude_calls()[first:]
        self.assertEqual([c for c in second if not c.endswith("--json")], [], second)
        self.assertIn("Already present: marketplace extra-market, alpha@claude-plugins-official, beta@extra-market",
                      result.output)

    def test_plugin_install_failure_is_a_warning(self):
        self.sb.fake_claude(plugins=["alpha@claude-plugins-official"], fail=["beta@extra-market"])
        result = self.install("--yes")
        self.assertOk(result)
        self.assertIn("Could not install beta@extra-market.", result.output)
        self.assertIn("Failed: beta@extra-market", result.output)

    def test_prune_only_reports_under_yes(self):
        self.sb.fake_claude(plugins=["alpha@claude-plugins-official", "beta@extra-market",
                                     "gamma@claude-plugins-official"], marketplaces=["claude-plugins-official", "extra-market"])
        result = self.install("--yes", "--prune")
        self.assertOk(result)
        self.assertIn("Installed but not in settings.json: gamma@claude-plugins-official", result.output)
        self.assertIn("Non-interactive run: prune only reports.", result.output)
        self.assertFalse(any(c.startswith("plugin uninstall") for c in self.sb.claude_calls()))

    def test_prune_interactive_uninstalls_after_yes(self):
        self.sb.fake_claude(plugins=["alpha@claude-plugins-official", "beta@extra-market",
                                     "gamma@claude-plugins-official"], marketplaces=["claude-plugins-official", "extra-market"])
        self.assertOk(self.install("--yes"))
        result = self.install("--prune", stdin="y\n")
        self.assertOk(result)
        self.assertIn("plugin uninstall gamma@claude-plugins-official", self.sb.claude_calls())
        self.assertIn("Uninstalled gamma@claude-plugins-official.", result.output)
        self.assertNotIn("gamma@claude-plugins-official", self.sb.claude_plugins())

    def test_claude_missing_skips_plugin_sync(self):
        result = self.install("--yes")
        self.assertOk(result)
        self.assertIn("claude is not on PATH - skipping plugin sync.", result.output)

```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd /e/Dev/projects/abc-claude && python -m unittest tests.test_installer 2>&1 | tail -3`
Expected: `FAILED`, with the 18 new runs failing (no claude calls are logged and the messages are missing).

- [ ] **Step 3: Add the sh plugin-sync section and main**

In `install.sh`, insert immediately above the `# --- summary ---` banner:
```sh
# --- plugin sync ------------------------------------------------------------
# manifest: "P<TAB>id" per enabledPlugins key, "M<TAB>name<TAB>source" per
# extraKnownMarketplaces entry. ids/names: one per line from CLI JSON on stdin.
PLUGINS_PY='
import json, sys
mode = sys.argv[1]
if mode == "manifest":
    with open(sys.argv[2], encoding="utf-8-sig") as f:
        settings = json.load(f)
    lines = ["P\t" + pid for pid in (settings.get("enabledPlugins") or {})]
    for name, entry in (settings.get("extraKnownMarketplaces") or {}).items():
        src = (entry or {}).get("source") or {}
        arg = {"github": src.get("repo"), "git": src.get("url"), "url": src.get("url"),
               "directory": src.get("path"), "file": src.get("path")}.get(src.get("source")) or ""
        lines.append("M\t" + name + "\t" + arg)
else:
    key = "id" if mode == "ids" else "name"
    lines = [str(item[key]) for item in json.load(sys.stdin.buffer) if isinstance(item, dict) and item.get(key)]
sys.stdout.buffer.write("".join(line + "\n" for line in lines).encode("utf-8"))
'

plugin_sync() {
    if ! have claude; then
        warn "claude is not on PATH - skipping plugin sync."
        return 0
    fi
    settings="$TARGET/settings.json"
    if [ ! -f "$settings" ]; then
        warn "settings.json is missing - skipping plugin sync."
        return 0
    fi
    manifest=$("$PY" -c "$PLUGINS_PY" manifest "$settings") || {
        warn "Could not read the plugin manifest from settings.json - skipping plugin sync."
        return 0
    }
    installed=$(claude plugin list --json </dev/null | "$PY" -c "$PLUGINS_PY" ids) || {
        warn "Could not list installed plugins (claude plugin list --json) - skipping plugin sync."
        return 0
    }
    known=$(claude plugin marketplace list --json </dev/null | "$PY" -c "$PLUGINS_PY" names) || {
        warn "Could not list marketplaces (claude plugin marketplace list --json) - skipping plugin sync."
        return 0
    }
    snapshot=$(mktemp)
    cp "$settings" "$snapshot"
    present=""
    added=""
    failed=""
    set -f; old_ifs=$IFS; IFS=$NL
    for line in $manifest; do
        kind=${line%%"$TAB"*}
        if [ "$kind" != M ]; then continue; fi
        rest=${line#*"$TAB"}
        name=${rest%%"$TAB"*}
        source=${rest#*"$TAB"}
        if printf '%s\n' "$known" | grep -Fqx -- "$name" || [ "$name" = claude-plugins-official ]; then
            present="$present, marketplace $name"
        elif [ -z "$source" ]; then
            warn "Marketplace $name has a source type the installer can't add: add it with claude plugin marketplace add."
            failed="$failed, marketplace $name"
        elif claude plugin marketplace add "$source" </dev/null; then
            added="$added, marketplace $name"
        else
            warn "Could not add marketplace $name ($source)."
            failed="$failed, marketplace $name"
        fi
    done
    for line in $manifest; do
        kind=${line%%"$TAB"*}
        if [ "$kind" != P ]; then continue; fi
        pid=${line#*"$TAB"}
        if printf '%s\n' "$installed" | grep -Fqx -- "$pid"; then
            present="$present, $pid"
        elif claude plugin install "$pid" </dev/null; then
            added="$added, $pid"
        else
            warn "Could not install $pid."
            failed="$failed, $pid"
        fi
    done
    extras=""
    for pid in $installed; do
        if ! printf '%s\n' "$manifest" | grep -Fqx -- "P$TAB$pid"; then extras="$extras$pid$NL"; fi
    done
    IFS=$old_ifs; set +f
    if ! cmp -s "$snapshot" "$settings"; then
        cp "$snapshot" "$settings"
        info "Restored settings.json after the claude CLI changed it: the tracked file is the plugin manifest."
    fi
    rm -f "$snapshot"
    if [ -n "$present" ]; then info "Already present: ${present#, }"; fi
    if [ -n "$added" ]; then info "Installed: ${added#, }"; fi
    if [ -n "$failed" ]; then warn "Failed: ${failed#, }"; fi
    if [ -z "$extras" ]; then return 0; fi
    set -f; IFS=$NL
    for pid in $extras; do
        if [ "$PRUNE" = 1 ] && [ "$YES" = 0 ]; then
            answer=$(ask "Uninstall $pid (installed but not in settings.json)? (y/n)" "n")
            case "$answer" in
                [Yy]*)
                    if claude plugin uninstall "$pid" </dev/null; then info "Uninstalled $pid."; else warn "Could not uninstall $pid."; fi ;;
                *) info "Kept $pid." ;;
            esac
        else
            info "Installed but not in settings.json: $pid"
        fi
    done
    IFS=$old_ifs; set +f
    if [ "$PRUNE" = 1 ] && [ "$YES" = 1 ]; then
        info "Non-interactive run: prune only reports."
    elif [ "$PRUNE" = 0 ]; then
        info "Re-run with --prune (-Prune) to choose which of these to uninstall."
    fi
}

```
Replace the `# --- main ---` block with:
```sh
# --- main -------------------------------------------------------------------
install_deps
find_source
resolve_target
bootstrap
setup_local_md
setup_identity
report_managed
check_python
plugin_sync
summary
```

- [ ] **Step 4: Add the ps1 plugin-sync section and main**

In `install.ps1`, insert immediately above the `# --- summary ---` banner:
```powershell
# --- plugin sync ------------------------------------------------------------
# Parsed JSON from a `claude ... --json` command, or $null when it fails.
function Get-ClaudeJson([string[]]$ArgList) {
    $raw = @(Invoke-Quiet 'claude' $ArgList)
    if ($script:NativeExit -ne 0) { return $null }
    # Assign before wrapping: in 5.1 ConvertFrom-Json emits a JSON array as one
    # object, so @(... | ConvertFrom-Json) would nest it.
    try { $parsed = ($raw -join "`n") | ConvertFrom-Json } catch { return $null }
    return ,@($parsed)
}

function Invoke-PluginSync {
    if (-not (Have 'claude')) { Write-Warn 'claude is not on PATH - skipping plugin sync.'; return }
    $settingsPath = Join-Path $script:Target 'settings.json'
    if (-not (Test-Path -LiteralPath $settingsPath)) { Write-Warn 'settings.json is missing - skipping plugin sync.'; return }
    try {
        $settings = Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        Write-Warn 'Could not read the plugin manifest from settings.json - skipping plugin sync.'
        return
    }
    $wanted = @()
    if ($settings.enabledPlugins) { $wanted = @($settings.enabledPlugins.PSObject.Properties | ForEach-Object { $_.Name }) }
    $markets = @()
    if ($settings.extraKnownMarketplaces) {
        foreach ($p in $settings.extraKnownMarketplaces.PSObject.Properties) {
            $src = $p.Value.source
            $arg = ''
            if ($src) {
                switch ([string]$src.source) {
                    'github' { $arg = [string]$src.repo }
                    'git' { $arg = [string]$src.url }
                    'url' { $arg = [string]$src.url }
                    'directory' { $arg = [string]$src.path }
                    'file' { $arg = [string]$src.path }
                }
            }
            $markets += [pscustomobject]@{ Name = $p.Name; Source = $arg }
        }
    }
    $pluginJson = Get-ClaudeJson @('plugin', 'list', '--json')
    if ($null -eq $pluginJson) { Write-Warn 'Could not list installed plugins (claude plugin list --json) - skipping plugin sync.'; return }
    $installed = @(foreach ($i in $pluginJson) { if ($i.id) { [string]$i.id } })
    $marketJson = Get-ClaudeJson @('plugin', 'marketplace', 'list', '--json')
    if ($null -eq $marketJson) { Write-Warn 'Could not list marketplaces (claude plugin marketplace list --json) - skipping plugin sync.'; return }
    $known = @(foreach ($i in $marketJson) { if ($i.name) { [string]$i.name } })

    $before = [System.IO.File]::ReadAllBytes($settingsPath)
    $present = @()
    $added = @()
    $failed = @()
    foreach ($m in $markets) {
        if (($known -contains $m.Name) -or $m.Name -eq 'claude-plugins-official') {
            $present += "marketplace $($m.Name)"
        } elseif (-not $m.Source) {
            Write-Warn "Marketplace $($m.Name) has a source type the installer can't add: add it with claude plugin marketplace add."
            $failed += "marketplace $($m.Name)"
        } else {
            & claude plugin marketplace add $m.Source | Out-Host
            if ($LASTEXITCODE -eq 0) { $added += "marketplace $($m.Name)" }
            else { Write-Warn "Could not add marketplace $($m.Name) ($($m.Source))."; $failed += "marketplace $($m.Name)" }
        }
    }
    foreach ($id in $wanted) {
        if ($installed -contains $id) { $present += $id; continue }
        & claude plugin install $id | Out-Host
        if ($LASTEXITCODE -eq 0) { $added += $id } else { Write-Warn "Could not install $id."; $failed += $id }
    }
    $after = [System.IO.File]::ReadAllBytes($settingsPath)
    if ([Convert]::ToBase64String($before) -ne [Convert]::ToBase64String($after)) {
        [System.IO.File]::WriteAllBytes($settingsPath, $before)
        Write-Info 'Restored settings.json after the claude CLI changed it: the tracked file is the plugin manifest.'
    }
    if ($present.Count -gt 0) { Write-Info "Already present: $($present -join ', ')" }
    if ($added.Count -gt 0) { Write-Info "Installed: $($added -join ', ')" }
    if ($failed.Count -gt 0) { Write-Warn "Failed: $($failed -join ', ')" }

    $extras = @($installed | Where-Object { $wanted -notcontains $_ })
    if ($extras.Count -eq 0) { return }
    foreach ($id in $extras) {
        if ($Prune -and -not $Yes) {
            $answer = Ask "Uninstall $id (installed but not in settings.json)? (y/n)" 'n'
            if ($answer -match '^[Yy]') {
                & claude plugin uninstall $id | Out-Host
                if ($LASTEXITCODE -eq 0) { Write-Info "Uninstalled $id." } else { Write-Warn "Could not uninstall $id." }
            } else {
                Write-Info "Kept $id."
            }
        } else {
            Write-Info "Installed but not in settings.json: $id"
        }
    }
    if ($Prune -and $Yes) { Write-Info 'Non-interactive run: prune only reports.' }
    elseif (-not $Prune) { Write-Info 'Re-run with --prune (-Prune) to choose which of these to uninstall.' }
}

```
Replace the `# --- main ---` block with:
```powershell
# --- main -------------------------------------------------------------------
Install-Deps
Find-Source
Resolve-Target
Invoke-Bootstrap
Initialize-LocalMd
Set-RepoIdentity
Write-ManagedReport
Test-PythonFromBash
Invoke-PluginSync
Write-Summary
exit 0
```

- [ ] **Step 5: Run the installer tests**

Run: `cd /e/Dev/projects/abc-claude && python -m unittest tests.test_installer 2>&1 | tail -3`
Expected: `Ran 98 tests` and `OK`.

- [ ] **Step 6: Full gate**

Run: `cd /e/Dev/projects/abc-claude && python -m unittest discover -s tests 2>&1 | tail -3` (about 4 minutes)
Expected: `Ran 135 tests` and `OK`.

- [ ] **Step 7: Commit**

Write `.git/TASK_COMMIT_MSG`:
```
Sync plugins and marketplaces from settings.json

Add each marketplace in extraKnownMarketplaces that claude doesn't know
yet and install each enabledPlugins entry that isn't installed, true or
false alike, checking the CLI's JSON lists before every step. Failures
are warnings. settings.json is restored byte for byte if the CLI rewrote
it. Installed plugins missing from the manifest are reported; --prune
asks before uninstalling each, and only reports under --yes.
```
Run: `cd /e/Dev/projects/abc-claude && git add install.sh install.ps1 tests/test_installer.py && git commit -F .git/TASK_COMMIT_MSG`
Expected: the commit succeeds.

---

### Task 10: README and GitHub Actions CI

**Files:**
- Modify: `README.md` (replace the whole file)
- Create: `.github/workflows/tests.yml`

**Interfaces:**
- Consumes: the flags, environment variables and messages from Tasks 4-9, and the test command.
- Produces: user documentation and a CI job named `tests (<os>)` on `windows-latest` and `ubuntu-latest`.

- [ ] **Step 1: Write the README**

`E:\Dev\projects\abc-claude\README.md`:
````markdown
# abc-claude

Claude Code user configuration (`~/.claude`) with idempotent installers for Windows, Linux and macOS. It holds the global rules, skills, agents, commands, hooks, statusline, settings and plugin set, so every machine runs the same setup.

The repo *is* `~/.claude`. A whitelist `.gitignore` tracks only the shared config. What Claude Code owns there (`projects/`, `plugins/`, sessions, history, credentials) stays untracked, and the installer never touches it. Edit and push from one machine; pull on the others.

> **Before you run it:** the installer installs missing packages (git, python) and turns `~/.claude` into a git working tree. It backs up every file it replaces first, to `<path>.bak.<yyyyMMddHHmmss>`. Close running Claude Code sessions before you start: they can hold files open.

## Install

The repo is private, so raw download URLs need a sign-in. There are two routes.

### Download and inspect

Windows (PowerShell):

```powershell
gh api repos/aaronbcarlisle/abc-claude/contents/install.ps1 -H "Accept: application/vnd.github.raw" > install.ps1
notepad install.ps1
powershell -ExecutionPolicy Bypass -File install.ps1
```

Linux, macOS or Git Bash. Fetch `install.sh` from a POSIX shell, so its bytes are saved unchanged:

```sh
gh api repos/aaronbcarlisle/abc-claude/contents/install.sh -H "Accept: application/vnd.github.raw" > install.sh
less install.sh
sh install.sh
```

### Clone first

```sh
gh repo clone aaronbcarlisle/abc-claude ~/src/abc-claude
sh ~/src/abc-claude/install.sh
```

On Windows, run `powershell -ExecutionPolicy Bypass -File <dir>\install.ps1` instead. The installer adopts the clone's committed work into `~/.claude` and sets `origin` to the clone's `origin`. Uncommitted edits in the clone aren't carried over. Delete the clone afterwards.

## What it does

1. **Dependencies:** installs git and python if they're missing. On Windows it tries winget (`--scope user`), then choco, then scoop. Elsewhere it uses the first of apt-get, dnf, yum, pacman, zypper, apk, brew and port that it finds. It never needs admin. `claude` and `gh` are optional.
2. **Source:** the checkout it runs from, if that's an abc-claude checkout; otherwise `ABC_CLAUDE_REPO`.
3. **Bootstrap** `~/.claude` (or `CLAUDE_CONFIG_DIR`), depending on what's there:
   - **Nothing:** clones the repo.
   - **An abc-claude checkout:** restores missing tracked files, then runs `git pull --ff-only` if no tracked file is modified.
   - **A git repo with another origin:** exits 1 and changes nothing.
   - **A folder that isn't a git repo:** adopts it in place. Files that differ from the repo are backed up, then the tracked files are written. Files that exist only locally are listed, so you can commit or delete them.
4. **Machine-local:** creates the untracked `rules/local.md` from `templates/local.md`, after asking for a machine label and whether admin rights or Developer Mode are available. It also sets this repo's git `user.name` and `user.email`.
5. **Checks:** reports any managed (policy) settings file and the keys in it that affect this config. Checks that `python` resolves from bash.
6. **Plugin sync:** adds the marketplaces in `extraKnownMarketplaces` and installs each plugin in `enabledPlugins` that's missing. That includes plugins set to `false`, since `settings.json` keeps their on/off state. Plugins that are installed but not listed are reported.
7. **Summary:** the backups made, local-only files and the warning count.

Re-running is safe: on a clean checkout it changes nothing.

## Flags and environment

| sh | PowerShell | Effect |
|---|---|---|
| `--yes` | `-Yes` | Non-interactive: take every default. |
| `--prune` | `-Prune` | For each installed plugin that isn't in `enabledPlugins`, ask whether to uninstall it. Under `--yes` it only reports. |
| `--help` | `-Help` | Print usage. |

| Variable | Default | Meaning |
|---|---|---|
| `ABC_CLAUDE_REPO` | `https://github.com/aaronbcarlisle/abc-claude.git` | Repo to clone or fetch from when not run from a checkout. |
| `CLAUDE_CONFIG_DIR` | `~/.claude` | Target directory. The tracked `settings.json` commands assume `~/.claude`, so the installer warns when this points elsewhere. |
| `ABC_CLAUDE_MANAGED_PATHS` | the platform's policy path | Test only: `;`-separated managed-settings paths to check. |

The exit code is 1 on a fatal error and 0 otherwise, including runs with warnings.

## Day to day

- `git status` in `~/.claude` shows only meaningful changes. `/config`, `/model` and `/plugin` edit `settings.json`; commit those changes or discard them.
- Notes that hold only on one machine go in `rules/local.md`, which is untracked and loads automatically. Facts that hold everywhere go in `rules/` or `CLAUDE.md`.
- Auto-memory (`projects/*/memory/`) stays per machine.
- Secrets never go in tracked files.

## Tests

```sh
python -m unittest discover -s tests
```

The suite covers:
- the hook and the statusline
- a portability lint: no home paths or token-like strings in tracked files
- the installer scenarios. Each one runs `install.sh` (Git Bash on Windows, `sh` elsewhere) and `install.ps1` (Windows PowerShell 5.1 and pwsh, on Windows) against a temp bare repo, a temp `CLAUDE_CONFIG_DIR` and a fake `claude`, with no network. Missing interpreters are skipped.

GitHub Actions runs the suite on `windows-latest` and `ubuntu-latest` for every PR.
````

- [ ] **Step 2: Write the CI workflow**

`E:\Dev\projects\abc-claude\.github\workflows\tests.yml`:
```yaml
name: tests

on:
  pull_request:
  push:
    branches: [master]

jobs:
  test:
    name: tests (${{ matrix.os }})
    strategy:
      fail-fast: false
      matrix:
        os: [windows-latest, ubuntu-latest]
    runs-on: ${{ matrix.os }}
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Show interpreters
        shell: bash
        run: |
          git --version
          python --version
          command -v sh bash pwsh powershell || true
      - name: Run the test suite
        run: python -m unittest discover -s tests -v
```

- [ ] **Step 3: Check the workflow parses and the lint still passes**

Run: `cd /e/Dev/projects/abc-claude && python -c "t=open('.github/workflows/tests.yml',encoding='utf-8').read(); assert '\t' not in t and '\r' not in t; print('ok')" && python -m unittest tests.test_portability 2>&1 | tail -2`
Expected: `ok`, then `OK`.

- [ ] **Step 4: Full gate**

Run: `cd /e/Dev/projects/abc-claude && python -m unittest discover -s tests 2>&1 | tail -3`
Expected: `Ran 135 tests` and `OK`.

- [ ] **Step 5: Commit**

Write `.git/TASK_COMMIT_MSG`:
```
Document the installers and run the suite in CI

The README covers the download-and-inspect and clone-first routes, what
each installer step does, flags, environment variables and tests. GitHub
Actions runs python -m unittest discover -s tests on windows-latest and
ubuntu-latest for every PR and every push to master.
```
Run: `cd /e/Dev/projects/abc-claude && git add README.md .github/workflows/tests.yml && git ls-files --eol README.md .github/workflows/tests.yml && git commit -F .git/TASK_COMMIT_MSG`
Expected: `i/lf    w/lf` for both, then the commit succeeds.

---

## Controller (not a task)

After Task 10:
- Run the whole suite once more.
- Push `feature/initial-setup` and open the PR to `master`, with no attribution lines. CI must pass on both OSes.

## Owner follow-up after merge (manual gates)

1. On this machine, run `sh <clone>/install.sh` (or `install.ps1`) against the live `~/.claude`, which takes the adopt path. Expected backups include `rules/windows.md`, plus any other live file whose content differs from the repo. `hooks/test_context_budget.py` is listed as local-only, so delete it.
2. Add these two lines to this machine's `rules/local.md` (they left `rules/windows.md`):
   - `- Python here runs with UTF-8 mode off: `subprocess.run(..., text=True)` decodes as cp1252. Pass `encoding="utf-8"` when capturing CLI output (e.g. `gh` JSON).`
   - `` - `core.longpaths` is set globally (since 2026-09-26). If `git worktree add` still fails with "Filename too long" (a tool that ignores it), note the branch was created before checkout: remove the orphaned branch with `git branch -D` and use a shorter worktree base path. ``
3. Start Claude Code and check that the statusline and the context-budget hook still work.
4. Do the first real run on the work machine.
5. Delete or archive the working clone at `E:\Dev\projects\abc-claude`.

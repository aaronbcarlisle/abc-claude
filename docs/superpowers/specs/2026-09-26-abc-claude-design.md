# abc-claude design

Date: 2026-09-26. Status: design approved in conversation, section by section; spec awaiting owner review.

## 1. Goal and constraints

**Outcome.** One private repo, `aaronbcarlisle/abc-claude`, holds the owner's Claude Code user configuration. Every machine runs the same workflow: the same global rules, skills, agents, commands, hooks, statusline, settings and plugin set. Changes flow both ways: edit and push from home or work, pull on the other.

**Owner requirements**
- A private repo, as generic as possible.
- Install-time prompts for values that really are machine-specific.
- Mirrors abc-vim (`aaronbcarlisle/abc-vim`, default branch `master`):
  - idempotent `install.sh` and `install.ps1`
  - download-and-inspect usage
  - installs from the local checkout when run from one
  - backs up anything replaced to `<path>.bak.<yyyyMMddHHmmss>`
  - an `ABC_CLAUDE_REPO` override
  - installs dependencies through whatever package manager is available

**Constraints**
- The work machine runs Windows with no admin rights and no Developer Mode, so there are no symlinks.
- Sync is two-way. Copying files out of a checkout would break that, which is why the repo *is* `~/.claude`.
- Every machine that uses the repo has Git installed. On Windows, that means Git for Windows, which provides Git Bash.

**Success criteria**
- On a fresh or existing machine, one installer run leaves `~/.claude` as a clean working tree of the repo. Nothing it replaces is lost; it's backed up. Untracked Claude Code data (`projects/`, `plugins/`, history, credentials) is never touched.
- Hooks and the statusline work unedited on every machine.
- `git status` in `~/.claude` shows only meaningful changes.
- The sh and ps1 installers pass the same scenario suite.

**Non-goals**
- Syncing auto-memory (`projects/*/memory/`), sessions, history or plugin caches.
- A settings layering or merge system.
- Secret management. There are no secrets today; see section 5.

## 2. Key decisions

| Decision | Why |
|---|---|
| The repo is `~/.claude`, with a whitelist `.gitignore` | No symlinks at work. It keeps two-way sync and matches abc-vim |
| Skills are tracked with a deny-list (`synced/` and `.trash/` ignored), not a per-skill whitelist | A new skill syncs automatically. If a new managed folder appears, it shows as untracked; nothing is silently skipped |
| `settings.json` is tracked directly, with `$HOME` paths | Hook and statusline commands run in Git Bash on Windows, and `~`/`$HOME` expand there (verified live, launching from both PowerShell and Git Bash). Claude Code has no user-level settings layering, and the owner expects differences between machines to be rare |
| `settings.json` `enabledPlugins` / `extraKnownMarketplaces` is the plugin manifest | It's already tracked, and it changes when plugins are toggled |
| frontend-design, playwright and pr-review-toolkit are dropped | Disabled and unused. They're uninstalled here and removed from `enabledPlugins` before the first commit |
| Machine-local notes go in an untracked `rules/local.md` | `rules/*.md` auto-loads, so no `@import` or import approval is needed |
| The repo is private | It's pushed to from work, so work-derived content must never be published. Going public later is easy; going back to private is not |
| No `--dry-run` | It would duplicate every step. Backups and download-and-inspect already cover the first run |

## 3. Repo layout and `.gitignore`

Tracked content, at the repo root (`~/.claude`):

```
.gitignore  .gitattributes  README.md  install.sh  install.ps1
CLAUDE.md   settings.json   status_line.py
agents/  commands/  hooks/  rules/  skills/  templates/  tests/  docs/
.github/workflows/
```

`.gitignore`:

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

`.gitattributes`: `* text=auto eol=lf`.
- The installer sets `core.autocrlf false` in the repo, because the Git for Windows system config on the owner's machine sets it to `true`.
- `install.ps1` is ASCII-only, so Windows PowerShell 5.1 reads it correctly without a BOM.

Deliberately untracked:
- everything Claude Code owns: `projects/`, `plugins/`, `state/`, `backups/`, `sessions/`, `history.jsonl`, `.credentials.json`, `settings.local.json`, caches, etc.
- `_archive-*`
- one-off audit outputs such as `project-checklists-*.md`
- the `.bak.*` backups

## 4. Bootstrap

**Target directory:** `$CLAUDE_CONFIG_DIR` if set, otherwise `~/.claude`. If `CLAUDE_CONFIG_DIR` is set to anything other than `~/.claude`, the installer warns that the tracked `settings.json` commands assume `~/.claude`.

**Local source:** the installer resolves its own directory and runs `git rev-parse --show-toplevel`. That counts as an abc-claude checkout only if it contains the marker files `install.sh`, `install.ps1` and `status_line.py`.

The installer then handles five states:

| Target state | Action |
|---|---|
| The local source *is* the target (case-insensitive full-path compare on Windows) | Use it in place and go to the post-steps |
| Git repo whose `origin` is the abc-claude URL (compared after normalizing a trailing `.git` and the scheme) | Restore missing tracked files (`git ls-files -d`, then `git checkout --` on those). Then `git pull --ff-only` if the tree is clean; if tracked files are modified, warn and skip the pull. A failed pull is a warning |
| Git repo with any other `origin`, or none | Exit 1 with an explanation. Change nothing |
| Exists but isn't a git repo | The adopt sequence (below) |
| Doesn't exist | `git clone` into the target, then set `core.autocrlf false` |

**Adopt sequence** (tested from Git Bash and pwsh):
1. `git init -b master`, `git config core.autocrlf false`, `git remote add origin <url>`, `git fetch origin`.
2. `git reset origin/master` changes HEAD and the index only, not the working files. Then `git branch -u origin/master`.
3. `git diff -z --name-only --diff-filter=M` lists files that exist locally with different content. Git compares content through the eol filters, so files that differ only in line endings don't count. Each one is copied to `<path>.bak.<yyyyMMddHHmmss>`, and all backups in a run share one timestamp.
4. `git checkout -- .` writes the tracked files.
5. The installer reports the backups, and lists `git ls-files -o --exclude-standard` as local-only files to review: commit them or delete them.

**Run from a clone elsewhere:** the adopt sequence fetches from that clone's path, then sets `origin` to the clone's own `origin` URL. The clone's committed work comes along; its uncommitted edits don't, and the installer warns if there are any.

**Locked files:** if checkout fails partway, the tree is left with missing tracked files. Re-running recovers through state 2. Before bootstrapping, the installer warns you to close running Claude Code sessions.

**Avoid:** `rev:path` arguments such as `origin/master:.gitattributes`. Git Bash rewrites them into Windows paths.

## 5. Machine-local overrides and prompts

- **`rules/local.md`:** untracked and auto-loaded. The installer creates it from `templates/local.md` only when it's missing, and never overwrites it.
- **Prompts** (skipped under `--yes`, where defaults apply):
  1. Machine label (default: the computer's hostname): written as `This machine: <label>.`
  2. Admin rights or Developer Mode available (default yes)? If no, the installer writes: `No admin rights or Developer Mode here: don't suggest elevated installs, symlinks or HKLM edits.`
  3. Git identity for this repo: `user.name` and `user.email`, stored in the repo's config. The defaults come from the global Git config. This prompt runs only when the repo-local identity is unset. Under `--yes`, the global values are used. If those are also unset, the installer leaves the identity unset and warns.
- Prompts 1 and 2 run only when `rules/local.md` is being created, so re-runs don't prompt.
- **Seeding change on the owner's machine:** move the machine-specific lines out of `rules/windows.md` and into that machine's `rules/local.md`. Those are "Python runs with UTF-8 mode off" and "`core.longpaths` set globally since 2026-09-26". `windows.md` keeps only notes that hold on any Windows machine.
- **Reachability:** before cloning or fetching, `git ls-remote <url>` checks access. On failure, the installer prints sign-in guidance (`gh auth login`, or Git Credential Manager) and exits 1.
- **Secrets:** none today and none built. The rule is that secrets never go in tracked files. The first time one is needed, it gets a prompt that stores it as a user-scope environment variable.
- **Auto-memory:** stays per machine. Facts that matter on every machine belong in tracked `rules/` or `CLAUDE.md`.

## 6. Settings

- `settings.json` is tracked as it is, after two changes:
  - The two commands become `python "$HOME/.claude/status_line.py"` and `python "$HOME/.claude/hooks/context_budget.py"`.
  - The three dropped plugins are removed from `enabledPlugins`.
- Changes Claude Code writes itself, from `/config`, `/model` and `/plugin`, show up as diffs. The owner commits or discards them.
- **Managed-settings report:** the installer checks the known policy locations:
  - Windows: `C:\Program Files\ClaudeCode\managed-settings.json`
  - macOS: `/Library/Application Support/ClaudeCode/managed-settings.json`
  - Linux: `/etc/claude-code/managed-settings.json`

  When one exists, the installer reports it and names any keys that affect this config, such as `allowManagedHooksOnly`, `allowManagedPermissionRulesOnly`, plugin or marketplace restrictions, and `statusLine`. It only reports. The list of paths can be overridden with the test-only variable `ABC_CLAUDE_MANAGED_PATHS`.
- **Python check:** the installer confirms that `python` resolves from bash (Git Bash on Windows). If only `python3` exists, it warns and suggests a fix, e.g. `python-is-python3` on Debian or Ubuntu.

## 7. Plugin sync

The manifest is `enabledPlugins` (`"name@marketplace": true|false`) plus `extraKnownMarketplaces` in `settings.json`. Every step checks before acting, rather than relying on the CLI to be safe to re-run:

1. Missing marketplaces are added with `claude plugin marketplace add <source>`. The official marketplace is built in.
2. Missing plugins are installed with `claude plugin install <name>@<marketplace>`, including those marked `false`. `settings.json` already holds their on/off state.
3. A summary lists what was already present, what was installed and what failed. Failures are warnings.
4. Installed plugins that aren't in `enabledPlugins` are always reported. With `--prune` / `-Prune`, the installer asks about each and uninstalls only after a yes. Under `--yes`, prune only reports.
5. If `claude` isn't on `PATH`, the step is skipped with a warning.

**To verify during planning:**
- the machine-readable output of `claude plugin list` and `claude plugin marketplace list`, e.g. a `--json` flag
- how the CLI behaves on re-install and re-add
- whether `extraKnownMarketplaces` triggers any automatic install

## 8. Installers

`install.sh` (POSIX sh, `set -eu`) and `install.ps1` (`#Requires -Version 5`, no pwsh-only syntax) mirror each other section by section:
- the same `# --- section ---` banners and the same messages
- output prefixed `[abc-claude]`, in green (info), yellow (warn) or red (error); sh sends warnings and errors to stderr
- the same order:

1. **Dependencies:**
   - git and python are required; `claude` and `gh` are optional.
   - Windows tries each available package manager in turn (winget with `--scope user`, then choco, then scoop) and refreshes `PATH` after each. On Linux and macOS, the first manager found wins (apt-get, dnf, yum, pacman, zypper, apk, brew, port), with sudo only when needed.
   - The installer never requires admin.
2. **Find the source** (section 4).
3. **Bootstrap** (section 4).
4. **Machine-local:** `rules/local.md` and git identity (section 5).
5. **Checks:** the managed-settings report and `python` resolution (section 6).
6. **Plugin sync** (section 7).
7. **Summary:** backups made, local-only files and warnings.

**Flags:**
- `--yes` / `-Yes`: non-interactive.
- `--prune` / `-Prune`: offer to uninstall plugins that aren't in the manifest.

**Environment:**
- `ABC_CLAUDE_REPO` (default `https://github.com/aaronbcarlisle/abc-claude.git`)
- `CLAUDE_CONFIG_DIR` (sets the target)
- `ABC_CLAUDE_MANAGED_PATHS` (test only)

**Exit codes:** 1 for a fatal error, 0 otherwise, including runs with warnings.

**README usage.** The repo is private, so raw URLs need a sign-in. Two routes:
- **Download and inspect:**
  1. `gh api repos/aaronbcarlisle/abc-claude/contents/install.ps1 -H "Accept: application/vnd.github.raw" > install.ps1`
  2. Review the file.
  3. Run `powershell -ExecutionPolicy Bypass -File install.ps1`. For sh, fetch `install.sh` the same way and run `sh install.sh`.
- **Clone first:** `gh repo clone aaronbcarlisle/abc-claude <dir>`, then run the installer from the clone. It adopts into `~/.claude`.

The README also warns that the installer installs packages and changes `~/.claude`, and lists the flags and variables.

## 9. Testing

- **One command:** `python -m unittest discover -s tests`. `hooks/test_context_budget.py` moves to `tests/`, since it was written for the old `hooks/` location. The existing 26 tests (18 hook + 8 statusline) must still pass.
- **Installer scenario suite:** a single Python test module runs each scenario against `install.sh` under bash, and against `install.ps1` under Windows PowerShell 5.1 and pwsh, whichever are present. Missing interpreters are skipped. Each scenario uses a temp local bare repo as `ABC_CLAUDE_REPO`, a temp `CLAUDE_CONFIG_DIR` and `--yes`, with no network.

| Scenario | Assertions |
|---|---|
| Fresh (target missing) | clone; tracked files present; exit 0 |
| Adopt non-empty, with conflicts, junk and a local-only file | conflicting files backed up with their original content; junk byte-identical; local-only file reported; tracked files LF |
| Re-run on a clean repo | no-op, exit 0, no new backups |
| Re-run with local edits | pull skipped with a warning; edits intact |
| Missing tracked files | restored |
| Foreign `origin` | exit 1; nothing changed |
| Run from a clone elsewhere | adopted; `origin` equals the clone's `origin` URL |
| Existing `rules/local.md` | unchanged |
| Managed settings present (via `ABC_CLAUDE_MANAGED_PATHS`) | reported, including affected keys |

- **Plugin stubs:** a fake `claude` (an sh script plus `claude.cmd`, first on `PATH`) logs its calls and returns canned list output. The tests assert that only missing plugins are installed, that a second run installs nothing, and that prune only reports under `--yes`.
- **Portability lint:** a test scans the tracked files (`git ls-files`) and fails on absolute home paths (`C:/Users/`, `C:\Users\`, `/home/<name>`, `/Users/<name>`) and on token-like strings. The files under `docs/superpowers/` and the test fixtures are exempt.
- **CI:** GitHub Actions on `windows-latest` and `ubuntu-latest` runs the suite on every PR.
- **Manual gates (owner):**
  1. After merge, adopt on the owner's machine and check that the statusline and hooks still work.
  2. The first real run on the work machine.

## 10. Build and seeding approach

- Implementation happens in a working clone at `E:\Dev\projects\abc-claude` on the `feature/initial-setup` branch, merged through a PR. The live `~/.claude` isn't touched until merge.
- The seed content is copied from this machine's live `~/.claude`:
  - `CLAUDE.md`, `rules/`, `agents/`, `commands/`, `hooks/`, `tests/`, `skills/handoff/`, `skills/resume/` and `status_line.py`
  - `settings.json`, with the changes in section 6
- Before the first `settings.json` commit, uninstall the three dropped plugins here.
- After merge, the owner runs the installer against the live `~/.claude` (the adopt path), then deletes or archives the working clone.

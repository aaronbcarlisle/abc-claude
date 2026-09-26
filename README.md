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

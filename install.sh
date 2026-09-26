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

# check_source_master - exit 1 unless the local clone's master holds install.sh
# and .gitignore: clone and adopt install that branch, not the checked-out one.
check_source_master() {
    listed=$(git -C "$LOCAL_SRC" ls-tree --name-only refs/heads/master -- install.sh .gitignore 2>/dev/null) || listed=""
    if ! printf '%s\n' "$listed" | grep -Fqx install.sh || ! printf '%s\n' "$listed" | grep -Fqx .gitignore; then
        die "The clone at $LOCAL_SRC has no usable master branch (install.sh or .gitignore missing). In the clone, run: git checkout master, then git pull --ff-only."
    fi
}

# warn_source_branch - warn when the local clone's master is behind its
# origin/master, or when the clone has another branch checked out.
warn_source_branch() {
    if git -C "$LOCAL_SRC" rev-parse --verify -q refs/remotes/origin/master >/dev/null 2>&1; then
        behind=$(git -C "$LOCAL_SRC" rev-list --count refs/heads/master..refs/remotes/origin/master 2>/dev/null) || behind=0
        if [ "${behind:-0}" -gt 0 ]; then
            warn "The clone's master is behind its origin/master: installing the older commit. Run git pull --ff-only in the clone for the latest."
        fi
    fi
    branch=$(git -C "$LOCAL_SRC" symbolic-ref -q --short HEAD 2>/dev/null) || branch=""
    if [ -z "$branch" ]; then branch="a detached HEAD"; fi
    if [ "$branch" != master ]; then
        warn "The clone is on $branch, not master: only master is installed."
    fi
}

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
        check_source_master
        FETCH_URL=$LOCAL_SRC
        ORIGIN_URL=$(git -C "$LOCAL_SRC" config --get remote.origin.url 2>/dev/null) || ORIGIN_URL=""
        if [ -z "$ORIGIN_URL" ]; then
            warn "$LOCAL_SRC has no origin remote - using $REPO_URL as origin."
            ORIGIN_URL=$REPO_URL
        fi
        info "Installing from local checkout $LOCAL_SRC (origin $ORIGIN_URL)."
        warn_source_branch
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
    if check_remote "$origin" warn; then
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
        git -C "$TARGET" remote set-url origin "$ORIGIN_URL" || die "git remote set-url failed."
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
            if ! git -C "$TARGET" rev-parse --verify -q HEAD >/dev/null 2>&1 && [ -z "$(git -C "$TARGET" for-each-ref refs/heads)" ]; then
                warn "Found an unfinished adopt in $TARGET: starting it again."
                rm -rf "$TARGET/.git"
                adopt_existing
                return 0
            fi
            update_existing
            return 0
        fi
        die "$TARGET is a git repo with a different origin (${origin:-none}). Move it aside or set CLAUDE_CONFIG_DIR, then re-run. Nothing was changed."
    fi
    if [ -e "$TARGET" ] && [ ! -d "$TARGET" ]; then
        die "$TARGET exists and is not a directory. Move it aside, then re-run."
    fi
    warn_uncommitted_source
    if [ -d "$TARGET" ]; then
        adopt_existing
    else
        clone_fresh
    fi
}

# --- bootstrap: adopt -------------------------------------------------------
# abort_adopt MESSAGE - undo the git init of a failed adopt, then exit 1.
abort_adopt() {
    rm -rf "$TARGET/.git"
    die "$1 Nothing was changed."
}

# backup_conflicts - copy files that differ from the repo (content, not line
# endings) to <path>.bak.<STAMP>.
backup_conflicts() {
    conflicts=$(git -C "$TARGET" -c core.quotepath=off diff --name-only --diff-filter=MT 2>/dev/null) \
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
    git -C "$TARGET" config core.autocrlf false || abort_adopt "git config failed in $TARGET."
    git -C "$TARGET" remote add origin "$FETCH_URL" || abort_adopt "git remote add failed."
    git -C "$TARGET" fetch -q origin || abort_adopt "git fetch from $FETCH_URL failed."
    # A missing origin/master is left to the reset below, which reports it.
    if gitignore=$(git -C "$TARGET" ls-tree --name-only origin/master -- .gitignore 2>/dev/null) && [ -z "$gitignore" ]; then
        abort_adopt "The fetched master has no .gitignore."
    fi
    git -C "$TARGET" reset -q origin/master || abort_adopt "git reset to origin/master failed."
    git -C "$TARGET" branch -q -u origin/master || abort_adopt "git branch -u origin/master failed."
    if [ "$FETCH_URL" != "$ORIGIN_URL" ]; then
        git -C "$TARGET" remote set-url origin "$ORIGIN_URL" || abort_adopt "git remote set-url failed."
    fi
    backup_conflicts
    git -C "$TARGET" checkout -- . || die "git checkout failed partway (a running Claude Code session may hold a file open). Close Claude Code and re-run: the re-run restores the missing files."
    normalize_eol
    info "Adopted $TARGET."
}

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
    if [ -n "$present" ]; then info "Already present: ${present#, }"; fi
    if [ -n "$added" ]; then info "Installed: ${added#, }"; fi
    if [ -n "$failed" ]; then warn "Failed: ${failed#, }"; fi
    if [ -n "$extras" ]; then
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
    fi
    # After prune too: claude plugin uninstall may rewrite settings.json as well.
    if ! cmp -s "$snapshot" "$settings"; then
        cp "$snapshot" "$settings"
        info "Restored settings.json after the claude CLI changed it: the tracked file is the plugin manifest."
    fi
    rm -f "$snapshot"
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
bootstrap
setup_local_md
setup_identity
report_managed
check_python
plugin_sync
summary

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
summary

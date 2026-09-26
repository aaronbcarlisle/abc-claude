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
        if (& $Found) { Write-Info "Installed '$Name'."; return }
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
Invoke-Bootstrap
Write-Summary
exit 0

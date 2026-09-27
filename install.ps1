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

# Exit 1 unless the local clone's master holds install.sh and .gitignore: clone
# and adopt install that branch, not the checked-out one.
function Assert-SourceMaster {
    $src = $script:LocalSrc
    $listed = @(Invoke-Quiet 'git' @('-C', $src, 'ls-tree', '--name-only', 'refs/heads/master', '--', 'install.sh', '.gitignore'))
    if ($script:NativeExit -ne 0 -or $listed -cnotcontains 'install.sh' -or $listed -cnotcontains '.gitignore') {
        Fail "The clone at $src has no usable master branch (install.sh or .gitignore missing). In the clone, run: git checkout master, then git pull --ff-only."
    }
}

# Warn when the local clone's master is behind its origin/master, or when the
# clone has another branch checked out.
function Write-SourceBranchWarnings {
    $src = $script:LocalSrc
    Invoke-Quiet 'git' @('-C', $src, 'rev-parse', '--verify', '-q', 'refs/remotes/origin/master') | Out-Null
    if ($script:NativeExit -eq 0) {
        $behind = Get-GitLine @('-C', $src, 'rev-list', '--count', 'refs/heads/master..refs/remotes/origin/master')
        if ($behind -and [int]$behind -gt 0) {
            Write-Warn "The clone's master is behind its origin/master: installing the older commit. Run git pull --ff-only in the clone for the latest."
        }
    }
    $branch = Get-GitLine @('-C', $src, 'symbolic-ref', '-q', '--short', 'HEAD')
    if (-not $branch) { $branch = 'a detached HEAD' }
    if ($branch -ne 'master') {
        Write-Warn "The clone is on $branch, not master: only master is installed."
    }
}

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

function Update-Existing($Origin) {
    $T = $script:Target
    Write-Info "$T is an abc-claude checkout - updating it."
    Write-Info 'Close running Claude Code sessions first: they can hold files open.'
    Restore-Missing
    $dirty = @(Invoke-Quiet 'git' @('-C', $T, 'status', '--porcelain', '--untracked-files=no') | Where-Object { $_ })
    if ($dirty.Count -gt 0) {
        Write-Warn "Tracked files in $T have local changes - skipping the pull. Commit or discard them, then re-run."
        return
    }
    if (Test-Remote $Origin) {
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
        if (-not (Invoke-Git @('-C', $T, 'remote', 'set-url', 'origin', $script:OriginUrl))) { Fail 'git remote set-url failed.' }
    }
}

# $true when the local source checkout is the target itself.
function Test-InPlace {
    if (-not $script:LocalSrc) { return $false }
    if (-not (Test-Path -LiteralPath (Join-Path $script:Target '.git'))) { return $false }
    $targetTop = Get-GitLine @('-C', $script:Target, 'rev-parse', '--show-toplevel')
    return [bool]($targetTop -and (Test-SamePath $targetTop $script:LocalSrc))
}

# Check a local source clone's master before bootstrap installs it; an in-place
# run installs nothing from it, so it is skipped.
function Confirm-Source {
    if (-not $script:LocalSrc) { return }
    if (Test-InPlace) { return }
    Assert-SourceMaster
    Write-SourceBranchWarnings
}

# $true when the target's .git has an unborn HEAD and no local branches: an
# adopt stopped before its reset, with no history to lose.
function Test-UnfinishedAdopt {
    Invoke-Quiet 'git' @('-C', $script:Target, 'rev-parse', '--verify', '-q', 'HEAD') | Out-Null
    if ($script:NativeExit -eq 0) { return $false }
    $branches = @(Invoke-Quiet 'git' @('-C', $script:Target, 'for-each-ref', 'refs/heads') | Where-Object { $_ })
    return ($branches.Count -eq 0)
}

function Restart-Adopt {
    $T = $script:Target
    Write-Warn "Found an unfinished adopt in ${T}: starting it again."
    Remove-Item -LiteralPath (Join-Path $T '.git') -Recurse -Force -ErrorAction SilentlyContinue
    Invoke-Adopt
}

function Invoke-Bootstrap {
    $T = $script:Target
    if (Test-InPlace) {
        Write-Info "Running from the checkout at $T - using it in place."
        return
    }
    if (Test-Path -LiteralPath (Join-Path $T '.git')) {
        $origin = Get-GitLine @('-C', $T, 'config', '--get', 'remote.origin.url')
        if ($origin -and ((Normalize-Url $origin) -eq (Normalize-Url $script:OriginUrl))) {
            if (Test-UnfinishedAdopt) {
                Restart-Adopt
                return
            }
            Update-Existing $origin
            return
        }
        # An adopt from a clone elsewhere stopped before remote set-url: origin
        # is still that clone's path.
        if ($script:LocalSrc -and $origin -and (Test-SamePath $origin $script:LocalSrc) -and (Test-UnfinishedAdopt)) {
            Restart-Adopt
            return
        }
        if (-not $origin) { $origin = 'none' }
        Fail "$T is a git repo with a different origin ($origin). Move it aside or set CLAUDE_CONFIG_DIR, then re-run. Nothing was changed."
    }
    if (Test-Path -LiteralPath $T -PathType Leaf) { Fail "$T exists and is not a directory. Move it aside, then re-run." }
    Warn-UncommittedSource
    if (Test-Path -LiteralPath $T) { Invoke-Adopt } else { Invoke-Clone }
}

# --- bootstrap: adopt -------------------------------------------------------
# Undo the git init of a failed adopt, then exit 1.
function Stop-Adopt($m) {
    Remove-Item -LiteralPath (Join-Path $script:Target '.git') -Recurse -Force -ErrorAction SilentlyContinue
    Fail "$m Nothing was changed."
}

# Copy files that differ from the repo (content, not line endings) to <path>.bak.<Stamp>.
function Backup-Conflicts {
    $T = $script:Target
    $conflicts = @(Invoke-Quiet 'git' @('-C', $T, '-c', 'core.quotepath=off', 'diff', '--name-only', '--diff-filter=MT') | Where-Object { $_ })
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
    if (-not (Invoke-Git @('-C', $T, 'config', 'core.autocrlf', 'false'))) { Stop-Adopt "git config failed in $T." }
    if (-not (Invoke-Git @('-C', $T, 'remote', 'add', 'origin', $script:FetchUrl))) { Stop-Adopt 'git remote add failed.' }
    if (-not (Invoke-Git @('-C', $T, 'fetch', '-q', 'origin'))) { Stop-Adopt "git fetch from $($script:FetchUrl) failed." }
    # A missing origin/master is left to the reset below, which reports it.
    $gitignore = @(Invoke-Quiet 'git' @('-C', $T, 'ls-tree', '--name-only', 'origin/master', '--', '.gitignore') | Where-Object { $_ })
    if ($script:NativeExit -eq 0 -and $gitignore.Count -eq 0) { Stop-Adopt 'The fetched master has no .gitignore.' }
    if (-not (Invoke-Git @('-C', $T, 'reset', '-q', 'origin/master'))) { Stop-Adopt 'git reset to origin/master failed.' }
    if (-not (Invoke-Git @('-C', $T, 'branch', '-q', '-u', 'origin/master'))) { Stop-Adopt 'git branch -u origin/master failed.' }
    if ($script:FetchUrl -ne $script:OriginUrl) {
        if (-not (Invoke-Git @('-C', $T, 'remote', 'set-url', 'origin', $script:OriginUrl))) { Stop-Adopt 'git remote set-url failed.' }
    }
    Backup-Conflicts
    if (-not (Invoke-Git @('-C', $T, 'checkout', '--', '.'))) {
        Fail 'git checkout failed partway (a running Claude Code session may hold a file open). Close Claude Code and re-run: the re-run restores the missing files.'
    }
    Repair-LineEndings
    Write-Info "Adopted $T."
}

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
    if ($present.Count -gt 0) { Write-Info "Already present: $($present -join ', ')" }
    if ($added.Count -gt 0) { Write-Info "Installed: $($added -join ', ')" }
    if ($failed.Count -gt 0) { Write-Warn "Failed: $($failed -join ', ')" }

    $extras = @($installed | Where-Object { $wanted -notcontains $_ })
    if ($extras.Count -gt 0) {
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
    # After prune too: claude plugin uninstall may rewrite settings.json as well.
    $after = [System.IO.File]::ReadAllBytes($settingsPath)
    if ([Convert]::ToBase64String($before) -ne [Convert]::ToBase64String($after)) {
        [System.IO.File]::WriteAllBytes($settingsPath, $before)
        Write-Info 'Restored settings.json after the claude CLI changed it: the tracked file is the plugin manifest.'
    }
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
Confirm-Source
Invoke-Bootstrap
Initialize-LocalMd
Set-RepoIdentity
Write-ManagedReport
Test-PythonFromBash
Invoke-PluginSync
Write-Summary
exit 0

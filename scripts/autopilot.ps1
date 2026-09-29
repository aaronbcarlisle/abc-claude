#Requires -Version 7.2
<#
.SYNOPSIS
Overnight autopilot: continue a repo's phase from its SESSION_HANDOFF.md in headless runs.

.DESCRIPTION
While the handoff's `Autopilot:` line says `continue`, runs
`claude -p "/resume autopilot"` in the repo's own checkout, up to -MaxRuns runs
and -BudgetUsd dollars in all.

It stops when:
- the line says `owner` or `done`, or is missing (also checked right after each
  run, and it wins over "stalled");
- two runs in a row change neither HEAD nor the Progress Log's content
  ("stalled"; compared by `handoff_state.py status`'s content signature, so a
  rewritten entry counts as a change);
- the budget is spent;
- a usage-limit reset is more than 28800 s (8 hours) away ("usage-limit");
- a live Claude Code session is in the checkout (checked before the handoff is
  first read and at the top of every loop pass; the handoff is then left
  untouched);
- the checkout has a modified or staged tracked file at start ("dirty-tree",
  refused before the first run; untracked files and SESSION_HANDOFF.md itself
  don't count; not re-checked
  between runs, so a tree an earlier run left dirty is continued);
- the handoff can't be read (`error: handoff status failed`) or anything throws
  (`error: <first line of the message>`).

A usage-limit stop sleeps until the reset time (30 minutes when none is given)
and continues, unless no run can follow (max runs or budget reached: sleeps 0 s)
or the wait is over the cap. The stream-json goes to ~/.claude/state/autopilot/, and a
summary line is appended to the handoff's Progress Log.

Exit: 0 ended normally (done, owner, no Autopilot line, max runs); 1 stalled,
budget, usage-limit or error; 2 refused (no such repo, not a git repo, live
session, dirty tree).

Test overrides: AUTOPILOT_STATE_DIR (log directory), AUTOPILOT_MAX_SLEEP_SECONDS
(caps each usage-limit sleep), CLAUDE_SESSIONS_DIR (read by live_sessions.py).
Tests: python -m unittest discover -s tests (from the repo root)
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Repo,
    [int]$MaxRuns = 6,
    [int]$MaxTurns = 200,
    [double]$BudgetUsd = 25,
    [string]$Model = 'opus'
)

$ErrorActionPreference = 'Stop'
$Utf8 = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $Utf8
$OutputEncoding = $Utf8
$Invariant = [Globalization.CultureInfo]::InvariantCulture
$DefaultWait = 1800
$MaxWait = 28800

if (-not (Test-Path -LiteralPath $Repo -PathType Container)) {
    [Console]::Error.WriteLine("autopilot: no such directory: $Repo")
    exit 2
}
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$top = & git -C $Repo rev-parse --show-toplevel 2>$null
if ($LASTEXITCODE -ne 0 -or -not "$top".Trim()) {
    [Console]::Error.WriteLine("autopilot: not a git repository: $Repo")
    exit 2
}
# A subdirectory means its checkout: the handoff and the live-session check belong to the top level.
$Repo = (Resolve-Path -LiteralPath "$top".Trim()).Path

$Scripts = $PSScriptRoot
$Handoff = Join-Path $Repo 'SESSION_HANDOFF.md'
$StateDir = if ($env:AUTOPILOT_STATE_DIR) { $env:AUTOPILOT_STATE_DIR } else { Join-Path $HOME '.claude/state/autopilot' }
New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
$Log = Join-Path $StateDir ('{0}-{1}.log' -f (Split-Path -Leaf $Repo), (Get-Date -Format 'yyyyMMdd-HHmm'))
$MaxSleep = if ($env:AUTOPILOT_MAX_SLEEP_SECONDS) { [long]$env:AUTOPILOT_MAX_SLEEP_SECONDS } else { [long]::MaxValue }

function Write-Log([string]$Text) {
    [IO.File]::AppendAllText($Log, $Text + "`n", $Utf8)
}

# A missing or non-UTF-8 handoff is not a failure (status exits 0). A non-zero
# exit or output that isn't JSON is: it throws, and the loop ends as an error.
function Get-HandoffState {
    $json = & python (Join-Path $Scripts 'handoff_state.py') status $Handoff 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'handoff status failed' }
    try { return (($json -join "`n") | ConvertFrom-Json -ErrorAction Stop) }
    catch { throw 'handoff status failed' }
}

function Get-Head {
    $head = & git -C $Repo rev-parse HEAD 2>$null
    if ($LASTEXITCODE -ne 0) { return '' }
    return "$head".Trim()
}

function Test-LiveSession {
    $found = & python (Join-Path $Scripts 'live_sessions.py') $Repo
    if ($LASTEXITCODE -eq 1) {
        Write-Log ('live session in {0}: {1}' -f $Repo, ($found -join '; '))
        return $true
    }
    return $false
}

function Get-StateName($State) {
    if ($State.autopilot) { return "$($State.autopilot)" }
    return 'no-autopilot-line'
}

function Invoke-Run([double]$Remaining) {
    $argv = @(
        '-p', '/resume autopilot',
        '--permission-mode', 'auto',
        '--model', $Model,
        '--max-turns', "$MaxTurns",
        '--max-budget-usd', $Remaining.ToString('0.00', $Invariant),
        '--output-format', 'stream-json',
        '--verbose'
    )
    $seen = @{ Result = $null; Limit = $null }
    $env:CLAUDE_AUTOPILOT = '1'
    $env:CLAUDE_AUTOPILOT_HANDOFF = $Handoff
    Push-Location -LiteralPath $Repo
    try {
        & claude @argv 2>&1 | ForEach-Object {
            $line = "$_"
            Write-Log $line
            try { $item = $line | ConvertFrom-Json -ErrorAction Stop } catch { return }
            if ($item.type -eq 'result') { $seen.Result = $item }
            elseif ($item.type -eq 'rate_limit_event' -and $item.rate_limit_info) { $seen.Limit = $item.rate_limit_info }
        }
        $code = $LASTEXITCODE
    }
    finally {
        Pop-Location
        Remove-Item Env:CLAUDE_AUTOPILOT, Env:CLAUDE_AUTOPILOT_HANDOFF -ErrorAction SilentlyContinue
    }
    return [pscustomobject]@{ Result = $seen.Result; Limit = $seen.Limit; Exit = $code }
}

# Seconds until a usage limit resets, or $null when the run didn't hit one.
function Get-LimitWait($Run) {
    $result = $Run.Result
    if ($result -and -not $result.is_error) { return $null }
    $byEvent = [bool]($Run.Limit -and $Run.Limit.status -and $Run.Limit.status -notin 'allowed', 'allowed_warning')
    $byText = [bool]($result -and ("$($result.result) $($result.subtype)" -match '(?i)usage limit|rate limit|limit reached'))
    if (-not ($byEvent -or $byText)) { return $null }
    if ($byEvent -and $Run.Limit.resetsAt) {
        return [Math]::Max([long]60, [long]$Run.Limit.resetsAt - [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() + 60)
    }
    # The text form can carry the reset as a Unix time: "Claude AI usage limit reached|1759300000".
    if ($byText -and "$($result.result)" -match '\|(\d{10})\b') {
        return [Math]::Max([long]60, [long]$Matches[1] - [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() + 60)
    }
    return [long]$DefaultWait
}

$spent = 0.0
$runs = 0
$idle = 0
$status = $null
Write-Log ('autopilot start {0} repo={1} max-runs={2} max-turns={3} budget={4} model={5}' -f (Get-Date -Format s),
    $Repo, $MaxRuns, $MaxTurns, $BudgetUsd.ToString('0.00', $Invariant), $Model)

try {
    if (Test-LiveSession) { $status = 'live-session' }
    # Once, at start: a tree left dirty by an autopilot run is checked only at the next start.
    # The handoff itself doesn't count: a tracked one carries the last summary line or the owner's edit.
    if ($null -eq $status -and (& git -C $Repo status --porcelain --untracked-files=no -- ':/' ':(exclude)SESSION_HANDOFF.md')) {
        $status = 'dirty-tree'
    }
    $first = $true
    while ($null -eq $status) {
        # The owner may have opened a session during a sleep; the first pass was checked above.
        if (-not $first -and (Test-LiveSession)) { $status = 'live-session'; break }
        $first = $false
        $state = Get-HandoffState
        if ($state.autopilot -ne 'continue') { $status = Get-StateName $state; break }
        if ($runs -ge $MaxRuns) { $status = 'max-runs'; break }
        $remaining = $BudgetUsd - $spent
        # Under a cent is spent: it would reach claude as --max-budget-usd 0.00.
        if ([Math]::Round($remaining, 2) -le 0) { $status = 'budget'; break }

        $headBefore = Get-Head
        $progressBefore = "$($state.progress)"   # Progress Log content signature, not an entry count
        $runs++
        Write-Log ('run {0} start {1}' -f $runs, (Get-Date -Format s))
        $run = Invoke-Run $remaining
        if ($run.Result -and $null -ne $run.Result.total_cost_usd) { $spent += [double]$run.Result.total_cost_usd }

        $wait = Get-LimitWait $run
        if ($null -ne $wait) {
            # The cap and the no-follow rule judge the computed wait, before the test cap.
            if ($wait -gt $MaxWait) {
                Write-Log ('usage limit: reset in {0} s, over the {1} s cap' -f $wait, $MaxWait)
                $status = 'usage-limit'
                break
            }
            $sleep = if ($runs -ge $MaxRuns -or [Math]::Round($BudgetUsd - $spent, 2) -le 0) { 0 } else { [Math]::Min([long]$wait, $MaxSleep) }
            Write-Log ('usage limit: reset in {0} s, sleeping {1} s' -f $wait, $sleep)
            if ($sleep -gt 0) { Start-Sleep -Seconds $sleep }
            continue
        }
        if ($null -eq $run.Result) { $status = "error: claude exited $($run.Exit) with no result"; break }
        if ($run.Result.is_error -and "$($run.Result.subtype)" -notmatch 'max_turns|max_budget') {
            $status = "error: $($run.Result.subtype)"
            break
        }

        $after = Get-HandoffState
        # The handoff's own state wins over "stalled".
        if ($after.autopilot -ne 'continue') { $status = Get-StateName $after; break }
        if ((Get-Head) -eq $headBefore -and "$($after.progress)" -ceq $progressBefore) { $idle++ } else { $idle = 0 }
        if ($idle -ge 2) { $status = 'stalled'; break }
    }
}
catch {
    $status = 'error: ' + (("$($_.Exception.Message)" -split '\r?\n')[0]).Trim()
}

$sha = Get-Head
if ($sha.Length -gt 7) { $sha = $sha.Substring(0, 7) }
$summary = '- {0} | autopilot | {1} | runs {2} | ${3} | {4}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm'), $status, $runs,
    $spent.ToString('0.00', $Invariant), $sha
Write-Log "autopilot end: $summary"
# Another session owns the handoff on a live-session refusal, and a dirty-tree
# refusal never writes it: never touch it.
if ($status -notin 'live-session', 'dirty-tree' -and (Test-Path -LiteralPath $Handoff)) {
    & python (Join-Path $Scripts 'handoff_state.py') append $Handoff $summary *> $null
}
Write-Output $summary
if ($status -in 'done', 'owner', 'no-autopilot-line', 'max-runs') { exit 0 }
if ($status -in 'live-session', 'dirty-tree') { exit 2 }
exit 1

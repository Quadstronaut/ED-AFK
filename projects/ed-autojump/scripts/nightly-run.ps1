<#
.SYNOPSIS
    Tier-2 unattended overnight runner for ed-autojump.

.DESCRIPTION
    Activates the project venv, runs `ed-autojump run --record` for the
    configured duration, tees stdout + stderr to a timestamped log file in
    the sessions directory, and exits with the bot's return code.

    Intended targets: any of the user's 4 ED installs. The 3 sandboxie-ng
    instances can be driven concurrently by invoking this script from
    inside each sandbox with a different -EdInstance label.

.PARAMETER ProjectRoot
    Path to projects\ed-autojump\. Defaults to the script's parent dir.

.PARAMETER DurationHours
    How long the bot tails the journal before exiting. Default 6.

.PARAMETER JournalDir
    Override the journal directory. Default: the standard FDev location
    under $env:USERPROFILE\Saved Games\. Each sandboxie instance has its
    own remapped Saved Games dir; pass the actual path explicitly.

.PARAMETER SessionsDir
    Override the recorded-session output dir. Default:
    $env:USERPROFILE\ed-afk-sessions\.

.PARAMETER EdInstance
    Optional label embedded into the log filename so concurrent runs
    don't stomp on each other. Defaults to "default".

.PARAMETER NoRecord
    Skip the JSONL session recorder (default is recording ON for nightly).

.EXAMPLE
    .\nightly-run.ps1 -DurationHours 8

.EXAMPLE
    .\nightly-run.ps1 -EdInstance "sandbox-A" -JournalDir "C:\Sandbox\Quadstronaut\sandbox-A\user\current\AppData\Local\Frontier Developments\..."
#>

[CmdletBinding()]
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [double]$DurationHours = 6.0,
    [string]$JournalDir = "",
    [string]$SessionsDir = "",
    [string]$EdInstance = "default",
    [switch]$NoRecord
)

$ErrorActionPreference = "Stop"

# --- resolve paths ---------------------------------------------------------

if (-not $SessionsDir) {
    $SessionsDir = Join-Path $env:USERPROFILE "ed-afk-sessions"
}
if (-not (Test-Path $SessionsDir)) {
    New-Item -ItemType Directory -Path $SessionsDir -Force | Out-Null
}

$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Error "venv python not found at $venvPython. Bootstrap the project first."
    exit 2
}

$stamp = Get-Date -Format "yyyy-MM-ddTHHmmss"
$logPath = Join-Path $SessionsDir ("nightly_{0}_{1}.log" -f $EdInstance, $stamp)

# --- build CLI args --------------------------------------------------------

$durationSeconds = [int]($DurationHours * 3600.0)
$cliArgs = @(
    "-m", "ed_autojump.cli",
    "run",
    "--duration", $durationSeconds,
    "--sessions-dir", $SessionsDir
)
if (-not $NoRecord) {
    $cliArgs += "--record"
}
if ($JournalDir) {
    $cliArgs += @("--journal-dir", $JournalDir)
}

# --- run -------------------------------------------------------------------

$env:PYTHONUTF8 = "1"
$env:ED_AFK_SESSIONS_DIR = $SessionsDir

Write-Host ("[{0}] ed-afk nightly: instance={1} duration={2}h log={3}" -f `
    (Get-Date -Format "u"), $EdInstance, $DurationHours, $logPath)
Write-Host "cmd: $venvPython $($cliArgs -join ' ')"

# Tee to log + console. Use Start-Process with redirection so we capture
# both streams and still get the exit code.
$proc = Start-Process -FilePath $venvPython -ArgumentList $cliArgs `
    -NoNewWindow -PassThru -Wait `
    -RedirectStandardOutput $logPath `
    -RedirectStandardError ($logPath -replace "\.log$", ".err.log")

Write-Host ("[{0}] ed-afk nightly exit={1}" -f (Get-Date -Format "u"), $proc.ExitCode)
exit $proc.ExitCode

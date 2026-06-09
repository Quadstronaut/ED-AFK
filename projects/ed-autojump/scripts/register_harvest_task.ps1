<#
.SYNOPSIS
    Register (or unregister) the ED-AFK ReceiveText Harvester as a per-user
    Windows Scheduled Task — NO admin / elevation required.

.DESCRIPTION
    Creates a Scheduled Task named "ED-AFK ReceiveText Harvester" that runs the
    project venv python on scripts\harvest_receivetext.py:
        * every 30 minutes (indefinitely), AND
        * at the current user's logon.

    The task runs as the CURRENT USER in the interactive (S4U/limited) context,
    so it never prompts for admin rights. It is idempotent: re-running replaces
    any existing task of the same name. The harvester itself is read-only on the
    game (reads journals, writes only under data\), so the schedule is harmless.

.PARAMETER Unregister
    Remove the task instead of registering it.

.EXAMPLE
    .\register_harvest_task.ps1
    .\register_harvest_task.ps1 -Unregister

.NOTES
    Verify after registering:
        schtasks /query /tn "ED-AFK ReceiveText Harvester" /v /fo LIST
    Run it on demand:
        schtasks /run   /tn "ED-AFK ReceiveText Harvester"
#>

[CmdletBinding()]
param(
    [switch]$Unregister
)

$ErrorActionPreference = 'Stop'

$TaskName = 'ED-AFK ReceiveText Harvester'

# Resolve paths relative to THIS script so the task is location-independent.
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Definition   # ...\scripts
$ProjectRoot = Split-Path -Parent $ScriptDir                            # ...\ed-autojump
$PythonExe   = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$Harvester   = Join-Path $ScriptDir  'harvest_receivetext.py'

# ── Unregister path ─────────────────────────────────────────────────────────
if ($Unregister) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task: $TaskName" -ForegroundColor Green
    } else {
        Write-Host "No scheduled task named '$TaskName' to remove." -ForegroundColor Yellow
    }
    return
}

# ── Pre-flight: the referenced files must exist ──────────────────────────────
if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "venv python not found: $PythonExe  (create the project .venv first)"
}
if (-not (Test-Path -LiteralPath $Harvester)) {
    throw "harvester not found: $Harvester"
}

# ── Build the task ───────────────────────────────────────────────────────────
# Run the harvester with the project root as the working dir so its relative
# data\ paths resolve correctly regardless of where the task fires from.
$action = New-ScheduledTaskAction `
    -Execute  $PythonExe `
    -Argument "`"$Harvester`"" `
    -WorkingDirectory $ProjectRoot

# Trigger 1: every 30 minutes, starting now, effectively forever.
# Explicit 10-year (3650-day) repetition duration — a valid xs:duration the
# Task Scheduler engine accepts. [TimeSpan]::MaxValue serializes outside the
# schema bound and can silently register a task that never fires (scheduler
# council REJECT, 2026-06-09).
$trigDaily = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 30) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

# Trigger 2: at this user's logon.
$trigLogon = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)

# Run as the current user, interactive, limited (NON-elevated) — no admin.
$principal = New-ScheduledTaskPrincipal `
    -UserId  ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel  Limited

# Don't pile up runs; allow on battery; let it run as long as needed.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

# Idempotent: Register-ScheduledTask -Force replaces an existing task of the
# same name in place.
$task = Register-ScheduledTask `
    -TaskName  $TaskName `
    -Action    $action `
    -Trigger   @($trigDaily, $trigLogon) `
    -Principal $principal `
    -Settings  $settings `
    -Description 'Harvests ReceiveText $token; strings from ED journals into data\receivetext_catalog.json. Read-only on the game.' `
    -Force

Write-Host "Registered scheduled task: $TaskName" -ForegroundColor Green
Write-Host "  python   : $PythonExe"
Write-Host "  script   : $Harvester"
Write-Host "  workdir  : $ProjectRoot"
Write-Host "  triggers : every 30 min + at logon (current user, non-elevated)"
Write-Host ""
Write-Host "Verify : schtasks /query /tn `"$TaskName`" /v /fo LIST" -ForegroundColor Cyan
Write-Host "Run now: schtasks /run   /tn `"$TaskName`""             -ForegroundColor Cyan
Write-Host "Remove : .\register_harvest_task.ps1 -Unregister"        -ForegroundColor Cyan

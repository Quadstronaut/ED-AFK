<#
.SYNOPSIS
    Minimal stand-in for launch.ps1's run site, used by the AC1/AC2 repros.

    Creates a kill-on-close job (unless -ForceNoJob), spawns dummy_child.py
    through Start-ChildInJob exactly as the real launcher does, writes the
    captured child PID to -PidOut, then idles holding the job handle open.

    The outer repro hard-kills THIS process (Stop-Process) and asserts the
    child PID is gone -- proving Layer 1's kill-on-close survives launcher
    hard-kill. With -ForceNoJob it idles with NO job (Layer 2 only), and the
    outer repro instead exercises the graceful taskkill path.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Python,
    [Parameter(Mandatory)][string]$DummyScript,
    [Parameter(Mandatory)][string]$WorkDir,
    [Parameter(Mandatory)][string]$PidOut,
    [switch]$ForceNoJob,
    [switch]$GracefulFallbackDemo,
    [double]$IdleSeconds = 60.0
)

$ErrorActionPreference = "Stop"
# lifecycle_tests -> scripts -> ed-autojump -> projects -> repo root (4 up)
$repoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)))
. (Join-Path $repoRoot "launch_job.ps1")

$childArgs = @($DummyScript, "long", "120")
$spawn = Start-ChildInJob -FilePath $Python -Arguments $childArgs `
    -WorkingDirectory $WorkDir -ForceNoJob:$ForceNoJob

Set-Content -Path $PidOut -Value $spawn.ChildPid -Encoding ASCII

if ($GracefulFallbackDemo) {
    # Layer-2 demo: NO job. Simulate a graceful launcher exit running its
    # finally -> taskkill /T on the captured PID. Then exit cleanly.
    Stop-ChildTree -ChildPid $spawn.ChildPid
    exit 0
}

# Idle holding the job handle open. The outer repro hard-kills us here.
Start-Sleep -Seconds $IdleSeconds

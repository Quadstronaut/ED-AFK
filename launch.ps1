<#
.SYNOPSIS
    Interactive launcher for the ed-autojump jump loop.
    Reviews your settings, FOCUSES the Elite window, then starts the bot so
    its keypresses land in the game instead of wherever your mouse last was.
    Run  .\launch.ps1 -Help  for a plain-English guide.
#>

[CmdletBinding(PositionalBinding = $false)]
param(
    [double]$DurationHours = 6.0,
    [switch]$NoRecord,
    [switch]$RoutePlot,
    [switch]$Yes,         # skip the menu entirely; launch straight from the flags
    [switch]$NoFocus,     # do NOT focus the Elite window before starting
    [switch]$Help,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Extra
)

$ErrorActionPreference = "Stop"

function Show-FriendlyHelp {
    Write-Host @'

================  ED-AFK  ---  plain-English guide  ================

WHAT THIS DOES
  You fly Elite Dangerous into a system and plot a route. Then this runs
  the robot that presses the keys to dodge the star, honk, scoop fuel, and
  jump -- over and over -- so you do not have to sit there.

  IT DOES NOT launch the game or click menus. YOU start ED and get in the
  cockpit yourself. This only does the jumping.

HOW IT NOW WORKS (interactive)
  1. A MENU shows every setting. Use the arrow keys:
       Up / Down  -- pick a row
       Left/Right -- change it (toggle on/off, or +/- the hours)
       Enter      -- done editing
  2. Then: [y] launch   [n] go back and change settings   [q] quit.
  3. On launch it FOCUSES the Elite window and counts down, so the keys land
     in the game -- not in your browser. KEEP ELITE IN FRONT after that; if
     you click away, the next keypress misses.

DO THIS FIRST  (or the ship will not move!)
  1. ONE TIME:  .\launch.ps1 install-binds   -- adds the "ED-AFK" keyboard
     preset to Elite. Then in ED > Controls, pick "ED-AFK". If another
     preset is active the keys do not match and the ship will not respond.
  2. STEERING (optional but recommended):  .\launch.ps1 calibrate-compass
     while in the cockpit with the nav-compass visible, then set
     [vision].enabled = true in config.toml. Without this the bot jumps
     BLIND -- it will not orient the ship toward the target.
  3. Be IN THE GAME, in your ship, with a route plotted (or use -RoutePlot).

HOW TO RUN
  .\launch.ps1           arrow-key menu, then y/n/q, focus ED, run
  .\launch.ps1 -Yes      skip the menu entirely (uses the flags below)
  .\launch.ps1 -Help     show this

  The flags below just seed the menu's starting values -- you can still
  change everything in the menu before launching.

THE KNOBS
  -DurationHours N   Starting hours. Default 6. Ctrl+C stops a run early.
  -NoRecord          Start with logging OFF. (Default: it DOES save one.)
  -RoutePlot         Start with auto-plot ON (plots to config.toml's dest).
  -Yes               Skip the menu + confirm; launch straight from the flags.
  -NoFocus           Do not grab the Elite window (debugging only).

PASS-THROUGH (advanced; skips the review + focus)
    .\launch.ps1 doctor
    .\launch.ps1 calibrate-compass
    .\launch.ps1 install-binds

===================================================================

'@
}

# The Python project lives in a subfolder; this script sits at the repo root.
$ProjectRoot = Join-Path $PSScriptRoot "projects\ed-autojump"
$venvDir = Join-Path $ProjectRoot ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$configPath = Join-Path $ProjectRoot "config.toml"

# --- help short-circuit (before any venv work) -----------------------------

$helpTokens = @('-h', '--help', '/?', 'help')
if ($Help -or ($Extra | Where-Object { $helpTokens -contains $_ })) {
    Show-FriendlyHelp
    exit 0
}

# --- bootstrap the venv if it's missing ------------------------------------

if (-not (Test-Path $venvPython)) {
    Write-Host "[launch] no .venv found -- creating one..."
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.11 -m venv $venvDir
        if (-not $?) { & py -3 -m venv $venvDir }
    } else {
        & python -m venv $venvDir
    }
    if (-not (Test-Path $venvPython)) {
        Write-Error "venv creation failed -- no python at $venvPython"
        exit 2
    }
}

& $venvPython -c "import ed_autojump" 2>$null
if (-not $?) {
    Write-Host "[launch] installing ed-autojump (editable) into the venv..."
    $pkgSpec = $ProjectRoot + "[dev,hotkey,vision]"
    & $venvPython -m pip install -e $pkgSpec
    if (-not $?) {
        Write-Error "pip install failed"
        exit 2
    }
}

# --- helpers ---------------------------------------------------------------

function Read-YesNo([string]$prompt, [bool]$default) {
    $hint = if ($default) { "(Y/n)" } else { "(y/N)" }
    $ans = Read-Host "$prompt $hint"
    if (-not $ans) { return $default }
    return ($ans -match '^[Yy]')
}

function Test-VisionEnabled([string]$path) {
    # Best-effort scan of config.toml: is [vision].enabled = true (uncommented)?
    if (-not (Test-Path $path)) { return $false }
    $inVision = $false
    foreach ($raw in Get-Content $path) {
        $line = $raw.Trim()
        if ($line.StartsWith("#")) { continue }
        if ($line -match '^\[(.+)\]') {
            $inVision = ($matches[1] -eq 'vision')
            continue
        }
        if ($inVision -and $line -match '^enabled\s*=\s*true') { return $true }
    }
    return $false
}

function Set-EliteForeground {
    # Bring the Elite Dangerous window to the foreground so SendInput lands
    # in the game. Returns $true if we found + focused a window.
    if (-not ([System.Management.Automation.PSTypeName]'EDAFK.Win32').Type) {
        Add-Type -Namespace EDAFK -Name Win32 -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern bool SetForegroundWindow(System.IntPtr hWnd);
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern bool ShowWindow(System.IntPtr hWnd, int nCmdShow);
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern bool BringWindowToTop(System.IntPtr hWnd);
'@
    }
    $proc = Get-Process -Name EliteDangerous64, EliteDangerous32 -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
    if (-not $proc) { return $false }
    $h = $proc.MainWindowHandle
    [void][EDAFK.Win32]::ShowWindow($h, 9)   # SW_RESTORE (un-minimize)
    [void][EDAFK.Win32]::BringWindowToTop($h)
    return [EDAFK.Win32]::SetForegroundWindow($h)
}

function Get-CliArgs([hashtable]$s) {
    # Single source of truth for the CLI invocation, shared by the live menu
    # preview and the actual launch.
    $durationSeconds = [int]($s.DurationHours * 3600.0)
    $a = @("-m", "ed_autojump.cli", "run", "--duration", $durationSeconds)
    if ($s.Engage) { $a += "--engage-keys" } else { $a += "--no-engage-keys" }
    if ($s.Record) { $a += "--record" }
    if ($s.RoutePlot) { $a += "--route-plot" }
    return , $a
}

function Set-RowValue([hashtable]$s, [string]$row, [int]$dir) {
    # Left/Right on a row. Duration steps by 0.5h (clamped); the rest toggle.
    switch ($row) {
        'Duration' {
            $v = [double]$s.DurationHours + (0.5 * $dir)
            if ($v -lt 0.5) { $v = 0.5 }
            if ($v -gt 24.0) { $v = 24.0 }
            $s.DurationHours = $v
        }
        default { $s[$row] = -not [bool]$s[$row] }   # on/off, direction ignored
    }
}

function Invoke-SettingsMenu {
    # Interactive arrow-key editor. Mutates $s in place (hashtables are by ref).
    # Up/Down move the cursor, Left/Right change the selected row, Enter = done.
    # Falls back to plain prompts where ReadKey isn't available (e.g. ISE).
    param([hashtable]$s, [bool]$visionOn, [string]$configPath, [string]$venvPython)

    $interactive = $true
    try { $null = [Console]::CursorTop } catch { $interactive = $false }
    if ($Host.Name -eq 'Windows PowerShell ISE Host') { $interactive = $false }

    if (-not $interactive) {
        Write-Host "`n=== ED-AFK setup (Enter keeps the current value) ==="
        $hrs = Read-Host ("Hours to jump? [{0}]" -f $s.DurationHours)
        if ($hrs) { $s.DurationHours = [double]$hrs }
        $s.Engage    = Read-YesNo "Press keys / fly the ship?" ([bool]$s.Engage)
        $s.Record    = Read-YesNo "Save a log of the run?"      ([bool]$s.Record)
        $s.RoutePlot = Read-YesNo "Auto-plot a route if none?"  ([bool]$s.RoutePlot)
        return
    }

    # Steering is row 4: it's not a setting you toggle here -- selecting it and
    # pressing Enter/Space/Left/Right runs calibrate-compass.
    $rowKeys = @('Duration', 'Engage', 'Record', 'RoutePlot', 'Steering')
    $sel = 0
    $vis = $visionOn   # mutable: recomputed after an in-menu calibration
    $W = [Math]::Min(78, [Console]::BufferWidth - 1)

    function Build-Lines([hashtable]$s, [int]$sel, [bool]$visionOn, [string]$cfg, [string]$py) {
        $dur    = "{0:0.0}" -f [double]$s.DurationHours
        $durSec = [int]($s.DurationHours * 3600.0)
        $tog    = { param($b) if ($b) { '[ ON  ]' } else { '[ off ]' } }
        $steer  = if (-not $s.Engage) { 'n/a (keys disabled)' }
                  elseif ($visionOn) { 'ON (vision/compass)' }
                  else { 'OFF -- BLIND (Enter to calibrate)' }
        $steerHint = if ($sel -eq 4) { '   <- Enter/Space = calibrate compass' } else { '' }
        $cmd    = "$py " + ((Get-CliArgs $s) -join ' ')
        $warn   = if ($s.Engage -and -not $visionOn) {
                      'NOTE: steering OFF -- bot will NOT orient toward target.'
                  } else { '' }

        $L = New-Object System.Collections.Generic.List[object]
        $add = { param($t, $r) $L.Add([pscustomobject]@{ Text = $t; Row = $r }) }

        & $add '=============== ED-AFK setup =================' $null
        & $add ' Up/Down pick   Left/Right change   Enter done' $null
        & $add '' $null
        & $add ("Duration (hours) :  < {0} >   ({1} s)" -f $dur, $durSec) 0
        & $add ("Press keys       :  {0}   {1}" -f (& $tog $s.Engage), $(if ($s.Engage) { 'fly the ship' } else { 'dry run' })) 1
        & $add ("Record log       :  {0}" -f (& $tog $s.Record)) 2
        & $add ("Auto-plot route  :  {0}   {1}" -f (& $tog $s.RoutePlot), $(if ($s.RoutePlot) { 'auto-plot' } else { 'use YOUR route' })) 3
        & $add ("Steering         :  {0}{1}" -f $steer, $steerHint) 4
        & $add '' $null
        & $add ("Config   : {0}" -f $cfg) $null
        & $add ("Command  : {0}" -f $cmd) $null
        & $add '' $null
        & $add $warn $null
        & $add '==============================================' $null
        return $L
    }

    # Allocate the menu's screen rows, then redraw in place each keypress.
    $height = (Build-Lines $s $sel $vis $configPath $venvPython).Count
    1..$height | ForEach-Object { Write-Host "" }
    $top = [Console]::CursorTop - $height

    while ($true) {
        [Console]::SetCursorPosition(0, $top)
        foreach ($ln in (Build-Lines $s $sel $vis $configPath $venvPython)) {
            $selected = ($ln.Row -ne $null -and $ln.Row -eq $sel)
            $prefix = if ($selected) { '> ' } else { '  ' }
            $text = $prefix + $ln.Text
            if ($text.Length -gt $W) { $text = $text.Substring(0, $W - 1) + '~' }
            else { $text = $text.PadRight($W) }
            if ($selected) { Write-Host $text -ForegroundColor Black -BackgroundColor Cyan }
            else { Write-Host $text }
        }
        $key = [Console]::ReadKey($true)
        $row = $rowKeys[$sel]
        $onSteering = ($row -eq 'Steering')
        $action = 'none'
        switch ($key.Key) {
            'UpArrow'    { $sel = ($sel - 1 + $rowKeys.Count) % $rowKeys.Count }
            'DownArrow'  { $sel = ($sel + 1) % $rowKeys.Count }
            'LeftArrow'  { if ($onSteering) { $action = 'calibrate' } else { Set-RowValue $s $row -1 } }
            'RightArrow' { if ($onSteering) { $action = 'calibrate' } else { Set-RowValue $s $row 1 } }
            'Spacebar'   { if ($onSteering) { $action = 'calibrate' } else { Set-RowValue $s $row 1 } }
            'Enter'      { if ($onSteering) { $action = 'calibrate' } else { $action = 'done' } }
            'Escape'     { $action = 'done' }
        }

        if ($action -eq 'done') {
            [Console]::SetCursorPosition(0, $top + $height)
            return
        }
        if ($action -eq 'calibrate') {
            # Drop below the menu, run calibrate-compass with its own output,
            # then re-read [vision].enabled and re-allocate the menu beneath it.
            [Console]::SetCursorPosition(0, $top + $height)
            Write-Host ""
            Write-Host "[launch] calibrate-compass -- get in the cockpit, nav-compass visible..."
            Push-Location $ProjectRoot
            try { & $venvPython -m ed_autojump.cli calibrate-compass } finally { Pop-Location }
            Write-Host ""
            Write-Host "  (paste the [vision] block above into config.toml, then re-pick Steering)"
            Write-Host ""
            $vis = Test-VisionEnabled $configPath
            $height = (Build-Lines $s $sel $vis $configPath $venvPython).Count
            1..$height | ForEach-Object { Write-Host "" }
            $top = [Console]::CursorTop - $height
        }
    }
}

function Read-Proceed {
    # y = launch, n = back to the menu to change settings, q = quit the script.
    while ($true) {
        $a = Read-Host "Proceed?  [y] launch   [n] change settings   [q] quit"
        switch -regex ($a) {
            '^[Yy]' { return 'y' }
            '^[Nn]' { return 'n' }
            '^[Qq]' { return 'q' }
            default { Write-Host "  type y, n, or q." }
        }
    }
}

# --- passthrough mode (advanced) -------------------------------------------
# Anything that isn't the standard run (doctor, calibrate-compass, etc.) goes
# straight to the CLI with no review/focus.

$env:PYTHONUTF8 = "1"

if ($Extra -and $Extra.Count -gt 0) {
    Write-Host "[launch] passthrough: $($Extra -join ' ')"
    $passArgs = @("-m", "ed_autojump.cli") + $Extra
    Push-Location $ProjectRoot
    try {
        & $venvPython @passArgs
        exit $LASTEXITCODE
    } finally {
        Pop-Location
    }
}

# --- gather settings (starting state from the flags) -----------------------

$s = @{
    DurationHours = $DurationHours
    Engage        = $true
    Record        = (-not $NoRecord)
    RoutePlot     = $RoutePlot.IsPresent
}
$visionOn = Test-VisionEnabled $configPath

# --- interactive review: menu, then y / n / q ------------------------------

if ($Yes) {
    # Unattended: keep the flags, print a one-line summary, go.
    Write-Host ("[launch] {0}h, engage={1}, record={2}, route-plot={3}, steering={4}" -f `
        $s.DurationHours, $s.Engage, $s.Record, $s.RoutePlot, $(if ($visionOn) { 'ON' } else { 'OFF' }))
} else {
    while ($true) {
        Invoke-SettingsMenu $s $visionOn $configPath $venvPython
        $choice = Read-Proceed
        if ($choice -eq 'y') { break }
        if ($choice -eq 'q') { Write-Host "[launch] quit."; exit 0 }
        # 'n' -> loop, re-show the menu with the current settings
    }
}

$cliArgs = Get-CliArgs $s

# --- pre-flight: is Elite running? -----------------------------------------

$ed = Get-Process -Name EliteDangerous64, EliteDangerous32 -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $ed) {
    Write-Host "[launch] WARNING: Elite Dangerous does not appear to be running"
    Write-Host "         (no EliteDangerous64 window found). Start it and get in"
    Write-Host "         the cockpit first."
    if (-not (Read-YesNo "Continue anyway?" $false)) {
        Write-Host "[launch] aborted."
        exit 0
    }
}

# --- focus Elite, then run -------------------------------------------------

if (-not $NoFocus) {
    Write-Host ""
    Write-Host "Make sure ED is in the cockpit. Focusing Elite -- keep it in front!"
    foreach ($n in 5, 4, 3, 2, 1) {
        Write-Host "  activating in $n..."
        Start-Sleep -Seconds 1
    }
    if (Set-EliteForeground) {
        Write-Host "[launch] Elite focused. Starting bot -- do NOT click away."
        Start-Sleep -Milliseconds 800   # let the focus change settle
    } else {
        Write-Host "[launch] could not focus Elite automatically -- click the ED"
        Write-Host "         window NOW so the keys land there."
        Start-Sleep -Seconds 2
    }
}

Write-Host "[launch] $venvPython $($cliArgs -join ' ')"

# Run FROM the project dir so the CLI finds config.toml (its --config default
# is cwd-relative) and resolves log/calibration/sessions dirs as documented.
Push-Location $ProjectRoot
try {
    & $venvPython @cliArgs
    exit $LASTEXITCODE
} finally {
    Pop-Location
}

<#
.SYNOPSIS
    Game-like launcher for ED-AFK.

    A main menu (Jump / Combat / Explore / Trade / Settings / Quit) declares your
    INTENT on entry. Only "Jump" is live today -- it runs the ed-autojump loop we
    have been building. Combat / Explore / Trade are Soon(TM) placeholders.

    Picking Jump focuses the Elite window, counts down, and starts the bot so its
    keypresses land in the game instead of wherever your mouse last was.

    Run  .\launch.ps1 -Help  for a plain-English guide.
#>

[CmdletBinding(PositionalBinding = $false)]
param(
    [double]$DurationHours = 6.0,   # seeds the Duration selector (mapped to the nearest preset)
    [switch]$Infinite,              # start with Duration = Infinite (~1yr stand-in until a real unbounded loop)
    [switch]$Monitor,               # start with Monitor-Only ON (log only -- no key presses)
    [switch]$NoRecord,              # start with Record OFF
    [switch]$NoVisitedLog,          # start with the visited-systems log OFF
    [switch]$Yes,                   # skip the menu entirely; go straight to Jump from the flags
    [switch]$NoFocus,               # do NOT focus the Elite window before starting (debugging only)
    [switch]$PrintCmd,              # resolve settings, PRINT the cli command, and exit (no focus, no run)
    [switch]$Help,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Extra
)

# TODO (next pass -- Operator 2026-06-13): wire the FEATURES section in Settings.
#   Ship features (scoop-refuel...) and Script features (dock-servicing, smack-
#   recovery, interdiction, body-tour...) get checkbox toggles grouped by kind.
#   Several map to config.local.toml rather than CLI flags -- map each honestly
#   before exposing it. Shown today as a dim "Soon(TM)" placeholder so the
#   structure is visible without faking a toggle that connects to nothing.

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
# Render the (TM) joke + box glyphs correctly in a modern console. Harmless if it fails.
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

function Show-FriendlyHelp {
    Write-Host @'

================  ED-AFK  ---  plain-English guide  ================

WHAT THIS DOES
  You fly Elite Dangerous into a system and plot a route. Then this runs the
  robot that presses the keys to dodge the star, honk, scoop fuel, and jump --
  over and over -- so you do not have to sit there.

  IT DOES NOT launch the game or click menus. YOU start ED and get in the
  cockpit yourself. This only does the jumping.

THE MENU
  E D - A F K
    Jump        <- the only live action today: runs the jump loop
    Combat      Soon(TM)
    Explore     Soon(TM)
    Trade       Soon(TM)
    Settings    <- knobs (see below)
    Quit

  Arrow keys move; Enter selects; Q quits. Picking Jump focuses Elite, counts
  down 5..1, and starts the bot. KEEP ELITE IN FRONT after that -- if you click
  away, the next keypress misses. Ctrl+C in this terminal stops a run.

SETTINGS  (RUN group -- all live today)
  Monitor-Only    log only; the bot reports what it WOULD do, sends no keys.
                  OFF by default == the bot actually flies.
  Record session  save a JSONL log of the run (ON by default).
  Log systems     append every visited system to ~/Documents (ON by default).
  Duration        1h / 6h / 12h / Infinite. Infinite is a ~1-year stand-in;
                  ED will not last that long -- a real unbounded loop comes later.
  Calibrate       run calibrate-compass so the bot can orient via the nav-compass.

DO THIS FIRST  (or the ship will not move!)
  1. ONE TIME:  .\launch.ps1 install-binds   -- adds the "ED-AFK" keyboard
     preset to Elite. Then in ED > Controls, pick "ED-AFK".
  2. Calibrate compass (Settings > Calibrate, or .\launch.ps1 calibrate-compass)
     and set [vision].enabled = true in config.toml -- else the bot jumps BLIND.
  3. Be IN THE GAME, in your ship, with a route plotted in the Galaxy Map.

HOW TO RUN
  .\launch.ps1            the menu
  .\launch.ps1 -Yes       skip the menu, go straight to Jump (uses the flags)
  .\launch.ps1 -PrintCmd  print the exact cli command the flags resolve to, exit
  .\launch.ps1 -Help      show this

THE SEED FLAGS  (just set the menu's starting values)
  -DurationHours N   nearest preset (default 6 -> "6h").
  -Infinite          start on "Infinite".
  -Monitor           start with Monitor-Only ON (log only).
  -NoRecord          start with Record OFF.
  -NoVisitedLog      start with the visited-systems log OFF.
  -Yes               skip the menu + confirm; Jump straight from the flags.
  -NoFocus           do not grab the Elite window (debugging only).

PASS-THROUGH (advanced; skips the menu + focus)
    .\launch.ps1 doctor
    .\launch.ps1 calibrate-compass
    .\launch.ps1 install-binds

===================================================================

'@
}

# The Python project lives in a subfolder; this script sits at the repo root.
# Resolve the root no matter how we were invoked: $PSScriptRoot is empty when the
# body is dot-sourced/pasted, so fall back to the invocation path, then cwd.
$RepoRoot = $PSScriptRoot
if (-not $RepoRoot -and $MyInvocation.MyCommand.Path) {
    $RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}
if (-not $RepoRoot) { $RepoRoot = (Get-Location).Path }
# Belt-and-suspenders canonicalization (NOT the load-bearing fix). Resolve-Path's
# ProviderPath normalizes casing, '..' segments, and relative artifacts to a clean
# absolute path. It does NOT collapse the C:\Users\...\Documents NTFS junction ->
# G:\Documents (verified: ProviderPath returns the C-form when invoked from the
# junction). The real junction/stale-editable correction lives in the realpath
# probe below; this line just guarantees $RepoRoot is a tidy literal path before we
# Join-Path off it.
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).ProviderPath
if (-not (Test-Path (Join-Path $RepoRoot "projects\ed-autojump"))) {
    Write-Error "Cannot find projects\ed-autojump under '$RepoRoot' -- run launch.ps1 from the repo root."
    exit 2
}
$ProjectRoot = Join-Path $RepoRoot "projects\ed-autojump"
$venvDir = Join-Path $ProjectRoot ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$configPath = Join-Path $ProjectRoot "config.toml"

# Layer 1/2 child-lifetime ownership (kill-on-close job object + taskkill /T
# fallback). Dot-source the helper so the long-running child (run / passthrough)
# can never outlive this launcher. See launch_job.ps1 for the full rationale.
. (Join-Path $RepoRoot "launch_job.ps1")

function Invoke-OwnedChild {
    # Shared spawn-and-own wrapper for the two long-running child sites
    # (main run + passthrough). Spawns under a kill-on-close job (Layer 1),
    # captures the child PID, and on ANY graceful exit of this launcher tears
    # the child down via taskkill /T as a belt-and-suspenders fallback
    # (Layer 2). Returns the child's exact exit code for re-propagation.
    param(
        [Parameter(Mandatory)][string[]]$ChildArgs
    )
    $spawn = Start-ChildInJob -FilePath $venvPython -Arguments $ChildArgs `
        -WorkingDirectory $ProjectRoot
    Write-Host ("[launch] child PID {0} (job={1})" -f `
        $spawn.ChildPid, $(if ($spawn.JobUsed) { 'on' } else { 'OFF-fallback' }))
    try {
        # WaitForExit keeps $spawn (and its open job handle) alive for the whole
        # run, so the kill-on-close guarantee holds until the child is gone.
        # Ctrl+C in this console reaches the child directly (it shares our
        # signal group); we just wait and then re-propagate the exit code.
        $code = Wait-ChildAndPropagate -Spawn $spawn
        return $code
    } finally {
        # Graceful / Ctrl+C teardown. Two belts:
        #  (a) TerminateJobObject — the EXPLICIT, reliable kill of the whole job
        #      (child + descendants). We call this rather than leaning on the
        #      implicit kill-on-close-on-handle-close, which is environment
        #      sensitive. This is the guarantee for every path where this
        #      finally actually runs.
        #  (b) Stop-ChildTree (taskkill /T by PID) — Layer 2, covers the case
        #      where the job was never created/assigned (assignment failed).
        # Hard-kill of the launcher runs NEITHER of these; that case relies on
        # KILL_ON_JOB_CLOSE firing when the kernel force-closes our last handle
        # (the documented OS guarantee). CloseHandle is still called so that
        # path is armed.
        if ($spawn.JobHandle -ne [IntPtr]::Zero) {
            [void][EDAFK.JobApi]::TerminateJobObject($spawn.JobHandle, 1)
        }
        Stop-ChildTree -ChildPid $spawn.ChildPid
        if ($spawn.JobHandle -ne [IntPtr]::Zero) {
            [void][EDAFK.JobApi]::CloseHandle($spawn.JobHandle)
        }
    }
}

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
        if (-not (Test-Path $venvPython)) { & py -3 -m venv $venvDir }
    } else {
        & python -m venv $venvDir
    }
    if (-not (Test-Path $venvPython)) {
        Write-Error "venv creation failed -- no python at $venvPython"
        exit 2
    }
}

# Probe the install: ed_autojump must be importable AND resolve INSIDE this
# project tree. Exit codes: 0 = good; 1 = not importable (fresh venv / moved
# repo); 2 = importable but resolving OUT OF TREE. (2) is the stale-editable
# trap: a `pip install -e` once run from a council/agent worktree baked that
# worktree's absolute path into __editable__.ed_autojump.pth, so ed_autojump kept
# importing a FROZEN out-of-tree snapshot -- importable, so the old find_spec-only
# check passed, and the bot ran OLD code no matter what was committed to the live
# tree (cost days, 2026-06-16). Treat (2) like (1) and reinstall so the editable
# path self-heals to $ProjectRoot.
#
# REALPATH ON BOTH SIDES (the load-bearing fix, 2026-06-16): C:\Users\...\Documents
# is an NTFS junction -> G:\Documents. When the operator launches from the C-form
# junction path, $ProjectRoot is C-form but the editable origin is G-form -- the
# SAME physical directory. The old abspath+startswith compared C-form root against
# G-form origin, mismatched, and fired a pointless --force-reinstall on EVERY
# launch (which then hard-failed offline). os.path.realpath collapses the junction
# on BOTH sides so the C-aliased root and G-form origin compare equal. A genuinely
# out-of-tree origin (a worktree .pth) still mismatches -> 2 (protection intact:
# realpath leaves a nonexistent C: worktree path literal C-form). The in-tree test
# is `origin == root OR origin startswith root + os.sep` so a sibling like
# ...\ed-autojump2 cannot prefix-match ...\ed-autojump.
#
# Deliberately NO stderr redirect / NO bare `import` (PS 5.1 wraps a redirected
# native-stderr line in a terminating NativeCommandError that would kill this
# script). Path is passed via env to dodge backslash-in-string quoting.
$env:EDAFK_PROJECT_ROOT = $ProjectRoot
& $venvPython -c "import importlib.util as u, sys, os; root=os.path.normcase(os.path.realpath(os.environ['EDAFK_PROJECT_ROOT'])); s=u.find_spec('ed_autojump'); o=os.path.normcase(os.path.realpath(s.origin)) if (s and s.origin) else None; sys.exit(1) if o is None else sys.exit(0 if (o == root or o.startswith(root + os.sep)) else 2)"
$probeCode = $LASTEXITCODE
$env:EDAFK_PROJECT_ROOT = ''

function Ensure-BuildTools {
    # OFFLINE-SAFE build-backend seeding. pyproject declares
    # [build-system] requires=["setuptools>=68","wheel"] with backend
    # setuptools.build_meta, but this venv has NEITHER setuptools nor wheel. Under
    # default PEP-517 build isolation pip would fetch them from PyPI -- which HARD-
    # FAILS with no network and turns a self-heal into a launch-blocking error.
    #
    # Fix: vendor setuptools (and wheel, if a donor has it) into the venv's own
    # site-packages by copying from a DONOR interpreter (sys.base_prefix python, or
    # a bare `python` on PATH) that already ships them. No network, no PyPI. Once
    # setuptools is importable the editable build runs with --no-build-isolation
    # against the venv's own interpreter. Modern setuptools (>=70) vendors all
    # wheel-building machinery, so the standalone `wheel` package is NOT actually
    # required for an editable build (verified: build_editable succeeds with only
    # setuptools present); we still seed wheel when a donor offers it, but only
    # setuptools is mandatory. Returns $true if setuptools ends up importable.
    $seed = @'
import importlib.util as U, os, sys, glob, shutil, subprocess, sysconfig
def have(m):
    try:
        return U.find_spec(m) is not None
    except Exception:
        return False
def donor(m):
    cands = []
    b = os.path.join(sys.base_prefix, "python.exe")
    if os.path.isfile(b):
        cands.append(b)
    cands.append("python")
    for py in cands:
        if os.path.normcase(os.path.abspath(py)) == os.path.normcase(os.path.abspath(sys.executable)):
            continue
        try:
            o = subprocess.run([py, "-c", "import importlib.util as u,os;s=u.find_spec(%r);print(os.path.dirname(s.submodule_search_locations[0]) if (s and s.submodule_search_locations) else (os.path.dirname(s.origin) if s and s.origin else str()))" % m], capture_output=True, text=True, timeout=30)
            p = o.stdout.strip()
            if p and os.path.isdir(p):
                return p
        except Exception:
            pass
    return None
def vendor(m, dest):
    sp = donor(m)
    if not sp:
        return False
    md = os.path.join(sp, m)
    if not os.path.isdir(md):
        return False
    shutil.copytree(md, os.path.join(dest, m), dirs_exist_ok=True)
    for di in glob.glob(os.path.join(sp, m + "-*.dist-info")):
        shutil.copytree(di, os.path.join(dest, os.path.basename(di)), dirs_exist_ok=True)
    if m == "setuptools":
        for e in ("pkg_resources", "_distutils_hack"):
            ed = os.path.join(sp, e)
            if os.path.isdir(ed):
                shutil.copytree(ed, os.path.join(dest, e), dirs_exist_ok=True)
        ph = os.path.join(sp, "distutils-precedence.pth")
        if os.path.isfile(ph):
            shutil.copy2(ph, os.path.join(dest, "distutils-precedence.pth"))
    return True
dest = sysconfig.get_paths()["purelib"]
need = [m for m in ("setuptools", "wheel") if not have(m)]
for m in need:
    print("[launch] seed build tool: %s -> %s" % (m, "OK" if vendor(m, dest) else "no offline donor (skipped)"))
sys.exit(0 if have("setuptools") else 3)
'@
    & $venvPython -c $seed
    return ($LASTEXITCODE -eq 0)
}

if ($probeCode -ne 0) {
    $pkgSpec = $ProjectRoot + "[dev,hotkey,vision]"
    # GUARANTEE the editable build can complete OFFLINE before invoking pip: the
    # backend needs setuptools and this venv has none. Seed (vendor) the build
    # tools from a donor interpreter, then build with --no-build-isolation (which
    # is valid ONLY once setuptools is importable -- a bare --no-build-isolation
    # would fail here today). This keeps the self-heal network-free (INV-4).
    if (-not (Ensure-BuildTools)) {
        Write-Error "could not seed setuptools offline -- no donor interpreter has it (need a base Python with setuptools, or network access)"
        exit 2
    }
    if ($probeCode -eq 2) {
        Write-Host "[launch] ed_autojump resolves OUT OF TREE (stale editable .pth from a worktree) -- repointing editable to the live tree..."
        & $venvPython -m pip install -e $pkgSpec --force-reinstall --no-deps --no-build-isolation
    } else {
        Write-Host "[launch] ed_autojump not importable (fresh venv, or the repo moved) -- (re)installing editable..."
        & $venvPython -m pip install -e $pkgSpec --no-build-isolation
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Error "pip install failed"
        exit 2
    }
}

# --- static menu data ------------------------------------------------------

# Duration presets. "Infinite" is a ~1-year stand-in: the launcher just passes a
# big --duration so the loop runs until ED dies or you Ctrl+C. A real unbounded
# loop is a later job (Operator: "Elite won't last infinitely, we'll fix that later").
$script:DurationPresets = @(
    @{ Label = '1h';       Seconds = 3600 },
    @{ Label = '6h';       Seconds = 21600 },
    @{ Label = '12h';      Seconds = 43200 },
    @{ Label = 'Infinite'; Seconds = 31536000 }
)

# Main-menu rows. Live=$false rows are navigable + show their tag but Enter is a
# no-op (Soon(TM)). Only Jump runs the bot today.
$script:MainItems = @(
    @{ Key = 'jump';     Label = 'Jump';     Tag = 'ready';    Live = $true },
    # Scenes (OPERATOR 2026-07-07): drive ONE named procedure with the full
    # live wiring, then exit -- the cli --scene RC mode, now menu-reachable.
    @{ Key = 'scenes';   Label = 'Scenes';   Tag = 'one-shot'; Live = $true },
    @{ Key = 'combat';   Label = 'Combat';   Tag = ("Soon" + [char]0x2122); Live = $false },
    @{ Key = 'trade';    Label = 'Trade';    Tag = ("Soon" + [char]0x2122); Live = $false },
    @{ Key = 'settings'; Label = 'Settings'; Tag = '';         Live = $true },
    @{ Key = 'quit';     Label = 'Quit';     Tag = '';         Live = $true }
)

# Scene picker rows (Scenes menu). Order = frequency of live use. Names must
# match the procedure TOMLs in projects\ed-autojump\procedures\.
$script:SceneNames = @(
    'startup', 'sc_resume', 'arrival', 'traversal', 'smack_recovery',
    'exploration', 'dock', 'dock_resume', 'route_complete_park', 'honk'
)

# Settings rows. Only toggle/envtoggle/cycle/action/back are navigable;
# header/blank/soon are decoration. RUN + VISION groups are wired today;
# FEATURES is still a placeholder.
#   toggle    -> RUN flags; flow into Get-CliArgs (CLI args).
#   envtoggle -> persistent config opt-in written to $ProjectRoot\.env via
#                Set-DotEnvKey. NEVER touches Get-CliArgs. EnvKey names the var.
$script:SettingsRows = @(
    @{ Kind = 'header'; Label = 'RUN' },
    @{ Kind = 'toggle'; Key = 'Monitor';   Label = 'Monitor-Only   (log only, no key presses)' },
    @{ Kind = 'toggle'; Key = 'Record';     Label = 'Record session' },
    @{ Kind = 'toggle'; Key = 'LogVisited'; Label = 'Log systems visited (~/Documents)' },
    @{ Kind = 'cycle';  Key = 'Duration';   Label = 'Duration' },
    @{ Kind = 'action'; Key = 'Calibrate'; Label = 'Calibrate compass (steering vision)' },
    @{ Kind = 'blank' },
    @{ Kind = 'header'; Label = 'VISION' },
    @{ Kind = 'envtoggle'; EnvKey = 'ED_AUTOJUMP_OVERLAY_CV_DEBUG'; Label = 'CV debug overlay (boxes + labels where the CV looks)' },
    @{ Kind = 'blank' },
    @{ Kind = 'header'; Label = 'FEATURES' },
    @{ Kind = 'soon';   Label = 'Ship / Script toggles' },
    @{ Kind = 'blank' },
    @{ Kind = 'back';   Label = 'Back to menu' }
)

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
    # Bring the Elite Dangerous window to the foreground so SendInput lands in the
    # game. Returns $true if we found + focused a window.
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

function Resolve-DurationIndex([double]$hours, [bool]$infinite) {
    # Map the -DurationHours seed to the nearest non-Infinite preset, or pick
    # Infinite outright if -Infinite was passed.
    if ($infinite) { return ($script:DurationPresets.Count - 1) }
    $bestI = 1; $bestD = [double]::MaxValue
    for ($i = 0; $i -lt $script:DurationPresets.Count; $i++) {
        $p = $script:DurationPresets[$i]
        if ($p.Label -eq 'Infinite') { continue }
        $ph = $p.Seconds / 3600.0
        $d = [Math]::Abs($ph - $hours)
        if ($d -lt $bestD) { $bestD = $d; $bestI = $i }
    }
    return $bestI
}

function Get-CliArgs([hashtable]$s) {
    # Single source of truth for the cli invocation, shared by -PrintCmd and Jump.
    # Monitor-Only inverts engage-keys: checked == --no-engage-keys (NullSender).
    $secs = $script:DurationPresets[$s.DurationIndex].Seconds
    $a = @("-m", "ed_autojump.cli", "run", "--duration", $secs)
    if ([bool]$s.Monitor) { $a += "--no-engage-keys" } else { $a += "--engage-keys" }
    if ([bool]$s.Record) { $a += "--record" }
    if ([bool]$s.LogVisited) { $a += "--visited-log" } else { $a += "--no-visited-log" }
    # Scenes menu (operator 2026-07-07): one-shot named procedure via the cli
    # --scene RC mode. All other flags (keys, record, visited) apply as-is.
    if ($s.Scene) { $a += @("--scene", $s.Scene) }
    return , $a
}

# --- .env opt-in helpers (CV debug overlay) --------------------------------
# The VISION toggle persists ED_AUTOJUMP_OVERLAY_CV_DEBUG into
# $ProjectRoot\.env -- a local, gitignored file the bot's config loader reads
# (_load_dotenv -> _apply_env_overrides). This NEVER flows through Get-CliArgs:
# it is config, not a CLI flag. config.py's .env parser is mirrored exactly
# below (KEY=VALUE, '#' comments, optional surrounding quotes; a REAL env var
# of the same name WINS, matching the bot's "key not in environ" rule).

function ConvertTo-EnvBool([string]$raw) {
    # Mirror config.py: truthy {1,true,yes,on}, falsy {0,false,no,off}; anything
    # else (or empty) -> $null so the caller can fall through to the next source.
    if ($null -eq $raw) { return $null }
    $v = $raw.Trim().Trim("'").Trim('"').ToLowerInvariant()
    if ('1', 'true', 'yes', 'on' -contains $v) { return $true }
    if ('0', 'false', 'no', 'off' -contains $v) { return $false }
    return $null
}

function Get-DotEnvValue([string]$path, [string]$key) {
    # First non-comment KEY=VALUE line whose key matches; else $null. Mirrors
    # config.py _load_dotenv parsing (strip, skip blank/'#'/no-'=', partition on
    # first '=', strip surrounding quotes).
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    foreach ($raw in [System.IO.File]::ReadAllLines($path)) {
        $line = $raw.Trim()
        if (-not $line) { continue }
        if ($line.StartsWith('#')) { continue }
        $eq = $line.IndexOf('=')
        if ($eq -lt 1) { continue }
        $k = $line.Substring(0, $eq).Trim()
        if ($k -eq $key) {
            $val = $line.Substring($eq + 1).Trim()
            return $val.Trim("'").Trim('"')
        }
    }
    return $null
}

function Get-CvDebugEnv([string]$projectRoot, [string]$key = 'ED_AUTOJUMP_OVERLAY_CV_DEBUG') {
    # Effective state of the CV debug overlay opt-in, [bool]. Self-contained:
    # the key name defaults inline so the helper has no module-scope dependency.
    # Precedence (matches the bot): real env var > .env key > $false (default off).
    $envRaw = [System.Environment]::GetEnvironmentVariable($key)
    $fromEnv = ConvertTo-EnvBool $envRaw
    if ($null -ne $fromEnv) { return $fromEnv }
    $dotPath = Join-Path $projectRoot '.env'
    $fromDot = ConvertTo-EnvBool (Get-DotEnvValue $dotPath $key)
    if ($null -ne $fromDot) { return $fromDot }
    return $false
}

function Set-DotEnvKey([string]$path, [string]$key, [string]$value) {
    # Idempotently set KEY=value in a .env file: replace the FIRST non-comment
    # line whose trimmed key equals $key, else append. Preserve every other line
    # verbatim (comments, blanks, unrelated keys, order). Create the file if
    # absent. Write UTF-8 with NO BOM (config.py reads UTF-8 -- a BOM would
    # corrupt the first key). PS 5.1's Out-File/Set-Content default to UTF-16 or
    # BOM'd UTF-8, so we use .NET WriteAllLines with a no-BOM UTF8Encoding.
    $lines = @()
    if (Test-Path -LiteralPath $path) {
        $lines = @([System.IO.File]::ReadAllLines($path))
    }
    $newLine = "$key=$value"
    $done = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $line = $lines[$i]
        $trimmed = $line.Trim()
        if (-not $trimmed) { continue }
        if ($trimmed.StartsWith('#')) { continue }
        $eq = $trimmed.IndexOf('=')
        if ($eq -lt 1) { continue }
        $k = $trimmed.Substring(0, $eq).Trim()
        if ($k -eq $key) {
            $lines[$i] = $newLine
            $done = $true
            break
        }
    }
    if (-not $done) { $lines += $newLine }

    $parent = Split-Path -Parent $path
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($path, [string[]]$lines, $utf8NoBom)
}

function Draw-Lines($lines, [int]$top, [int]$W) {
    # Redraw a frame in place. Each line is {Text; Selected; Dim}.
    if ($top -lt 0) { $top = 0 }
    [Console]::SetCursorPosition(0, $top)
    foreach ($ln in $lines) {
        $text = [string]$ln.Text
        if ($text.Length -gt $W) { $text = $text.Substring(0, $W - 1) + '~' }
        else { $text = $text.PadRight($W) }
        if ($ln.Selected) { Write-Host $text -ForegroundColor Black -BackgroundColor Cyan }
        elseif ($ln.Dim) { Write-Host $text -ForegroundColor DarkGray }
        else { Write-Host $text -ForegroundColor Gray }
    }
}

function Test-Interactive {
    $ok = $true
    try { $null = [Console]::CursorTop } catch { $ok = $false }
    if ($Host.Name -eq 'Windows PowerShell ISE Host') { $ok = $false }
    return $ok
}

# --- main menu -------------------------------------------------------------

function Build-MainLines([int]$sel) {
    $L = New-Object System.Collections.Generic.List[object]
    $add = { param($t, $seld, $dim) $L.Add([pscustomobject]@{ Text = $t; Selected = $seld; Dim = $dim }) }
    & $add "" $false $false
    & $add "        E D - A F K" $false $false
    & $add "        ===========" $false $false
    & $add "" $false $false
    for ($i = 0; $i -lt $script:MainItems.Count; $i++) {
        $it = $script:MainItems[$i]
        if ($it.Key -eq 'settings') { & $add "" $false $false }   # spacer before Settings
        $isSel = ($i -eq $sel)
        $marker = if ($isSel) { '> ' } else { '  ' }
        $text = "    {0}{1}{2}" -f $marker, $it.Label.PadRight(12), $it.Tag
        & $add $text $isSel (-not $it.Live)
    }
    & $add "" $false $false
    & $add "    Up/Down move | Enter select | Q quit" $false $true
    & $add "" $false $false
    return , $L
}

function Invoke-SceneMenu {
    # Scene picker: returns a procedure name, or $null on back/quit.
    if (-not (Test-Interactive)) {
        Write-Host "`n=== SCENES ==="
        for ($i = 0; $i -lt $script:SceneNames.Count; $i++) {
            Write-Host ("  {0}) {1}" -f ($i + 1), $script:SceneNames[$i])
        }
        $a = Read-Host "choose (blank = back)"
        $n = 0
        if ([int]::TryParse($a, [ref]$n) -and $n -ge 1 -and $n -le $script:SceneNames.Count) {
            return $script:SceneNames[$n - 1]
        }
        return $null
    }
    Clear-Host
    $W = [Math]::Min(60, [Console]::BufferWidth - 1)
    $rows = @($script:SceneNames) + @('Back')
    $count = $rows.Count
    $sel = 0
    $build = {
        param($cur)
        $L = New-Object System.Collections.Generic.List[object]
        $add = { param($t, $seld, $dim) $L.Add([pscustomobject]@{ Text = $t; Selected = $seld; Dim = $dim }) }
        & $add "" $false $false
        & $add "    S C E N E S   (one-shot, full live wiring)" $false $false
        & $add "    -------------------------------------------" $false $false
        & $add "" $false $false
        for ($i = 0; $i -lt $rows.Count; $i++) {
            & $add ("    {0}" -f $rows[$i]) ($i -eq $cur) $false
        }
        & $add "" $false $false
        & $add "    Up/Down move | Enter run | Esc back" $false $true
        & $add "" $false $false
        return , $L
    }
    $height = (& $build $sel).Count
    1..$height | ForEach-Object { Write-Host "" }
    $top = [Console]::CursorTop - $height
    while ($true) {
        Draw-Lines (& $build $sel) $top $W
        $key = [Console]::ReadKey($true)
        switch ($key.Key) {
            'UpArrow'   { $sel = ($sel - 1 + $count) % $count }
            'DownArrow' { $sel = ($sel + 1) % $count }
            'Q'         { return $null }
            'Escape'    { return $null }
            { $_ -eq 'Enter' -or $_ -eq 'Spacebar' } {
                if ($sel -eq $count - 1) { return $null }   # Back
                return $rows[$sel]
            }
        }
    }
}

function Invoke-MainMenu {
    # Returns 'jump' | 'scenes' | 'settings' | 'quit'. Soon(TM) rows are navigable no-ops.
    if (-not (Test-Interactive)) {
        Write-Host "`n=== ED-AFK ===   1) Jump   2) Scenes   3) Settings   4) Quit"
        $a = Read-Host "choose"
        switch ($a) { '2' { return 'scenes' } '3' { return 'settings' } '4' { return 'quit' } default { return 'jump' } }
    }
    Clear-Host
    $W = [Math]::Min(60, [Console]::BufferWidth - 1)
    $count = $script:MainItems.Count
    $sel = 0
    $height = (Build-MainLines $sel).Count
    1..$height | ForEach-Object { Write-Host "" }
    $top = [Console]::CursorTop - $height
    while ($true) {
        Draw-Lines (Build-MainLines $sel) $top $W
        $key = [Console]::ReadKey($true)
        switch ($key.Key) {
            'UpArrow'   { $sel = ($sel - 1 + $count) % $count }
            'DownArrow' { $sel = ($sel + 1) % $count }
            'Q'         { return 'quit' }
            'Escape'    { return 'quit' }
            { $_ -eq 'Enter' -or $_ -eq 'Spacebar' } {
                $it = $script:MainItems[$sel]
                if ($it.Key -eq 'jump')     { return 'jump' }
                if ($it.Key -eq 'scenes')   { return 'scenes' }
                if ($it.Key -eq 'settings') { return 'settings' }
                if ($it.Key -eq 'quit')     { return 'quit' }
                # Soon(TM): no-op, fall through and keep drawing.
            }
        }
    }
}

# --- settings menu ---------------------------------------------------------

function Build-SettingsLines([int]$sel, [hashtable]$s, [bool]$vis, $nav) {
    $curRow = $nav[$sel]
    $L = New-Object System.Collections.Generic.List[object]
    $add = { param($t, $seld, $dim) $L.Add([pscustomobject]@{ Text = $t; Selected = $seld; Dim = $dim }) }
    & $add "" $false $false
    & $add "    S E T T I N G S" $false $false
    & $add "    ---------------" $false $false
    & $add "" $false $false
    for ($i = 0; $i -lt $script:SettingsRows.Count; $i++) {
        $r = $script:SettingsRows[$i]
        $sl = ($i -eq $curRow)
        switch ($r.Kind) {
            'header' { & $add ("  {0}" -f $r.Label) $false $true }
            'blank'  { & $add "" $false $false }
            'toggle' {
                $box = if ([bool]$s[$r.Key]) { '[x]' } else { '[ ]' }
                & $add ("    {0} {1}" -f $box, $r.Label) $sl $false
            }
            'envtoggle' {
                # Display the EFFECTIVE current value (real env var > .env > off).
                $on = Get-CvDebugEnv $ProjectRoot
                $box = if ($on) { '[x]' } else { '[ ]' }
                & $add ("    {0} {1}" -f $box, $r.Label) $sl $false
            }
            'cycle' {
                $lab = $script:DurationPresets[$s.DurationIndex].Label
                & $add ("    {0,-14} < {1} >" -f $r.Label, $lab) $sl $false
            }
            'action' {
                $st = if ($vis) { 'ON (compass)' } else { 'OFF - blind' }
                & $add ("    {0}  : {1}" -f $r.Label, $st) $sl $false
            }
            'soon' { & $add ("    {0}    Soon{1} (next pass)" -f $r.Label, [char]0x2122) $false $true }
            'back' { & $add ("    {0}" -f $r.Label) $sl $false }
        }
    }
    & $add "" $false $false
    & $add "    Up/Down move | Left/Right or Enter change | Esc back" $false $true
    & $add "" $false $false
    return , $L
}

function Invoke-SettingsMenu([hashtable]$s, [bool]$visionOn) {
    # Mutates $s in place (hashtables are by ref). Returns when Back/Esc is hit.
    if (-not (Test-Interactive)) {
        Write-Host "`n=== ED-AFK settings (Enter keeps current) ==="
        $s.Monitor   = Read-YesNo "Monitor-Only (log only, no keys)?" ([bool]$s.Monitor)
        $s.Record    = Read-YesNo "Record session log?"               ([bool]$s.Record)
        $s.RoutePlot = Read-YesNo "Auto-plot a route if none?"        ([bool]$s.RoutePlot)
        $inf = Read-YesNo "Run infinitely (until ED dies / Ctrl+C)?"  ($script:DurationPresets[$s.DurationIndex].Label -eq 'Infinite')
        if ($inf) { $s.DurationIndex = $script:DurationPresets.Count - 1 }
        else {
            $hrs = Read-Host ("Hours? [{0}]" -f ($script:DurationPresets[$s.DurationIndex].Seconds / 3600.0))
            if ($hrs) { $s.DurationIndex = Resolve-DurationIndex ([double]$hrs) $false }
        }
        return
    }

    $vis = $visionOn
    $nav = @()
    for ($i = 0; $i -lt $script:SettingsRows.Count; $i++) {
        if ($script:SettingsRows[$i].Kind -in 'toggle', 'envtoggle', 'cycle', 'action', 'back') { $nav += $i }
    }
    $sel = 0
    $W = [Math]::Min(70, [Console]::BufferWidth - 1)
    Clear-Host
    $height = (Build-SettingsLines $sel $s $vis $nav).Count
    1..$height | ForEach-Object { Write-Host "" }
    $top = [Console]::CursorTop - $height

    while ($true) {
        Draw-Lines (Build-SettingsLines $sel $s $vis $nav) $top $W
        $key = [Console]::ReadKey($true)
        $row = $script:SettingsRows[$nav[$sel]]
        $do = 'none'
        switch ($key.Key) {
            'UpArrow'    { $sel = ($sel - 1 + $nav.Count) % $nav.Count }
            'DownArrow'  { $sel = ($sel + 1) % $nav.Count }
            'LeftArrow'  { $do = 'dec' }
            'RightArrow' { $do = 'inc' }
            'Spacebar'   { $do = 'activate' }
            'Enter'      { $do = 'activate' }
            'Escape'     { return }
        }
        if ($do -eq 'none') { continue }

        switch ($row.Kind) {
            'toggle' { $s[$row.Key] = -not [bool]$s[$row.Key] }
            'envtoggle' {
                # Flip the persistent .env opt-in. Read effective state, invert,
                # write explicit =1 / =0 (=0 self-documents the opt-out). A real
                # env var of the same name overrides what we wrote until cleared,
                # so the redrawn box reflects the true effective value, not blindly
                # what we just persisted.
                $cur = Get-CvDebugEnv $ProjectRoot
                $newVal = if ($cur) { '0' } else { '1' }
                $dotPath = Join-Path $ProjectRoot '.env'
                Set-DotEnvKey $dotPath $row.EnvKey $newVal
            }
            'cycle' {
                $n = $script:DurationPresets.Count
                if ($do -eq 'dec') { $s.DurationIndex = ($s.DurationIndex - 1 + $n) % $n }
                else { $s.DurationIndex = ($s.DurationIndex + 1) % $n }
            }
            'action' {
                if ($do -eq 'activate') {
                    [Console]::SetCursorPosition(0, $top + $height)
                    Write-Host ""
                    Write-Host "[launch] calibrate-compass -- get in the cockpit, nav-compass visible..."
                    Push-Location $ProjectRoot
                    try { & $venvPython -m ed_autojump.cli calibrate-compass } finally { Pop-Location }
                    Write-Host ""
                    Write-Host "  (paste the [vision] block above into config.toml, then re-pick)"
                    $vis = Test-VisionEnabled $configPath
                    Clear-Host
                    $height = (Build-SettingsLines $sel $s $vis $nav).Count
                    1..$height | ForEach-Object { Write-Host "" }
                    $top = [Console]::CursorTop - $height
                }
            }
            'back' { if ($do -eq 'activate') { return } }
        }
    }
}

# --- passthrough mode (advanced) -------------------------------------------
# Anything that isn't the standard run (doctor, calibrate-compass, etc.) goes
# straight to the CLI with no menu / focus.

if ($Extra -and $Extra.Count -gt 0) {
    Write-Host "[launch] passthrough: $($Extra -join ' ')"
    $passArgs = @("-m", "ed_autojump.cli") + $Extra
    # Passthrough is a long-running child too (doctor/launch can sit a while),
    # so it inherits the same job-object lifetime ownership as the main run.
    # CWD = $ProjectRoot is set inside the spawn (no Push-Location needed).
    exit (Invoke-OwnedChild -ChildArgs $passArgs)
}

# --- gather settings (starting state from the flags) -----------------------

$s = @{
    Monitor       = $Monitor.IsPresent           # default OFF -> steering ON (the bot flies)
    Record        = (-not $NoRecord)             # default ON
    LogVisited    = (-not $NoVisitedLog)         # default ON
    DurationIndex = (Resolve-DurationIndex $DurationHours $Infinite.IsPresent)
}
$visionOn = Test-VisionEnabled $configPath

# --- -PrintCmd: resolve + print the cli command, then exit (no focus/run) ---

if ($PrintCmd) {
    $cli = Get-CliArgs $s
    Write-Host ("[launch] would run: {0} {1}" -f $venvPython, ($cli -join ' '))
    Write-Host ("[launch] duration={0}  monitor-only={1}  record={2}  visited-log={3}  steering(vision)={4}" -f `
        $script:DurationPresets[$s.DurationIndex].Label, $s.Monitor, $s.Record, $s.LogVisited, $(if ($visionOn) { 'ON' } else { 'OFF' }))
    exit 0
}

# --- menu: pick an action --------------------------------------------------

if ($Yes) {
    # Unattended: only Jump is live, so go straight to it from the seeded flags.
    Write-Host ("[launch] Jump (unattended): duration={0}, monitor-only={1}, record={2}, visited-log={3}, steering={4}" -f `
        $script:DurationPresets[$s.DurationIndex].Label, $s.Monitor, $s.Record, $s.LogVisited, $(if ($visionOn) { 'ON' } else { 'OFF' }))
} else {
    $launch = $false
    while (-not $launch) {
        $action = Invoke-MainMenu
        switch ($action) {
            'jump'     { $launch = $true }
            'scenes'   {
                $scene = Invoke-SceneMenu
                if ($scene) { $s.Scene = $scene; $launch = $true }
            }
            'settings' { Invoke-SettingsMenu $s $visionOn; $visionOn = Test-VisionEnabled $configPath }
            'quit'     { Write-Host "`n[launch] quit."; exit 0 }
            default    { }   # Soon(TM) no-op
        }
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

# Run FROM the project dir so the CLI finds config.toml (its --config default is
# cwd-relative) and resolves log/calibration/sessions dirs as documented. The
# spawn sets WorkingDirectory=$ProjectRoot, so the old Push-Location is folded
# into Invoke-OwnedChild. The child now lives under a kill-on-close job: it
# cannot outlive this launcher under ANY kill mode (Layer 1), with a taskkill
# /T fallback on graceful exit (Layer 2). Exit code re-propagated exactly.
exit (Invoke-OwnedChild -ChildArgs $cliArgs)

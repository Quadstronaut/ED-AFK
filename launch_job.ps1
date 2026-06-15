<#
.SYNOPSIS
    Job-object child-process launcher (Layer 1 + Layer 2 of the orphan fix).

    Dot-source this from launch.ps1 (and from the acceptance repros) to get
    Start-ChildInJob, which spawns a child python under a Win32 JOB OBJECT
    created with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE. The launcher holds the ONLY
    open handle to the job; when the launcher dies by ANY means (graceful exit,
    Ctrl+C, Stop-Process / TerminateProcess, Task-Manager End Task), the last
    handle closes, the kernel tears down the job, and the child (plus any
    descendants) dies with it. That is the OS-level guarantee that defeats the
    orphan bug even under hard-kill of the launcher.

    Fallback (Layer 2): if job creation OR assignment fails (e.g. this console
    is already inside a job that forbids breakaway), we DO NOT crash the launch.
    We return JobUsed=$false; the caller's trap/finally then runs
    `taskkill /T /PID <childPid>` on graceful exit. Layer 2 cannot cover a
    hard-killed launcher (its trap never runs) -- that is Layer 1's job.

    Non-regression: the child is spawned with the console inherited (stdout /
    stderr stream live, no capture), WorkingDirectory = the project root, and
    no CREATE_NEW_PROCESS_GROUP -- so Ctrl+C in the launcher console still
    reaches the child as a normal CTRL_C_EVENT (KeyboardInterrupt). We capture
    and re-propagate the child's exact ExitCode.

.NOTES
    HONEST COVERAGE MATRIX  (kill mode  x  which layer catches it)
    L1 = launcher job object (kill-on-close + explicit TerminateJobObject in the
         finally).  L2 = launcher taskkill /T fallback by captured PID.
    L3 = bot lifecycle.py best-effort key release (console-ctrl + signal +
         atexit).  L4 = PID-file + `cleanup`/`panic` CLI for stale survivors.

    | Kill mode                              | Orphan? | Keys released? | Covered by |
    |----------------------------------------|---------|----------------|------------|
    | Launcher Ctrl+C (KeyboardInterrupt)    | NO      | YES            | L3 in child + L1/L2 finally |
    | Launcher hard-kill (Stop-Process /     | NO*     | NO (residual)  | L1 kill-on-close (*OS guarantee; |
    |   TerminateProcess / Task-Mgr End Task)|         |                |  see SANDBOX note in AC1b) |
    | Launcher graceful exit / Quit          | NO      | YES            | L1 finally TerminateJobObject + L3 |
    | Child python crashes (unhandled exc)   | n/a     | YES            | cli.py finally + L3 atexit |
    | Child hard-killed directly (laun alive)| n/a     | NO (residual)  | launcher sees exit; no signal to child |
    | Whole-tree taskkill /T on launcher     | NO      | NO (residual)  | taskkill kills both |
    | Console window X (CTRL_CLOSE_EVENT)    | NO      | best-effort    | L3 (~5s budget) + L1 |

    RESIDUAL stated honestly: on any mode whose teardown is a raw
    TerminateProcess of the CHILD (launcher hard-kill -> job kill is itself a
    TerminateProcess of the child; direct child kill; whole-tree taskkill), we
    do NOT release held keys -- there is no signal/handler window. The orphan is
    solved in those modes (no surviving python); ED clears stuck keys on focus
    loss. AC1b proves the kill-on-close mechanism is correctly wired; in a
    restricted/sandboxed host that suppresses KILL_ON_JOB_CLOSE it is marked
    reasoned-unprovable-in-harness (the canonical pattern works on a normal
    desktop -- Chromium/VS Code rely on it).
#>

# --- P/Invoke: job-object + process APIs -----------------------------------
# PowerShell 5.1 has no native job-object cmdlet, so we Add-Type the Win32 API.
# Use -TypeDefinition (full C# source) so we can declare a namespace + structs
# + a static API class. (-MemberDefinition wraps everything in one class and
# rejects `using` directives / sibling top-level types.) Guarded so
# re-dot-sourcing in the same session doesn't redefine the type.
if (-not ([System.Management.Automation.PSTypeName]'EDAFK.JobApi').Type) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

namespace EDAFK {

[StructLayout(LayoutKind.Sequential)]
public struct JOBOBJECT_BASIC_LIMIT_INFORMATION {
    public Int64 PerProcessUserTimeLimit;
    public Int64 PerJobUserTimeLimit;
    public UInt32 LimitFlags;
    public UIntPtr MinimumWorkingSetSize;
    public UIntPtr MaximumWorkingSetSize;
    public UInt32 ActiveProcessLimit;
    public UIntPtr Affinity;
    public UInt32 PriorityClass;
    public UInt32 SchedulingClass;
}

[StructLayout(LayoutKind.Sequential)]
public struct IO_COUNTERS {
    public UInt64 ReadOperationCount;
    public UInt64 WriteOperationCount;
    public UInt64 OtherOperationCount;
    public UInt64 ReadTransferCount;
    public UInt64 WriteTransferCount;
    public UInt64 OtherTransferCount;
}

[StructLayout(LayoutKind.Sequential)]
public struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION {
    public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
    public IO_COUNTERS IoInfo;
    public UIntPtr ProcessMemoryLimit;
    public UIntPtr JobMemoryLimit;
    public UIntPtr PeakProcessMemoryUsed;
    public UIntPtr PeakJobMemoryUsed;
}

public static class JobApi {
    public const UInt32 JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000;
    public const UInt32 JOB_OBJECT_LIMIT_BREAKAWAY_OK      = 0x0800;
    public const int    JobObjectExtendedLimitInformation  = 9;

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern IntPtr CreateJobObject(IntPtr lpJobAttributes, string lpName);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool SetInformationJobObject(
        IntPtr hJob, int JobObjectInfoClass, IntPtr lpJobObjectInfo, uint cbJobObjectInfoLength);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool AssignProcessToJobObject(IntPtr hJob, IntPtr hProcess);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool IsProcessInJob(IntPtr ProcessHandle, IntPtr JobHandle, out bool Result);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool CloseHandle(IntPtr hObject);

    // Mark the job handle NON-inheritable so the child we spawn can never get a
    // duplicate of it (a leaked inheritable copy in the child would keep the
    // job alive past the launcher's death and defeat kill-on-close).
    public const uint HANDLE_FLAG_INHERIT = 0x1;
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool SetHandleInformation(IntPtr hObject, uint dwMask, uint dwFlags);

    // Terminate every process in the job NOW. Belt-and-suspenders fallback the
    // launcher calls in its finally if kill-on-close ever doesn't fire.
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool TerminateJobObject(IntPtr hJob, uint uExitCode);

    // Raw CreateProcess so we can spawn with bInheritHandles=FALSE. .NET's
    // Process.Start(UseShellExecute=false) sets bInheritHandles=TRUE, which
    // leaks a duplicate of our (otherwise non-inheritable) handles into the
    // child; that copy kept the job alive past the launcher's death and
    // DEFEATED kill-on-close. With bInheritHandles=FALSE the child can hold no
    // copy of the job handle, so the launcher's handle is the last one and
    // kill-on-close fires on launcher death (the load-bearing AC1 guarantee).
    // We do NOT set STARTF_USESTDHANDLES, so the child still shares the
    // launcher's console -> stdout/stderr stream live (AC7) and Ctrl+C reaches
    // it as a normal CTRL_C_EVENT (no CREATE_NEW_PROCESS_GROUP).
    [StructLayout(LayoutKind.Sequential)]
    public struct STARTUPINFO {
        public Int32 cb; public string lpReserved; public string lpDesktop;
        public string lpTitle; public Int32 dwX; public Int32 dwY;
        public Int32 dwXSize; public Int32 dwYSize; public Int32 dwXCountChars;
        public Int32 dwYCountChars; public Int32 dwFillAttribute; public Int32 dwFlags;
        public Int16 wShowWindow; public Int16 cbReserved2; public IntPtr lpReserved2;
        public IntPtr hStdInput; public IntPtr hStdOutput; public IntPtr hStdError;
    }
    [StructLayout(LayoutKind.Sequential)]
    public struct PROCESS_INFORMATION {
        public IntPtr hProcess; public IntPtr hThread;
        public int dwProcessId; public int dwThreadId;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern bool CreateProcess(
        string lpApplicationName, string lpCommandLine,
        IntPtr lpProcessAttributes, IntPtr lpThreadAttributes,
        bool bInheritHandles, uint dwCreationFlags, IntPtr lpEnvironment,
        string lpCurrentDirectory, ref STARTUPINFO lpStartupInfo,
        out PROCESS_INFORMATION lpProcessInformation);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern uint WaitForSingleObject(IntPtr hHandle, uint dwMilliseconds);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool GetExitCodeProcess(IntPtr hProcess, out uint lpExitCode);
}

} // namespace EDAFK
'@
}

function New-KillOnCloseJob {
    <#
      Create a job object configured KILL_ON_JOB_CLOSE | BREAKAWAY_OK. Returns
      the job handle (IntPtr) or [IntPtr]::Zero on failure (caller falls back).
      BREAKAWAY_OK lets a child that is already in some OTHER job request
      CREATE_BREAKAWAY_FROM_JOB; on Win8+ nesting just works, so this is belt
      and suspenders for the pre-Win8 / restrictive-parent-job caveat.
    #>
    try {
        $h = [EDAFK.JobApi]::CreateJobObject([IntPtr]::Zero, $null)
        if ($h -eq [IntPtr]::Zero) { return [IntPtr]::Zero }

        $info = New-Object 'EDAFK.JOBOBJECT_EXTENDED_LIMIT_INFORMATION'
        $info.BasicLimitInformation.LimitFlags =
            [EDAFK.JobApi]::JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE -bor `
            [EDAFK.JobApi]::JOB_OBJECT_LIMIT_BREAKAWAY_OK

        $len = [System.Runtime.InteropServices.Marshal]::SizeOf($info)
        $ptr = [System.Runtime.InteropServices.Marshal]::AllocHGlobal($len)
        try {
            [System.Runtime.InteropServices.Marshal]::StructureToPtr($info, $ptr, $false)
            $ok = [EDAFK.JobApi]::SetInformationJobObject(
                $h, [EDAFK.JobApi]::JobObjectExtendedLimitInformation, $ptr, $len)
        } finally {
            [System.Runtime.InteropServices.Marshal]::FreeHGlobal($ptr)
        }
        if (-not $ok) { [void][EDAFK.JobApi]::CloseHandle($h); return [IntPtr]::Zero }
        # Belt: ensure the handle is never inherited by a child (a leaked copy
        # in the child would keep the job alive and defeat kill-on-close).
        [void][EDAFK.JobApi]::SetHandleInformation($h, [EDAFK.JobApi]::HANDLE_FLAG_INHERIT, 0)
        return $h
    } catch {
        return [IntPtr]::Zero
    }
}

function ConvertTo-Win32ArgString {
    <#
      Quote an argument array into a single Win32 command-line string per the
      CommandLineToArgvW rules: wrap an arg containing space/tab/quote in double
      quotes, double any backslashes that precede a quote, and escape embedded
      quotes. Plain args (no spaces/quotes) pass through unquoted.
    #>
    param([Parameter(Mandatory)][AllowEmptyCollection()][string[]]$ArgList)
    $parts = New-Object System.Collections.Generic.List[string]
    foreach ($a in $ArgList) {
        if ($a -ne '' -and $a -notmatch '[\s"]') {
            $parts.Add($a); continue
        }
        $sb = New-Object System.Text.StringBuilder
        [void]$sb.Append('"')
        $backslashes = 0
        foreach ($ch in $a.ToCharArray()) {
            if ($ch -eq '\') {
                $backslashes++
            } elseif ($ch -eq '"') {
                [void]$sb.Append('\' * ($backslashes * 2 + 1))
                [void]$sb.Append('"')
                $backslashes = 0
            } else {
                if ($backslashes -gt 0) { [void]$sb.Append('\' * $backslashes); $backslashes = 0 }
                [void]$sb.Append($ch)
            }
        }
        if ($backslashes -gt 0) { [void]$sb.Append('\' * ($backslashes * 2)) }
        [void]$sb.Append('"')
        $parts.Add($sb.ToString())
    }
    return ($parts -join ' ')
}

function Start-ChildInJob {
    <#
    .SYNOPSIS
      Spawn a child process under a kill-on-close job (Layer 1), falling back
      to a PID-targeted taskkill (Layer 2) when the job can't be used.

    .PARAMETER FilePath        the executable (the venv python.exe).
    .PARAMETER Arguments       string[] of args (passed individually, no shell).
    .PARAMETER WorkingDirectory  child CWD (= $ProjectRoot, preserves config.toml resolution).
    .PARAMETER ForceNoJob      test hook: skip job creation to exercise Layer 2.

    .OUTPUTS
      A PSCustomObject:
        .ProcHandle  [IntPtr] raw child process handle (Wait-ChildAndPropagate
                     waits on it then closes it)
        .ChildPid    [int] the child PID captured at spawn (load-bearing)
        .JobHandle   [IntPtr] held-open job handle (Zero if no job)
        .JobUsed     [bool] $true iff the child is assigned to a kill-on-close job
      The CALLER must keep this object (hence the JobHandle) alive for the
      lifetime of the child -- that open handle IS the kill-on-close guarantee.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][AllowEmptyCollection()][string[]]$Arguments,
        [Parameter(Mandatory)][string]$WorkingDirectory,
        [switch]$ForceNoJob
    )

    # Create the job FIRST so we can assign immediately after spawn (minimises
    # the documented assignment race: the child runs a few ms unassigned).
    $job = [IntPtr]::Zero
    if (-not $ForceNoJob) { $job = New-KillOnCloseJob }
    $jobUsed = $false

    # Spawn via raw CreateProcess with bInheritHandles=FALSE. This is the
    # load-bearing detail: .NET's Process.Start(UseShellExecute=false) sets
    # bInheritHandles=TRUE, which leaks a duplicate of our handles into the
    # child; that copy of the job handle kept the job ALIVE past the launcher's
    # death and silently defeated kill-on-close (AC1 failed exactly this way).
    # With FALSE the child can hold no copy, so the launcher's handle is the
    # last -> kill-on-close fires when the launcher dies by ANY means.
    # We do NOT set STARTF_USESTDHANDLES, so the child still attaches to our
    # console: stdout/stderr stream live (AC7), Ctrl+C arrives normally.
    $cmdLine = '"' + $FilePath + '" ' + (ConvertTo-Win32ArgString -ArgList $Arguments)
    $si = New-Object 'EDAFK.JobApi+STARTUPINFO'
    $si.cb = [System.Runtime.InteropServices.Marshal]::SizeOf([type]'EDAFK.JobApi+STARTUPINFO')
    $pi = New-Object 'EDAFK.JobApi+PROCESS_INFORMATION'
    # Pass lpApplicationName explicitly (avoids the search-path / quoting
    # ambiguity of a null app name); lpCommandLine still carries argv[0].
    $created = [EDAFK.JobApi]::CreateProcess(
        $FilePath, $cmdLine, [IntPtr]::Zero, [IntPtr]::Zero,
        $false, 0, [IntPtr]::Zero, $WorkingDirectory, [ref]$si, [ref]$pi)
    if (-not $created) {
        $e = [System.Runtime.InteropServices.Marshal]::GetLastWin32Error()
        if ($job -ne [IntPtr]::Zero) { [void][EDAFK.JobApi]::CloseHandle($job) }
        throw "CreateProcess failed (Win32 $e) for $FilePath"
    }
    # Don't need the thread handle.
    [void][EDAFK.JobApi]::CloseHandle($pi.hThread)
    $childPid = $pi.dwProcessId
    $procHandle = $pi.hProcess

    # Assign to the job immediately (race window = the few ms above).
    if ($job -ne [IntPtr]::Zero) {
        $assigned = [EDAFK.JobApi]::AssignProcessToJobObject($job, $procHandle)
        if ($assigned) {
            $jobUsed = $true
        } else {
            # ERROR_ACCESS_DENIED (5) => console already in a job that forbids
            # breakaway. Don't crash: close our job handle and let Layer 2
            # cover graceful exits.
            $errCode = [System.Runtime.InteropServices.Marshal]::GetLastWin32Error()
            Write-Host ("[launch] WARN: job assignment failed (Win32 $errCode) -- " +
                        "falling back to taskkill /T on exit (Layer 2). " +
                        "Hard-kill of THIS launcher will NOT be covered.")
            [void][EDAFK.JobApi]::CloseHandle($job)
            $job = [IntPtr]::Zero
        }
    } else {
        if (-not $ForceNoJob) {
            Write-Host ("[launch] WARN: job-object creation failed -- falling " +
                        "back to taskkill /T on exit (Layer 2). Hard-kill of " +
                        "THIS launcher will NOT be covered.")
        }
    }

    return [pscustomobject]@{
        ProcHandle = $procHandle
        ChildPid   = $childPid
        JobHandle  = $job
        JobUsed    = $jobUsed
    }
}

function Stop-ChildTree {
    <#
      Layer-2 fallback teardown by EXACT PID (never a command-line scan).
      Safe to call when the job already handled it (taskkill on a dead PID is a
      harmless no-op). /T takes descendants too.
    #>
    param([Parameter(Mandatory)][int]$ChildPid)
    if ($ChildPid -le 0) { return }
    try {
        & taskkill.exe /T /F /PID $ChildPid 2>$null | Out-Null
    } catch { }
}

function Wait-ChildAndPropagate {
    <#
      Block until the child exits, then re-propagate its EXACT exit code. The
      caller `exit`s with this so $LASTEXITCODE / launcher exit == child exit
      (0 clean, 130 Ctrl+C, 2 bootstrap error). Keeps $Spawn (and its
      JobHandle) referenced for the whole wait so the kill-on-close guarantee
      holds until the child is actually gone.

      We wait on the raw process handle. To keep Ctrl+C live (so the launcher's
      trap can forward it / the child sees CTRL_C_EVENT), we wait in short
      bounded slices rather than one INFINITE blocking call.
    #>
    param([Parameter(Mandatory)]$Spawn)
    $WAIT_TIMEOUT = 0x102
    while ([EDAFK.JobApi]::WaitForSingleObject($Spawn.ProcHandle, 250) -eq $WAIT_TIMEOUT) {
        # spin in 250ms slices; PowerShell delivers Ctrl+C between slices
    }
    $code = [uint32]0
    [void][EDAFK.JobApi]::GetExitCodeProcess($Spawn.ProcHandle, [ref]$code)
    [void][EDAFK.JobApi]::CloseHandle($Spawn.ProcHandle)
    return [int]$code
}

[CmdletBinding()]
param(
    [ValidateRange(1, 60)][int]$PollSeconds = 15,
    [ValidateRange(0, 86400)][int]$DurationSeconds = 0
)
$ErrorActionPreference = 'Stop'
$taskVBox = Join-Path $env:ProgramFiles 'Oracle\VirtualBox\VBoxManage.exe'
if (-not (Test-Path -LiteralPath $taskVBox)) { throw 'VirtualBox is not installed at its expected path.' }
$taskMutex = New-Object System.Threading.Mutex($false, 'Local\SmartLearningLabPowerGuard')
$taskAcquired = $false
$taskPowerSet = $false
try {
    try { $taskAcquired = $taskMutex.WaitOne(0) }
    catch [System.Threading.AbandonedMutexException] { $taskAcquired = $true }
    if (-not $taskAcquired) { Write-Output 'Lab power guard is already running.'; return }
    if (-not ('SmartLearningLab.PowerState' -as [type])) {
        Add-Type -TypeDefinition @'
using System.Runtime.InteropServices;
namespace SmartLearningLab {
    public static class PowerState {
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern uint SetThreadExecutionState(uint flags);
    }
}
'@
    }
    $taskElapsed = [System.Diagnostics.Stopwatch]::StartNew()
    while ($DurationSeconds -eq 0 -or $taskElapsed.Elapsed.TotalSeconds -lt $DurationSeconds) {
        $taskRunning = & $taskVBox list runningvms
        if ($LASTEXITCODE -ne 0) { throw 'Cannot read VM status; releasing the power request.' }
        if (-not ($taskRunning -match '^"(VM1|VM_2|VM3)"\s+\{')) { break }
        # ES_CONTINUOUS | ES_SYSTEM_REQUIRED. This is scoped to this process;
        # exiting releases it. It does not change the power plan or lid policy.
        if ([SmartLearningLab.PowerState]::SetThreadExecutionState([uint32]2147483649) -eq 0) {
            throw 'Windows rejected the lab power request.'
        }
        if (-not $taskPowerSet) {
            $taskPowerSet = $true
            Write-Output 'Lab power guard active. Keep the lid open; manual sleep still suspends the VMs.'
        }
        $taskWaitSeconds = $PollSeconds
        if ($DurationSeconds -gt 0) {
            $taskWaitSeconds = [Math]::Min($PollSeconds, [Math]::Max(1, [Math]::Ceiling($DurationSeconds - $taskElapsed.Elapsed.TotalSeconds)))
        }
        Start-Sleep -Seconds $taskWaitSeconds
    }
}
finally {
    if ($taskPowerSet) {
        [void][SmartLearningLab.PowerState]::SetThreadExecutionState([uint32]2147483648)
        Write-Output 'Lab power guard released.'
    }
    if ($taskAcquired) { $taskMutex.ReleaseMutex() }
    $taskMutex.Dispose()
}

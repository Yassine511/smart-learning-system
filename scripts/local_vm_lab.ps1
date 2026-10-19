[CmdletBinding()]
param(
    [ValidateSet('Status', 'Start', 'Stop', 'Restore')][string]$Action = 'Status'
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskStatePath = Join-Path $taskRoot 'backups\local-three-vm\state.json'
$taskVBox = Join-Path $env:ProgramFiles 'Oracle\VirtualBox\VBoxManage.exe'
if (-not (Test-Path -LiteralPath $taskVBox)) { throw 'VirtualBox is not installed at its expected path.' }

function Invoke-TaskVBox {
    param([string[]]$TaskArguments)
    $taskResult = & $taskVBox @TaskArguments
    if ($LASTEXITCODE -ne 0) { throw "VirtualBox failed: $($TaskArguments[0]) $($TaskArguments[1])" }
    return $taskResult
}
function Get-TaskVM {
    param([string]$TaskName)
    $taskInfo = Invoke-TaskVBox @('showvminfo', $TaskName, '--machinereadable')
    $taskValues = @{}
    foreach ($taskLine in $taskInfo) {
        if ($taskLine -match '^([^=]+)=(.*)$') { $taskValues[$Matches[1]] = $Matches[2].Trim('"') }
    }
    return [pscustomobject]@{Name=$TaskName; State=$taskValues.VMState; MemoryMB=[int]$taskValues.memory; CPUs=[int]$taskValues.cpus}
}
$taskVMs = @('VM1', 'VM_2', 'VM3') | ForEach-Object { Get-TaskVM $_ }
if ($Action -eq 'Status') {
    $taskVMs | Format-Table -AutoSize
    $taskAvailableMB = [math]::Floor((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1024)
    Write-Output "Windows free memory: $taskAvailableMB MB"
    exit
}
if ($Action -eq 'Start') {
    $taskStopped = @($taskVMs | Where-Object State -eq 'poweroff')
    $taskRequiredMB = ($taskStopped | Measure-Object -Property MemoryMB -Sum).Sum + 1024
    $taskAvailableMB = (Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1024
    if ($taskAvailableMB -lt $taskRequiredMB) { throw "Close unused host applications first: need $taskRequiredMB MB free, have $([int]$taskAvailableMB) MB." }
    foreach ($taskVM in $taskStopped) { Invoke-TaskVBox @('startvm', $taskVM.Name, '--type', 'gui') }
    exit
}
if ($Action -eq 'Stop') {
    foreach ($taskVM in $taskVMs | Where-Object State -eq 'running') {
        Invoke-TaskVBox @('controlvm', $taskVM.Name, 'acpipowerbutton')
    }
    Write-Output 'Graceful shutdown requested. Run Status before closing VirtualBox; no volumes or snapshots were removed.'
    exit
}
if ($taskVMs | Where-Object State -ne 'poweroff') { throw 'Shut down all three VMs before restoring snapshots.' }
if (-not (Test-Path -LiteralPath $taskStatePath)) { throw 'The baseline snapshot manifest is missing.' }
$taskState = Get-Content -LiteralPath $taskStatePath -Raw | ConvertFrom-Json
$taskRecoveryName = 'prototype-before-restore-' + (Get-Date -Format 'yyyyMMddTHHmmss')
foreach ($taskVM in $taskState.vms) {
    Invoke-TaskVBox @('snapshot', $taskVM.name, 'take', $taskRecoveryName)
}
foreach ($taskVM in $taskState.vms) {
    Invoke-TaskVBox @('snapshot', $taskVM.name, 'restore', $taskVM.snapshot_id)
}
Write-Output 'Original guest disks and VM settings restored. New prototype data remains recoverable in the retained snapshot branches.'

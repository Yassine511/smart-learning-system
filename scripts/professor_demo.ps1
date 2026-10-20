[CmdletBinding()]
param([ValidateSet('Start','Status','Stop')][string]$Action = 'Status')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskStateDir = Join-Path $taskRoot 'backups/professor-demo'
$taskStateFile = Join-Path $taskStateDir 'tunnel.json'
$taskTailscale = Join-Path $env:ProgramFiles 'Tailscale/tailscale.exe'
$taskSSH = Join-Path $env:WINDIR 'System32/OpenSSH/ssh.exe'
$taskSSHConfig = Join-Path $taskRoot 'backups/local-three-vm/media/lab-ssh.conf'
$taskTarget = 'http://127.0.0.1:18080'
$taskForward = '127.0.0.1:18080:127.0.0.1:8080'
if (-not (Test-Path -LiteralPath $taskStateFile)) { throw 'The initial professor-demo setup has not been completed.' }
$taskState = Get-Content -LiteralPath $taskStateFile -Raw | ConvertFrom-Json

function Get-DemoTunnel {
    $taskProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($taskState.pid)" -ErrorAction SilentlyContinue
    if ($taskProcess -and $taskProcess.Name -eq 'ssh.exe' -and $taskProcess.CommandLine.Contains($taskForward)) {
        return $taskProcess
    }
    return $null
}
function Assert-DemoServeConfig {
    $taskExisting = & $taskTailscale funnel status --json
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect the Tailscale configuration.' }
    $taskText = $taskExisting -join "`n"
    if ($taskText.Trim() -notin @('{}','null','') -and -not $taskText.Contains($taskTarget)) {
        throw 'A different Tailscale web configuration exists; refusing to replace it.'
    }
}
if ($Action -eq 'Stop') {
    Assert-DemoServeConfig
    & $taskTailscale funnel --bg --https=443 $taskTarget off
    if ($LASTEXITCODE -ne 0) { throw 'Tailscale did not confirm public access was stopped.' }
    $taskTunnel = Get-DemoTunnel
    if ($taskTunnel) { Stop-Process -Id $taskTunnel.ProcessId }
    Write-Output 'Professor demo stopped. VM1, its local HTTPS site, and saved data remain available.'
    exit
}
if ($Action -eq 'Start') {
    # Windows otherwise stops a user-owned connection when its last client exits.
    & $taskTailscale up --unattended=true --timeout=20s
    if ($LASTEXITCODE -ne 0) { throw 'Tailscale could not connect in unattended mode.' }
    $taskTailnet = (& $taskTailscale status --json | ConvertFrom-Json)
    if ($taskTailnet.BackendState -ne 'Running') { throw 'Connect this PC to its personal Tailscale network first.' }
    $taskOrigin = 'https://' + $taskTailnet.Self.DNSName.TrimEnd('.')
    if ($taskOrigin -ne $taskState.origin) { throw 'The current Tailscale hostname differs from the prepared VM1 origin.' }
    Assert-DemoServeConfig
    if (-not (Get-DemoTunnel)) {
        $taskArguments = @('-N','-F',('"'+$taskSSHConfig+'"'),'-o','HostName=127.0.0.1',
            '-o','Port=2221','-o','ProxyJump=none','-o','ExitOnForwardFailure=yes',
            '-L',$taskForward,'sl-server')
        $taskProcess = Start-Process -FilePath $taskSSH -ArgumentList $taskArguments -WindowStyle Hidden `
            -RedirectStandardError (Join-Path $taskStateDir 'tunnel-error.log') -PassThru
        $taskState.pid = $taskProcess.Id
        $taskState | ConvertTo-Json | Set-Content -LiteralPath $taskStateFile -Encoding utf8
        Start-Sleep -Seconds 2
        if ($taskProcess.HasExited) { throw 'The SSH tunnel failed; inspect backups/professor-demo/tunnel-error.log.' }
    }
    $taskReady = Invoke-RestMethod -Uri "$taskTarget/api/ready" -TimeoutSec 15
    if ($taskReady.status -ne 'ready') { throw 'VM1 is not ready; public access was not enabled.' }
    & $taskTailscale funnel --bg --yes --https=443 $taskTarget
    if ($LASTEXITCODE -ne 0) { throw 'Tailscale Funnel was not enabled.' }
    $taskPython = Join-Path $taskRoot 'backend/.venv/Scripts/python.exe'
    & $taskPython (Join-Path $PSScriptRoot 'check_professor_public.py') --origin $taskState.origin `
        --output (Join-Path $taskStateDir 'public-readiness.json') --wait-seconds 60
    if ($LASTEXITCODE -ne 0) { throw 'Public Funnel relays are not all ready; inspect backups/professor-demo/public-readiness.json.' }
}
& $taskTailscale funnel status
$taskReady = Invoke-RestMethod -Uri "$taskTarget/api/ready" -TimeoutSec 15
Write-Output "VM1 readiness: $($taskReady.status)"
Write-Output "Demo URL: $($taskState.origin)"

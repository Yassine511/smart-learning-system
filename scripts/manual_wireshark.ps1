param(
    [Parameter(Mandatory)][string]$OutputDirectory,
    [ValidateRange(1,60)][int]$Minutes = 20
)
# Run with pwsh. Binary pcap bytes move directly between native streams.
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$taskOutput = [IO.Path]::GetFullPath($OutputDirectory)
$taskAllowed = [IO.Path]::GetFullPath((Join-Path $taskRoot 'backups/local-three-vm')) + [IO.Path]::DirectorySeparatorChar
if (-not $taskOutput.StartsWith($taskAllowed, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Manual captures must stay inside backups/local-three-vm.'
}
New-Item -ItemType Directory -Force -Path $taskOutput | Out-Null
$taskConfig = Join-Path $taskRoot 'backups/local-three-vm/media/lab-ssh.conf'
$taskSSH = 'C:\Windows\System32\OpenSSH\ssh.exe'
$taskSCP = 'C:\Windows\System32\OpenSSH\scp.exe'
$taskWireshark = 'C:\Program Files\Wireshark\Wireshark.exe'
$taskBase = @('-F', $taskConfig, '-o', 'ProxyJump=none', '-o', 'HostName=127.0.0.1')
$taskStreams = @()
$taskCaptureStarted = $false
$taskState = @{status='starting'; started_at=[DateTimeOffset]::UtcNow.ToString('o'); directory=$taskOutput; streams=@()}
function Save-State { $taskState | ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 (Join-Path $taskOutput 'state.json') }
function Capture-Control([string]$Command) {
    & $taskSSH @taskBase -p 2221 -l root sl-server $Command
    if ($LASTEXITCODE -ne 0) { throw "Restricted capture command failed: $Command" }
}
try {
    Save-State
    $taskPrevious = Join-Path $taskOutput 'previous'
    New-Item -ItemType Directory -Force -Path $taskPrevious | Out-Null
    foreach ($taskKind in @('http','https')) {
        & $taskSCP @taskBase -P 2221 "sl-server:/var/lib/smart-learning-local-evidence/$taskKind.pcap" $taskPrevious
        if ($LASTEXITCODE -ne 0) { throw 'Preserving the previous capture failed; no new capture started.' }
    }
    Capture-Control 'capture-start'
    $taskCaptureStarted = $true
    foreach ($taskKind in @('http','https')) {
        $taskRemoteInfo = [Diagnostics.ProcessStartInfo]::new($taskSSH)
        $taskRemoteInfo.UseShellExecute = $false
        $taskRemoteInfo.CreateNoWindow = $true
        $taskRemoteInfo.RedirectStandardOutput = $true
        $taskRemoteInfo.RedirectStandardError = $true
        foreach ($taskArg in ($taskBase + @('-p','2221','sl-server',"tail -c +1 -f /var/lib/smart-learning-local-evidence/$taskKind.pcap"))) { $taskRemoteInfo.ArgumentList.Add($taskArg) }
        $taskRemote = [Diagnostics.Process]::Start($taskRemoteInfo)
        $taskRemoteErrors = $taskRemote.StandardError.ReadToEndAsync()
        $taskEntry = @{kind=$taskKind; remote=$taskRemote; errors=$taskRemoteErrors; gui=$null; pump=$null}
        $taskStreams += $taskEntry
        $taskHeader = [byte[]]::new(24)
        $taskHeaderRead = $taskRemote.StandardOutput.BaseStream.ReadAsync($taskHeader,0,24)
        if (-not $taskHeaderRead.Wait(25000)) { throw "Timed out reading $taskKind capture header." }
        $taskHeaderBytes = $taskHeaderRead.GetAwaiter().GetResult()
        if ($taskHeaderBytes -lt 4 -or [BitConverter]::ToString($taskHeader,0,4) -notin @('D4-C3-B2-A1','A1-B2-C3-D4','4D-3C-B2-A1','A1-B2-3C-4D','0A-0D-0D-0A')) {
            throw "The $taskKind stream did not provide a pcap header."
        }
        $taskGuiInfo = [Diagnostics.ProcessStartInfo]::new($taskWireshark)
        $taskGuiInfo.UseShellExecute = $false
        $taskGuiInfo.RedirectStandardInput = $true
        $taskTitle = if ($taskKind -eq 'http') {'Smart Learning - readable HTTP on VM1'} else {'Smart Learning - encrypted traffic from both clients'}
        $taskFilter = if ($taskKind -eq 'http') {'http.request || http.response'} else {'tcp.port == 443'}
        $taskGuiArgs = @('-k','-i','-','-n','-Y',$taskFilter,'-o',"gui.window_title:$taskTitle",'-w',(Join-Path $taskOutput "$taskKind-live.pcapng"))
        if ($taskKind -eq 'http') { $taskGuiArgs += @('-d','tcp.port==8080,http') }
        foreach ($taskArg in $taskGuiArgs) { $taskGuiInfo.ArgumentList.Add($taskArg) }
        $taskGui = [Diagnostics.Process]::Start($taskGuiInfo)
        $taskEntry.gui = $taskGui
        $taskGui.StandardInput.BaseStream.Write($taskHeader,0,$taskHeaderBytes)
        $taskGui.StandardInput.BaseStream.Flush()
        $taskEntry.pump = $taskRemote.StandardOutput.BaseStream.CopyToAsync($taskGui.StandardInput.BaseStream)
        $taskState.streams += @{kind=$taskKind; wireshark_pid=$taskGui.Id; ssh_pid=$taskRemote.Id; file="$taskKind-live.pcapng"}
    }
    $taskState.status = 'running'
    $taskState.ends_by = [DateTimeOffset]::UtcNow.AddMinutes($Minutes).ToString('o')
    Save-State
    $taskDeadline = [DateTimeOffset]::UtcNow.AddMinutes($Minutes)
    while ([DateTimeOffset]::UtcNow -lt $taskDeadline -and -not (Test-Path (Join-Path $taskOutput 'stop.flag'))) {
        $taskLive = @($taskStreams | Where-Object { -not $_.gui.HasExited -and -not $_.pump.IsCompleted })
        if ($taskLive.Count -eq 0) { break }
        Start-Sleep -Milliseconds 500
    }
} catch {
    $taskState.error = $_.Exception.Message
} finally {
    foreach ($taskEntry in $taskStreams) {
        if ($taskEntry.gui -and -not $taskEntry.gui.HasExited) { try { $taskEntry.gui.StandardInput.Close() } catch {} }
        if (-not $taskEntry.remote.HasExited) { $taskEntry.remote.Kill(); $taskEntry.remote.WaitForExit() }
        if ($taskEntry.errors.IsCompleted) { $taskEntry.errors.GetAwaiter().GetResult() | Set-Content -Encoding utf8 (Join-Path $taskOutput ($taskEntry.kind+'-ssh.log')) }
    }
    if ($taskCaptureStarted) {
        try { Capture-Control 'capture-stop' } catch { $taskState.stop_error = $_.Exception.Message }
        foreach ($taskKind in @('http','https')) {
            & $taskSCP @taskBase -P 2221 "sl-server:/var/lib/smart-learning-local-evidence/$taskKind.pcap" (Join-Path $taskOutput "$taskKind-vm.pcap")
        }
    }
    $taskState.status = if ($taskState.error -or $taskState.stop_error) {'needs_attention'} else {'stopped'}
    $taskState.finished_at = [DateTimeOffset]::UtcNow.ToString('o')
    Save-State
}

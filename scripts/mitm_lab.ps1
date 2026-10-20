[CmdletBinding()]
param(
    [ValidateSet('Status','Prepare','Start','Run','Stop','Collect')][string]$Action = 'Status',
    [ValidateSet('all','baseline','observe','modify','inject','drop','recovery','comparison')][string]$Mode = 'all',
    [string]$Session = ''
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskPython = (Get-Command python -ErrorAction Stop).Source
$taskArguments = @((Join-Path $taskRoot 'deploy/mitm-lab/host.py'), $Action.ToLowerInvariant(), '--mode', $Mode)
if ($Session) { $taskArguments += @('--session', $Session) }
& $taskPython @taskArguments
if ($LASTEXITCODE -ne 0) { throw "MITM lab $Action did not complete. Review the specific error above." }

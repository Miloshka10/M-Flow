param([Parameter(Mandatory=$true)][string]$Archive)
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$pythonExecutable = Join-Path $projectDirectory '.venv\Scripts\python.exe'
$checkScript = Join-Path $projectDirectory 'postgres_app_check.py'
$previousUtf8 = $env:PYTHONUTF8
try {
    $env:PYTHONUTF8 = '1'
    [Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
    & $pythonExecutable $checkScript $Archive
} finally {
    $env:PYTHONUTF8 = $previousUtf8
    Read-Host 'Press Enter only AFTER reading the result to close this window' | Out-Null
}

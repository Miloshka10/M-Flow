$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$pythonExecutable = Join-Path $projectDirectory '.venv\Scripts\python.exe'
$backupScript = Join-Path $projectDirectory 'postgres_backup.py'
if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    throw 'Project Python virtual environment not found.'
}
$previousUtf8 = $env:PYTHONUTF8
try {
    $env:PYTHONUTF8 = '1'
    [Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
    & $pythonExecutable $backupScript
} finally {
    $env:PYTHONUTF8 = $previousUtf8
    Read-Host 'Press Enter to close this window' | Out-Null
}

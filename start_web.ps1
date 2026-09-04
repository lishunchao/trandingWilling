param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw 'Local environment is missing. Run .\setup.ps1 first.' }
Push-Location $ProjectRoot
try { & $Python -m web.server --port $Port } finally { Pop-Location }

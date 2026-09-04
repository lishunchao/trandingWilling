param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw '尚未安装本地环境，请先运行 .\setup.ps1' }
Push-Location $ProjectRoot
try { & $Python -m web.server --port $Port } finally { Pop-Location }

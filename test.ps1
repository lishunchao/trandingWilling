$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw '尚未安装本地环境，请先运行 .\setup.ps1' }
& $Python (Join-Path $ProjectRoot 'work\test_paper_tracker_costs.py')
Push-Location $ProjectRoot
try { & $Python -m unittest work.test_web_console } finally { Pop-Location }
& $Python -m compileall -q (Join-Path $ProjectRoot 'work')
& $Python -m compileall -q (Join-Path $ProjectRoot 'web')
Write-Host '本地测试通过。'

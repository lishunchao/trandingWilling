param([switch]$Once)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw '尚未安装本地环境，请先运行 .\setup.ps1' }
$Arguments = @((Join-Path $ProjectRoot 'work\qingyun_paper_tracker.py'))
if ($Once) { $Arguments += '--once' }
& $Python @Arguments

param([switch]$Once)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw 'Local environment is missing. Run .\setup.ps1 first.' }
$Arguments = @((Join-Path $ProjectRoot 'work\qingyun_paper_tracker.py'))
if ($Once) { $Arguments += '--once' }
& $Python @Arguments

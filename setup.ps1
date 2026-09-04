$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$Python = if (Test-Path -LiteralPath $BundledPython) { $BundledPython } else { 'python' }

& $Python -m venv (Join-Path $ProjectRoot '.venv')
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
& $VenvPython -m pip install --disable-pip-version-check -r (Join-Path $ProjectRoot 'requirements.txt')
& $VenvPython -m compileall -q (Join-Path $ProjectRoot 'work')
Write-Host '环境已就绪。运行 .\test.ps1 验证，运行 .\start.ps1 -Once 执行一次公开行情扫描。'

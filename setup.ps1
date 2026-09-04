$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$Python = if (Test-Path -LiteralPath $BundledPython) { $BundledPython } else { 'python' }

& $Python -m venv (Join-Path $ProjectRoot '.venv')
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
& $VenvPython -m pip install --disable-pip-version-check -r (Join-Path $ProjectRoot 'requirements.txt')
& $VenvPython -m compileall -q (Join-Path $ProjectRoot 'work')
Write-Host 'Setup complete. Run .\test.ps1 to verify, .\start.ps1 -Once to scan, or .\start_web.ps1 for Web V1.'

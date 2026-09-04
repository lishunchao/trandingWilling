param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw 'Local environment is missing. Run .\setup.ps1 first.' }

# Replace an older instance of this console so code updates are visible immediately.
$Listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($Listener) {
    $IsTradingConsole = $false
    try {
        $Health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/v1/health" -TimeoutSec 3
        $IsTradingConsole = $Health.service -eq 'qingyun-web-v1'
    } catch {}
    if (-not $IsTradingConsole) {
        throw "Port $Port is already used by another application."
    }
    Stop-Process -Id $Listener.OwningProcess -Force
    Start-Sleep -Milliseconds 800
}

Push-Location $ProjectRoot
try { & $Python -m web.server --port $Port } finally { Pop-Location }

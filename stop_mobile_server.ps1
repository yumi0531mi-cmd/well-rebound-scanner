$ErrorActionPreference = "Stop"

$scannerRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidPath = Join-Path $scannerRoot ".scanner_data\runtime\streamlit.pid"

if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Host "기록된 로컬 스캐너 프로세스가 없습니다."
    exit 0
}

$scannerPid = Get-Content -LiteralPath $pidPath -ErrorAction Stop
$scannerProcess = Get-Process -Id $scannerPid -ErrorAction SilentlyContinue
if ($scannerProcess) {
    Stop-Process -Id $scannerPid
    Write-Host "로컬 스캐너를 종료했습니다. PID=$scannerPid"
}
Remove-Item -LiteralPath $pidPath -Force

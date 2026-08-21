$ErrorActionPreference = "Stop"

$scannerRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtimePython = "C:\Users\cj123\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$toolsRoot = Join-Path $scannerRoot ".local-tools"
$cloudflaredPath = Join-Path $toolsRoot "cloudflared.exe"
$runtimeRoot = Join-Path $scannerRoot ".scanner_data\runtime"
$pidPath = Join-Path $runtimeRoot "streamlit.pid"

New-Item -ItemType Directory -Force -Path $toolsRoot, $runtimeRoot | Out-Null

if (-not (Test-Path -LiteralPath $runtimePython)) {
    throw "Python 실행 파일을 찾을 수 없습니다: $runtimePython"
}

if (-not $env:KIS_APP_KEY) {
    $env:KIS_APP_KEY = Read-Host "KIS_APP_KEY 입력"
}
if (-not $env:KIS_APP_SECRET) {
    $secretValue = Read-Host "KIS_APP_SECRET 입력(화면에 표시되지 않음)" -AsSecureString
    $secretPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secretValue)
    try {
        $env:KIS_APP_SECRET = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPointer)
    }
}

if (-not (Test-Path -LiteralPath $cloudflaredPath)) {
    Write-Host "Cloudflare Tunnel을 처음 한 번 내려받습니다."
    Invoke-WebRequest -UseBasicParsing `
        -Uri "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" `
        -OutFile $cloudflaredPath
}

if (Test-Path -LiteralPath $pidPath) {
    $oldPid = Get-Content -LiteralPath $pidPath -ErrorAction SilentlyContinue
    if ($oldPid -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
        Write-Host "기존 로컬 스캐너가 실행 중입니다. PID=$oldPid"
    }
    else {
        Remove-Item -LiteralPath $pidPath -Force
    }
}

if (-not (Test-Path -LiteralPath $pidPath)) {
    $streamlitProcess = Start-Process -FilePath $runtimePython -WorkingDirectory $scannerRoot -WindowStyle Hidden -PassThru `
        -ArgumentList @("-m", "streamlit", "run", "app.py", "--server.address", "127.0.0.1", "--server.port", "8501", "--server.headless", "true")
    Set-Content -LiteralPath $pidPath -Value $streamlitProcess.Id -Encoding ascii
}

$healthy = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        $health = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8501/_stcore/health" -TimeoutSec 2
        if ($health.StatusCode -eq 200) {
            $healthy = $true
            break
        }
    }
    catch {
        Start-Sleep -Seconds 1
    }
}
if (-not $healthy) {
    throw "로컬 Streamlit 서버가 30초 안에 시작되지 않았습니다."
}

Write-Host ""
Write-Host "로컬 스캐너 정상 실행: http://127.0.0.1:8501"
Write-Host "아래에 표시되는 https://*.trycloudflare.com 주소를 휴대전화에서 여세요."
Write-Host "이 창을 닫으면 외부 접속 터널이 종료됩니다."
Write-Host ""
& $cloudflaredPath tunnel --no-autoupdate --url http://127.0.0.1:8501

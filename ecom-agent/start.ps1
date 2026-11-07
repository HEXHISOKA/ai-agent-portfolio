# ecom-analysis-agent 一键启动：后端 8000 + 前端 5173，就绪后自动打开浏览器
# 用法：双击 start.bat，或在 PowerShell 里执行  .\start.ps1
#      加 -NoBrowser 可以只启动服务、不打开浏览器
param([switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # 关掉 Invoke-WebRequest 的进度条噪声

$Root     = Split-Path -Parent $MyInvocation.MyCommand.Path
$Backend  = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'
$LogDir   = Join-Path $Root 'logs'
$Python   = Join-Path $Backend '.venv\Scripts\python.exe'
$BackendUrl  = 'http://127.0.0.1:8000/'
$FrontendUrl = 'http://localhost:5173/'

function Test-Port([int]$port) {
    return [bool](Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
}

function Wait-Url([string]$url, [int]$seconds) {
    for ($i = 0; $i -lt $seconds; $i++) {
        try {
            $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
            if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500) { return $true }
        } catch { }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Show-Tail([string]$path, [int]$lines = 12) {
    if (Test-Path $path) {
        Write-Host "--- $path 末尾 ---" -ForegroundColor DarkGray
        Get-Content -LiteralPath $path -Tail $lines -Encoding UTF8
    }
}

Write-Host ''
Write-Host '=== ecom-analysis-agent 启动 ===' -ForegroundColor Cyan
Write-Host "项目目录: $Root"

# 前置检查：缺依赖就明确报错，不带着半套环境启动
if (-not (Test-Path $Python)) {
    Write-Host "[错误] 找不到后端虚拟环境：$Python" -ForegroundColor Red
    Write-Host '       请先在 backend 目录执行：python -m venv .venv 然后 pip install -r requirements.txt'
    exit 1
}
if (-not (Test-Path (Join-Path $Frontend 'node_modules'))) {
    Write-Host "[错误] 找不到前端依赖：$Frontend\node_modules" -ForegroundColor Red
    Write-Host '       请先在 frontend 目录执行：npm install'
    exit 1
}
if (-not (Test-Path (Join-Path $Backend '.env'))) {
    Write-Host '[警告] backend\.env 不存在，对话与语义检索会报配置缺失' -ForegroundColor Yellow
}
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# 代理变量去重：环境里同时存在 HTTPS_PROXY 和 https_proxy 时，
# Start-Process 构造环境变量字典会因大小写不敏感而报"已添加相同关键字"直接失败。
foreach ($name in 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY') {
    $lower = $name.ToLower()
    $upperValue = [System.Environment]::GetEnvironmentVariable($name)
    $lowerValue = [System.Environment]::GetEnvironmentVariable($lower)
    if ($lowerValue -and -not $upperValue) {
        [System.Environment]::SetEnvironmentVariable($name, $lowerValue)
        [System.Environment]::SetEnvironmentVariable($lower, $null)
    } elseif ($lowerValue) {
        [System.Environment]::SetEnvironmentVariable($lower, $null)
    }
}

# 后端：已经占用端口就不重复启动，避免出现两个进程抢 8000
if (Test-Port 8000) {
    Write-Host '[跳过] 8000 端口已有服务在监听' -ForegroundColor Yellow
} else {
    Write-Host '[1/2] 启动后端 uvicorn ...'
    Start-Process -FilePath $Python `
        -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000' `
        -WorkingDirectory $Backend -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir 'backend.log') `
        -RedirectStandardError  (Join-Path $LogDir 'backend.err.log') | Out-Null
}

# 前端：cmd /c 包一层，npm 是 .cmd 脚本，不能直接 Start-Process
if (Test-Port 5173) {
    Write-Host '[跳过] 5173 端口已有服务在监听' -ForegroundColor Yellow
} else {
    Write-Host '[2/2] 启动前端 vite ...'
    Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', 'npm run dev' `
        -WorkingDirectory $Frontend -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir 'frontend.log') `
        -RedirectStandardError  (Join-Path $LogDir 'frontend.err.log') | Out-Null
}

# 等就绪：后端首次导入 langgraph/chromadb 要几秒，前端 vite 也要编译
Write-Host '等待服务就绪 ...'
$backendOk  = Wait-Url $BackendUrl 90
$frontendOk = Wait-Url $FrontendUrl 90

Write-Host ''
if ($backendOk) {
    Write-Host "  后端 OK   $BackendUrl" -ForegroundColor Green
    Write-Host "  接口文档  http://127.0.0.1:8000/docs"
} else {
    Write-Host '  后端未就绪' -ForegroundColor Red
    Show-Tail (Join-Path $LogDir 'backend.err.log')
    Show-Tail (Join-Path $LogDir 'backend.log')
}
if ($frontendOk) {
    Write-Host "  前端 OK   $FrontendUrl" -ForegroundColor Green
} else {
    Write-Host '  前端未就绪' -ForegroundColor Red
    Show-Tail (Join-Path $LogDir 'frontend.err.log')
    Show-Tail (Join-Path $LogDir 'frontend.log')
}

if ($frontendOk -or $backendOk) {
    Write-Host ''
    Write-Host '日志：logs\backend.log / logs\frontend.log' -ForegroundColor DarkGray
    Write-Host '停止：双击 stop.bat（或执行 .\stop.ps1）' -ForegroundColor DarkGray
    if (-not $NoBrowser) { Start-Process $FrontendUrl }
}

if (-not ($backendOk -and $frontendOk)) { exit 1 }
Write-Host ''
Write-Host '两个服务都已就绪，浏览器已打开。' -ForegroundColor Cyan

# ecom-analysis-agent 一键停止：按端口找宿主进程，再按路径清残留
# 用法：双击 stop.bat，或在 PowerShell 里执行  .\stop.ps1
$ErrorActionPreference = 'SilentlyContinue'

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ''
Write-Host '=== ecom-analysis-agent 停止 ===' -ForegroundColor Cyan

$killed = 0
foreach ($port in 8000, 5173, 5174, 5175) {
    @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object {
            $proc = Get-Process -Id $_ -ErrorAction SilentlyContinue
            if ($proc) {
                Write-Host "  停止 $($proc.ProcessName) (PID $($proc.Id))，端口 $port"
                Stop-Process -Id $proc.Id -Force
                $killed++
            }
        }
}

# 兜底：端到端开发时 npm 会留下父 cmd 或孤立 node，按项目路径再清一遍
Get-CimInstance Win32_Process |
    Where-Object {
        $_.CommandLine -and $_.CommandLine -like "*$Root*" -and
        $_.Name -in @('node.exe', 'python.exe')
    } |
    ForEach-Object {
        Write-Host "  清理残留 $($_.Name) (PID $($_.ProcessId))"
        Stop-Process -Id $_.ProcessId -Force
        $killed++
    }

Start-Sleep -Milliseconds 800
$left = @()
foreach ($port in 8000, 5173) {
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) { $left += $port }
}

Write-Host ''
if ($left.Count -eq 0) {
    Write-Host "已全部停止（共处理 $killed 个进程）。" -ForegroundColor Green
} else {
    Write-Host "仍有端口被占用：$($left -join ', ')" -ForegroundColor Yellow
    Write-Host '若一直释放不掉，可执行：netstat -ano | findstr :8000  找到 PID 后 taskkill /F /PID <pid>'
}

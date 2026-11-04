# 查看守护化日志：默认输出最新一天日志的末尾 80 行；-Follow 持续跟踪。
# 用法：powershell -ExecutionPolicy Bypass -File backend\.local\daemon-logs.ps1 [-Lines 80] [-Follow]
# 编码：必须保存为 UTF-8 with BOM。

param([int]$Lines = 80, [switch]$Follow)

$ErrorActionPreference = 'Stop'
$logDir = Join-Path $PSScriptRoot 'logs'

$log = Get-ChildItem $logDir -Filter 'backend-*.log' -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($null -eq $log) {
    Write-Host "暂无日志：$logDir 下没有 backend-*.log（服务可能从未以守护方式启动）。" -ForegroundColor Yellow
    exit 1
}

Write-Host ("日志文件：{0}（{1:yyyy-MM-dd HH:mm:ss}，{2} 字节）" -f $log.FullName, $log.LastWriteTime, $log.Length) -ForegroundColor Cyan
if ($Follow) {
    Get-Content $log.FullName -Tail $Lines -Wait -Encoding UTF8
} else {
    Get-Content $log.FullName -Tail $Lines -Encoding UTF8
}

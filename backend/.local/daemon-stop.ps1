# 守护化停止：先停止计划任务（Task Scheduler 结束进程树），等待端口释放；
# 若仍有本项目残留（CommandLine 含 pathai_backend）则按 PID 强制终止。
# 非本项目进程占用端口时只报警、不误杀。
# 用法：powershell -ExecutionPolicy Bypass -File backend\.local\daemon-stop.ps1 [-TimeoutSec 20]
# 编码：必须保存为 UTF-8 with BOM。

param([int]$TimeoutSec = 20)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$taskName = 'PathAI-Backend'
$pidFile = Join-Path $here 'run\backend.pid.json'

function Get-TcpOwner([int]$port) {
    # 兼容 State 字段为空的环境：先 Listen 过滤，再退化为按端口号匹配。
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $c) {
        $c = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1
    }
    if ($null -eq $c) { return $null }
    return [int]$c.OwningProcess
}
function Get-UdpOwner([int]$port) {
    $c = Get-NetUDPEndpoint -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $c) { return $null }
    return [int]$c.OwningProcess
}
function Get-ProcInfo([int]$procId) {
    return Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
}
function Test-OurBackend([int]$procId) {
    $p = Get-ProcInfo $procId
    if ($null -eq $p) { return $false }
    return ($p.CommandLine -like '*pathai_backend*')
}

# ---------- 1. 停止计划任务 ----------
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -ne $task) {
    if ($task.State -eq 'Running') {
        try {
            Stop-ScheduledTask -TaskName $taskName
            Write-Host "[信息] 已停止计划任务 $taskName。" -ForegroundColor Green
        } catch {
            Write-Host "[警告] 停止计划任务失败：$($_.Exception.Message)" -ForegroundColor Yellow
        }
    } else {
        Write-Host "[信息] 计划任务 $taskName 当前状态：$($task.State)（无需停止）。"
    }
} else {
    Write-Host "[提示] 未发现计划任务 $taskName（可能为会话内启动的实例）。"
}

# ---------- 2. 等待端口释放 ----------
$deadline = (Get-Date).AddSeconds($TimeoutSec)
$tcpOwner = Get-TcpOwner 8000
$udpOwner = Get-UdpOwner 39001
while ((Get-Date) -lt $deadline -and ($null -ne $tcpOwner -or $null -ne $udpOwner)) {
    Start-Sleep -Seconds 1
    $tcpOwner = Get-TcpOwner 8000
    $udpOwner = Get-UdpOwner 39001
}

# ---------- 3. 残留处理（逐端口取最新 owner，避免用过期快照误判） ----------
foreach ($port in @(8000, 39001)) {
    $ownerNow = if ($port -eq 8000) { Get-TcpOwner 8000 } else { Get-UdpOwner 39001 }
    if ($null -eq $ownerNow) { continue }
    $p = Get-ProcInfo $ownerNow
    if ($null -eq $p) {
        # 进程刚退出，端口正在释放：交给最终校验判断
        Write-Host "[提示] 端口 $port 的 PID $ownerNow 已退出，等待释放。" -ForegroundColor DarkGray
        continue
    }
    if (Test-OurBackend $ownerNow) {
        Write-Host "[信息] 端口 $port 仍被本项目残留进程占用（PID $ownerNow），强制终止。" -ForegroundColor Yellow
        try {
            Stop-Process -Id $ownerNow -Force -ErrorAction Stop
        } catch {
            Write-Host "[错误] 终止 PID $ownerNow 失败：$($_.Exception.Message)" -ForegroundColor Red
        }
    } else {
        $name = $p.Name
        $cmd = if ($null -ne $p.CommandLine) { $p.CommandLine } else { '<不可读>' }
        Write-Host "[错误] 端口 $port 被非本项目进程占用（PID $ownerNow 名称=$name），未终止。" -ForegroundColor Red
        Write-Host "       命令行：$cmd" -ForegroundColor DarkGray
    }
}

# 残留 runner（计划任务外的会话实例）：按命令行匹配结束
$runners = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*daemon-runner.ps1*' }
foreach ($r in $runners) {
    if ($r.ProcessId -eq $PID) { continue }
    Write-Host "[信息] 结束残留 runner PID $($r.ProcessId)。" -ForegroundColor Yellow
    Stop-Process -Id $r.ProcessId -Force -ErrorAction SilentlyContinue
}

# ---------- 4. 最终校验（留出端口释放时间） ----------
$deadline2 = (Get-Date).AddSeconds(5)
while ((Get-Date) -lt $deadline2 -and
    ($null -ne (Get-TcpOwner 8000) -or $null -ne (Get-UdpOwner 39001))) {
    Start-Sleep -Milliseconds 500
}
Remove-Item $pidFile -Force -ErrorAction SilentlyContinue

$t = Get-TcpOwner 8000
$u = Get-UdpOwner 39001
if ($null -ne $t -or $null -ne $u) {
    Write-Host "[失败] 停止后端口仍被占用：TCP 8000=$(if ($null -eq $t) { '空闲' } else { $t })；UDP 39001=$(if ($null -eq $u) { '空闲' } else { $u })" -ForegroundColor Red
    exit 1
}

Write-Host '[完成] 后端已停止，TCP 8000 / UDP 39001 均已释放。' -ForegroundColor Green
exit 0

# 守护化启动（幂等）：已运行则提示并退出；否则注册/校验计划任务 PathAI-Backend 并立即启动，
# 等待 /healthz 通过后输出状态。计划任务的动作始终是 daemon-runner.ps1 → start-local.ps1。
# 用法：powershell -ExecutionPolicy Bypass -File backend\.local\daemon-start.ps1 [-TimeoutSec 45]

param([int]$TimeoutSec = 45)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$taskName = 'PathAI-Backend'
$runner = Join-Path $here 'daemon-runner.ps1'
$startScript = Join-Path $here 'start-local.ps1'

function Get-TcpOwner([int]$port) {
    # 兼容部分环境（含沙箱/代理）里 Get-NetTCPConnection 的 State 字段为空的情况：
    # 先按 Listen 过滤，查不到再退化为按端口号匹配（服务端连接同样归属该 PID）。
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

function Show-Occupant([int]$procId, [string]$label) {
    $p = Get-ProcInfo $procId
    $name = if ($null -ne $p) { $p.Name } else { '<已退出>' }
    $cmd = if ($null -ne $p -and $null -ne $p.CommandLine) { $p.CommandLine } else { '<不可读>' }
    Write-Host "[错误] $label 被非本项目进程占用：PID=$procId 名称=$name" -ForegroundColor Red
    Write-Host "       命令行：$cmd" -ForegroundColor DarkGray
    Write-Host '       处理建议：确认该进程用途后手动结束，或改用其他端口（本方案固定 8000/39001）。' -ForegroundColor DarkGray
}

# ---------- 0. 前置检查 ----------
if (-not (Test-Path $startScript)) {
    Write-Host '[错误] 缺少 start-local.ps1（环境变量注入/迁移/启动入口），无法启动。' -ForegroundColor Red
    exit 4
}
if (-not (Test-Path $runner)) {
    Write-Host '[错误] 缺少 daemon-runner.ps1。' -ForegroundColor Red
    exit 4
}

# ---------- 1. 幂等：已在运行则只提示 ----------
$tcpOwner = Get-TcpOwner 8000
if ($null -ne $tcpOwner) {
    if (Test-OurBackend $tcpOwner) {
        Write-Host "[提示] 后端已在运行（PID $tcpOwner，8000 已监听），无需重复启动。" -ForegroundColor Yellow
        & (Join-Path $here 'daemon-status.ps1')
        exit 0
    }
    Show-Occupant $tcpOwner 'TCP 8000'
    exit 1
}
$udpOwner = Get-UdpOwner 39001
if ($null -ne $udpOwner) {
    if (Test-OurBackend $udpOwner) {
        Write-Host "[提示] 发现本项目残留实例（UDP 39001，PID $udpOwner），请先执行 daemon-stop.ps1。" -ForegroundColor Yellow
        exit 1
    }
    Show-Occupant $udpOwner 'UDP 39001'
    exit 1
}

# ---------- 2. 注册/校正计划任务（动作：daemon-runner.ps1 → start-local.ps1） ----------
# 始终 Register -Force：让动作/触发器/重启策略与脚本保持一致（本分支只在后端未运行时才会走到这里）。
$expectedArgs = ('-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f (Join-Path $here 'daemon-runner.ps1'))
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $expectedArgs
# 触发器：登录自启。崩溃自恢复由 daemon-runner.ps1 自身的重试循环负责
# （实测 Task Scheduler 的 RestartOnFailure 在进程被外部杀死时不触发，结果码 -1）。
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $taskName `
    -Action $action -Trigger $logonTrigger -Settings $settings `
    -Description 'PathAI 本地后端守护进程（调用 backend/.local/start-local.ps1）' `
    -Force | Out-Null
Write-Host "[信息] 已注册计划任务 $taskName（登录自启；runner 内建 15 秒崩溃重启）。" -ForegroundColor Green

Start-ScheduledTask -TaskName $taskName

# ---------- 3. 等待健康检查通过 ----------
$deadline = (Get-Date).AddSeconds($TimeoutSec)
$ok = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 1
    try {
        $resp = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/healthz' -UseBasicParsing -TimeoutSec 3
        if ($resp.StatusCode -eq 200) { $ok = $true; break }
    } catch { }
}

if (-not $ok) {
    Write-Host "[失败] ${TimeoutSec}s 内 /healthz 未就绪，日志末尾：" -ForegroundColor Red
    $log = Get-ChildItem (Join-Path $here 'logs') -Filter 'backend-*.log' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($null -ne $log) { Get-Content $log.FullName -Tail 25 }
    exit 1
}

Write-Host "[完成] 后端已由计划任务托管并健康。" -ForegroundColor Green
& (Join-Path $here 'daemon-status.ps1')
exit 0

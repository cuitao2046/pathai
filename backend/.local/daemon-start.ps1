# 守护化启动（幂等）：已运行则提示并退出；否则注册/校正计划任务 PathAI-Backend 并立即启动，
# 等待 /healthz 通过后输出状态。计划任务的动作始终是 daemon-runner.ps1 → start-local.ps1。
# 任务以 S4U（会话 0）运行，彻底脱离交互终端/控制台；动作注入真实 Python 目录（会话 0 下
# WindowsApps 别名无法启动）。看门狗触发器每分钟重复，runner 死掉后会被重新拉起。
# 用法：powershell -ExecutionPolicy Bypass -File backend\.local\daemon-start.ps1 [-TimeoutSec 45]
# 编码：必须保存为 UTF-8 with BOM。

param([int]$TimeoutSec = 45)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$taskName = 'PathAI-Backend'
$runner = Join-Path $here 'daemon-runner.ps1'
$startScript = Join-Path $here 'start-local.ps1'

function Get-TcpOwner([int]$port) {
    # 会话 0 下 State 可能为空；忽略 OwningProcess=0 的“幻影行”，返回第一个有真实 owner 的 PID。
    try {
        foreach ($r in @(Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue)) {
            $owner = [int]$r.OwningProcess
            if ($owner -ne 0) { return $owner }
        }
    } catch { }
    return $null
}
function Get-UdpOwner([int]$port) {
    try {
        $c = Get-NetUDPEndpoint -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -eq $c) { return $null }
        $owner = [int]$c.OwningProcess
        if ($owner -eq 0) { return $null }
        return $owner
    } catch { return $null }
}
function Get-ProcInfo([int]$procId) {
    return Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
}
function Test-OurBackend([int]$procId) {
    $p = Get-ProcInfo $procId
    if ($null -eq $p) { return $false }
    return ($p.CommandLine -like '*pathai_backend*')
}
function Test-PortListening([int]$port) {
    # 先做回环 TCP 连接探测：能连上才是真的在监听。
    # 本机会话 0 下 Get-NetTCPConnection 会出现 State 为空、OwningProcess=0 的“幻影行”
    # （netstat 无 LISTENING），若直接按行数判断会误报端口被占。
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect('127.0.0.1', $port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne(400)) { $client.Close(); return $false }
        $client.EndConnect($iar)
        $client.Close()
        return $true
    } catch { }
    # 兜底：仅把 State=Listen 或 OwningProcess 非 0 的行当作监听（忽略 owner=0 的幻影行）。
    try {
        foreach ($r in @(Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue)) {
            if ($r.State -eq 'Listen') { return $true }
            if ([int]$r.OwningProcess -ne 0) { return $true }
        }
    } catch { }
    return $false
}
function Test-Healthz {
    try {
        $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/healthz' -UseBasicParsing -TimeoutSec 3
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}
function Get-BackendPid {
    # PID 兜底顺序：TCP owner（非 0）→ UDP 39001 owner（非 0）→ 进程命令行（uvicorn + pathai_backend）
    $tcp = Get-TcpOwner 8000
    if ($null -ne $tcp -and $tcp -ne 0) { return [int]$tcp }
    $udp = Get-UdpOwner 39001
    if ($null -ne $udp -and $udp -ne 0) { return [int]$udp }
    $p = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*uvicorn*' -and $_.CommandLine -like '*pathai_backend*' } |
        Select-Object -First 1
    if ($null -ne $p) { return [int]$p.ProcessId }
    return $null
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

# ---------- 1. 幂等与端口预检 ----------
$listening = Test-PortListening 8000
$udpOwner = Get-UdpOwner 39001
$tcpOwner = Get-TcpOwner 8000
$backendPid = Get-BackendPid
$ourBackend = ($null -ne $backendPid -and (Test-OurBackend $backendPid))

if ($listening -or $null -ne $udpOwner -or $ourBackend) {
    if ($ourBackend) {
        Write-Host "[提示] 后端已在运行（PID $backendPid），无需重复启动。" -ForegroundColor Yellow
        & (Join-Path $here 'daemon-status.ps1')
        exit 0
    }
    if (Test-Healthz) {
        Write-Host '[提示] 后端已在运行（/healthz 200；会话 0 下 PID 可能不可见），无需重复启动。' -ForegroundColor Yellow
        & (Join-Path $here 'daemon-status.ps1')
        exit 0
    }
    # 端口确实被占且不是本项目：明确报错（优先展示能解析到的 PID/命令行）
    $shown = 0
    if ($null -ne $backendPid) { $shown = $backendPid }
    elseif ($null -ne $udpOwner -and $udpOwner -ne 0) { $shown = [int]$udpOwner }
    elseif ($null -ne $tcpOwner -and $tcpOwner -ne 0) { $shown = [int]$tcpOwner }
    if ($shown -gt 0) {
        Show-Occupant $shown 'TCP 8000 / UDP 39001'
    } else {
        Write-Host '[错误] 端口 8000/39001 被占用，但无法解析 PID，且 /healthz 不通（非本项目进程）。' -ForegroundColor Red
        Write-Host '       定位：netstat -ano | findstr :8000 ；tasklist /FI "PID eq <pid>"' -ForegroundColor DarkGray
    }
    exit 1
}

# ---------- 2. 解析真实 Python（会话 0 下 WindowsApps 别名不可用） ----------
$pyExe = $null
try {
    $pyExe = (& python -c "import sys; print(sys.executable)" 2>$null) | Select-Object -First 1
} catch { }
if ($null -ne $pyExe) { $pyExe = $pyExe.ToString().Trim() }
if ($null -eq $pyExe -or -not (Test-Path $pyExe)) {
    $cmd = (Get-Command python -ErrorAction SilentlyContinue).Source
    if ($null -ne $cmd) { $pyExe = $cmd }
}
if ($null -eq $pyExe -or -not (Test-Path $pyExe)) {
    Write-Host '[错误] 找不到 python 可执行文件（PATH 中没有 python）。' -ForegroundColor Red
    exit 2
}
$pyDir = Split-Path $pyExe -Parent

# ---------- 3. 注册/校正计划任务（S4U；动作：daemon-runner.ps1 → start-local.ps1） ----------
# 始终 Register -Force：让动作/主体/触发器/重启策略与脚本保持一致（本流程只在后端未运行时才会走到这里）。
$arg = ('/c set "PATH={0};%PATH%" && powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{1}"' -f $pyDir, $runner)
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument $arg
$principal = New-ScheduledTaskPrincipal -UserId ("{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME) -LogonType S4U -RunLevel Limited
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn
# 看门狗：每分钟重复；任务在跑时由 IgnoreNew 忽略，runner 死掉后 1 分钟内被重新拉起
# （RestartOnFailure 对“外部强杀”实测不触发，仅作兜底）。
$watchdogTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 1)
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $taskName `
    -Action $action -Principal $principal -Trigger @($logonTrigger, $watchdogTrigger) -Settings $settings `
    -Description 'PathAI 本地后端守护进程（S4U 会话 0；调用 daemon-runner.ps1 → start-local.ps1）' `
    -Force | Out-Null
Enable-ScheduledTask -TaskName $taskName | Out-Null
Write-Host "[信息] 已注册计划任务 $taskName（S4U 会话 0；登录自启 + 每分钟看门狗；runner 内建 15 秒崩溃重启）。" -ForegroundColor Green
Write-Host ("       Python：{0}" -f $pyExe) -ForegroundColor DarkGray

Start-ScheduledTask -TaskName $taskName

# ---------- 4. 等待健康检查通过 ----------
$deadline = (Get-Date).AddSeconds($TimeoutSec)
$ok = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 1
    if (Test-Healthz) { $ok = $true; break }
}

if (-not $ok) {
    Write-Host "[失败] ${TimeoutSec}s 内 /healthz 未就绪，日志末尾：" -ForegroundColor Red
    $log = Get-ChildItem (Join-Path $here 'logs') -Filter 'backend-*.log' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($null -ne $log) { Get-Content $log.FullName -Tail 25 }
    exit 1
}

Write-Host '[完成] 后端已由计划任务托管（S4U 会话 0）并健康。' -ForegroundColor Green
& (Join-Path $here 'daemon-status.ps1')
exit 0

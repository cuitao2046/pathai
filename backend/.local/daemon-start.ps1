# 守护化启动（幂等）：已运行则提示并退出；否则注册/校正计划任务 PathAI-Backend 并立即启动，
# 等待 /healthz 通过后输出状态。计划任务的动作始终是 daemon-runner.ps1 → start-local.ps1。
# 任务以 S4U（会话 0）运行，彻底脱离交互终端/控制台；动作注入真实 Python 目录（会话 0 下
# WindowsApps 别名无法启动）。看门狗触发器每分钟重复，runner 死掉后会被重新拉起。
# 用法：powershell -ExecutionPolicy Bypass -File backend\.local\daemon-start.ps1 [-TimeoutSec 45]
# 退出码：0=已运行或启动成功；1=端口被非本项目进程占用/启动自检失败；2=找不到 python；
#         3=8000 被本项目旧实例占用（路由表缺关键路由，需先 daemon-stop）；4=缺少脚本。
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
# 启动自检依赖的关键路由：只校验"新代码才有的路径"，旧进程必然缺其中至少一条。
$script:RequiredRoutes = @(
    '/api/v1/config/location-params',
    '/api/v1/calibration/readiness',
    '/api/v1/fingerprint-db/active'
)

function Get-ProcSessionId([int]$procId) {
    try {
        $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($null -ne $p) { return [int]$p.SessionId }
    } catch { }
    return $null
}
function Format-ProcDetail([int]$procId) {
    # 统一输出 PID/名称/启动时间/SessionId/命令行，便于直接判定"是哪个进程在跑哪份代码"。
    $p = Get-ProcInfo $procId
    if ($null -eq $p) { return ("PID={0}（进程已退出，命令行/启动时间不可读）" -f $procId) }
    $cmd = if ($null -ne $p.CommandLine) { $p.CommandLine } else { '<不可读>' }
    $created = if ($null -ne $p.CreationDate) { ([datetime]$p.CreationDate).ToString('yyyy-MM-dd HH:mm:ss') } else { '<未知>' }
    $sid = Get-ProcSessionId $procId
    $sidText = if ($null -eq $sid) { '<未知>' } else { $sid.ToString() }
    return ("PID={0} 名称={1} 启动时间={2} SessionId={3}`r`n       命令行：{4}" -f $procId, $p.Name, $created, $sidText, $cmd)
}
function Get-PortOwnerPid {
    # 只认端口真实 owner：TCP 8000 → UDP 39001 → 进程命令行扫描（会话 0 下 owner 可能不可见）。
    # 注意：命令行扫描是最后兜底，可能命中"没在监听但残留的 python"，因此仅在端口 owner 不可见时使用。
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
function Get-OpenApiPaths {
    # 返回实际加载的路由路径列表；拿不到（超时/非 200/非 JSON）返回 $null。
    try {
        $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/openapi.json' -UseBasicParsing -TimeoutSec 5
        if ($r.StatusCode -ne 200) { return $null }
        $doc = ($r.Content | ConvertFrom-Json)
        if ($null -eq $doc.paths) { return @() }
        return @($doc.paths.PSObject.Properties.Name)
    } catch { return $null }
}
function Get-MissingRoutes {
    $paths = Get-OpenApiPaths
    if ($null -eq $paths) { return @($script:RequiredRoutes) }
    $missing = @()
    foreach ($need in $script:RequiredRoutes) {
        if ($paths -notcontains $need) { $missing += $need }
    }
    return $missing
}
function Invoke-StartFailureCleanup {
    # 启动失败清理：停任务 + 只结束命令行含 pathai_backend 的本项目进程；
    # 同时禁用任务，避免看门狗每分钟把同样的坏配置再拉起来刷日志。
    Write-Host '[清理] 停止计划任务并结束本项目后端进程…' -ForegroundColor Yellow
    try { Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue } catch { }
    try { Disable-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue | Out-Null } catch { }
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*pathai_backend*' }
    foreach ($p in $procs) {
        Write-Host ("       结束 PID {0}" -f $p.ProcessId) -ForegroundColor DarkGray
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Write-Host '[清理] 计划任务已停止并禁用；修复原因后重新执行 daemon-start.ps1 即可重新启用。' -ForegroundColor Yellow
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

# ---------- 1. 幂等与端口归属校验 ----------
# 关键：不能因为"8000 有人监听"就认为"本后端在跑"。必须按占用进程的命令行 + 实际路由表判定，
# 否则遗留的旧代码进程会让本脚本静默退出 0，而客户端继续吃 404。
$listening = Test-PortListening 8000
$udpOwner = Get-UdpOwner 39001
$occupied = ($listening -or $null -ne $udpOwner)

if ($occupied) {
    $ownerPid = Get-PortOwnerPid
    $ownerIsOurs = ($null -ne $ownerPid -and (Test-OurBackend $ownerPid))

    if ($null -eq $ownerPid) {
        # 会话 0 下 owner 可能不可见：只有【路由表命中全部关键路由】才认作本项目；
        # 仅 /healthz 200 不算（任何 FastAPI 都可能返回 200，这正是上次误判的入口）。
        $missing = Get-MissingRoutes
        if (@($missing).Count -eq 0) {
            $ownerIsOurs = $true
            Write-Host '[提示] 8000 已有监听且 /openapi.json 含全部关键路由（PID 在会话 0 下不可见），判定为本项目后端在运行。' -ForegroundColor Yellow
        }
    }

    if ($ownerIsOurs) {
        if ($null -ne $ownerPid) {
            Write-Host ('[提示] 已有本项目后端在运行：' + (Format-ProcDetail $ownerPid)) -ForegroundColor Yellow
        }
        $missing = Get-MissingRoutes
        if (@($missing).Count -gt 0) {
            Write-Host '[错误] 监听进程是本项目后端，但 /openapi.json 无法读取或缺少关键路由，说明它跑的是旧代码：' -ForegroundColor Red
            foreach ($m in $missing) { Write-Host ("       缺失：{0}" -f $m) -ForegroundColor Red }
            Write-Host '       这不是"已在运行"，是遗留进程占着 8000，不会自动强杀。按顺序执行：' -ForegroundColor Yellow
            Write-Host '         powershell -ExecutionPolicy Bypass -File backend\.local\daemon-stop.ps1' -ForegroundColor DarkGray
            Write-Host '         powershell -ExecutionPolicy Bypass -File backend\.local\daemon-start.ps1' -ForegroundColor DarkGray
            exit 3
        }
        $sid = if ($null -ne $ownerPid) { Get-ProcSessionId $ownerPid } else { $null }
        if ($null -ne $sid -and $sid -ne 0) {
            Write-Host ("[警告] 该后端运行在 SessionId={0}（非会话 0），不受计划任务托管，关闭终端后可能消失。" -f $sid) -ForegroundColor Yellow
        }
        Write-Host '[提示] 后端已在运行且关键路由齐全，无需重复启动。' -ForegroundColor Yellow
        & (Join-Path $here 'daemon-status.ps1')
        exit 0
    }

    # 端口被占用，且占用者不是本项目后端 → 直接失败，不等待、不空转、不误杀
    if ($null -ne $ownerPid) {
        Write-Host ('[错误] 端口 8000/39001 被非本项目进程占用：' + (Format-ProcDetail $ownerPid)) -ForegroundColor Red
    } else {
        Write-Host '[错误] 端口 8000/39001 被占用，但无法解析占用 PID，且 /openapi.json 缺少本项目关键路由（判定为非本项目进程）。' -ForegroundColor Red
        Write-Host '       定位：netstat -ano | findstr :8000 ；tasklist /FI "PID eq <pid>"' -ForegroundColor DarkGray
    }
    Write-Host '       不会自动结束该进程。确认用途后手动结束，或改用其他端口（本方案固定 8000/39001）。' -ForegroundColor DarkGray
    exit 1
}

Write-Host '[信息] 端口 8000/39001 空闲，准备启动守护任务。' -ForegroundColor DarkGray

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

# ---------- 4. 等待就绪（带日志，不静默空转） ----------
$deadline = (Get-Date).AddSeconds($TimeoutSec)
$ok = $false
$waited = 0
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 1
    $waited++
    if (Test-Healthz) { $ok = $true; break }
    if ($waited % 10 -eq 0) {
        Write-Host ("[等待] /healthz 未就绪，继续等待（已等 {0}s / 上限 {1}s）…" -f $waited, $TimeoutSec) -ForegroundColor DarkGray
    }
}

if (-not $ok) {
    Write-Host "[失败] ${TimeoutSec}s 内 /healthz 未就绪，日志末尾：" -ForegroundColor Red
    $log = Get-ChildItem (Join-Path $here 'logs') -Filter 'backend-*.log' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($null -ne $log) { Get-Content $log.FullName -Tail 25 }
    Invoke-StartFailureCleanup
    exit 1
}

# ---------- 5. 启动后自检：路由表 + 会话归属 ----------
# /healthz 只能证明"有 FastAPI 在跑"，必须确认新路由真的加载了，否则就是旧代码进程。
$missing = Get-MissingRoutes
if (@($missing).Count -gt 0) {
    Write-Host '[失败] /healthz 已 200，但 /openapi.json 无法读取或缺少关键路由：' -ForegroundColor Red
    foreach ($m in $missing) { Write-Host ("       缺失：{0}" -f $m) -ForegroundColor Red }
    Write-Host '       典型原因：8000 上仍是旧进程（旧代码），或启动的是别的 FastAPI 实例。' -ForegroundColor Yellow
    Invoke-StartFailureCleanup
    exit 1
}
Write-Host ('[信息] 路由自检通过：{0}' -f ($script:RequiredRoutes -join '、')) -ForegroundColor DarkGray

$ownerPid = Get-PortOwnerPid
if ($null -ne $ownerPid) {
    Write-Host ('[信息] 后端进程：' + (Format-ProcDetail $ownerPid)) -ForegroundColor DarkGray
    $sid = Get-ProcSessionId $ownerPid
    if ($null -ne $sid -and $sid -ne 0) {
        Write-Host ("[失败] 后端进程 SessionId={0}，不是计划任务的会话 0，不受守护托管（关终端即消失）。" -f $sid) -ForegroundColor Red
        Invoke-StartFailureCleanup
        exit 1
    }
} else {
    Write-Host '[警告] 无法解析监听进程 PID（会话 0 下可能不可见），跳过 SessionId 校验。' -ForegroundColor Yellow
}

Write-Host '[完成] 后端已由计划任务托管（S4U 会话 0），/healthz 正常且关键路由齐全。' -ForegroundColor Green
& (Join-Path $here 'daemon-status.ps1')
exit 0

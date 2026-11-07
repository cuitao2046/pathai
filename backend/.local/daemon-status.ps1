# 守护化状态：计划任务状态 + runner/uvicorn 进程与父子链 + 8000/39001 监听 + /healthz + 地图版本 + /admin/
# 用法：powershell -ExecutionPolicy Bypass -File backend\.local\daemon-status.ps1
# 编码：必须保存为 UTF-8 with BOM。

$ErrorActionPreference = 'Continue'
$here = $PSScriptRoot
$taskName = 'PathAI-Backend'
$pidFile = Join-Path $here 'run\backend.pid.json'

function Get-TcpOwner([int]$port) {
    # 会话 0 下 State 可能为空；忽略 OwningProcess=0 的“幻影行”。
    try {
        foreach ($r in @(Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue)) {
            $owner = [int]$r.OwningProcess
            if ($owner -ne 0) { return $owner }
        }
    } catch { }
    return $null
}
function Get-UdpOwner([int]$port) {
    $c = Get-NetUDPEndpoint -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $c) { return $null }
    $owner = [int]$c.OwningProcess
    if ($owner -eq 0) { return $null }
    return $owner
}
function Get-ProcInfo([int]$procId) {
    return Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
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
function Format-Proc([int]$procId) {
    $p = Get-ProcInfo $procId
    if ($null -eq $p) { return "PID $procId <已退出>" }
    return ('PID {0} {1} (父 PID {2})' -f $procId, $p.Name, $p.ParentProcessId)
}

Write-Host '=== 1. 计划任务 ===' -ForegroundColor Cyan
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Host "未注册（$taskName）。" -ForegroundColor Yellow
} else {
    $info = Get-ScheduledTaskInfo -TaskName $taskName -ErrorAction SilentlyContinue
    Write-Host ("任务：{0}  状态：{1}  上次结果：{2}  上次运行：{3}" -f `
        $taskName, $task.State, $info.LastTaskResult, $info.LastRunTime)
    $action = $task.Actions | Select-Object -First 1
    Write-Host ("动作：{0} {1}" -f $action.Execute, $action.Arguments) -ForegroundColor DarkGray
    Write-Host ("策略：多实例={0}  崩溃重启={1} 次 / 间隔 {2}" -f `
        $task.Settings.MultipleInstances, $task.Settings.RestartCount, $task.Settings.RestartInterval) -ForegroundColor DarkGray
}

Write-Host ''
Write-Host '=== 2. 进程与父子链 ===' -ForegroundColor Cyan
$pidJson = $null
if (Test-Path $pidFile) {
    try { $pidJson = Get-Content $pidFile -Raw -Encoding UTF8 | ConvertFrom-Json } catch { }
}
if ($null -ne $pidJson) {
    Write-Host ("PID 文件：runner={0}  启动于 {1}" -f $pidJson.runnerPid, $pidJson.startedAt)
    if ($null -ne $pidJson.pythonExe) { Write-Host ("python：{0}" -f $pidJson.pythonExe) -ForegroundColor DarkGray }
} else {
    Write-Host 'PID 文件：不存在（任务未运行或已退出）' -ForegroundColor DarkGray
}

$tcpOwner = Get-TcpOwner 8000
$udpOwner = Get-UdpOwner 39001
$listening8000 = Test-PortListening 8000
$backendPid = Get-BackendPid
if (-not $listening8000) {
    Write-Host '8000 无监听进程。' -ForegroundColor Yellow
} elseif ($null -ne $backendPid) {
    $sessionId = try { (Get-Process -Id $backendPid -ErrorAction Stop).SessionId } catch { -1 }
    $pidSource = if ($null -ne $tcpOwner -and $tcpOwner -ne 0) { 'TCP owner' } else { 'UDP/进程命令行兜底' }
    Write-Host ('8000 监听：' + (Format-Proc $backendPid) + "  SessionId=$sessionId  PID来源=$pidSource")
    # 向上追溯 4 层，说明挂在哪（预期：python <- powershell(start-local) <- powershell(runner) <- svchost(Task Scheduler)）
    $cur = $backendPid
    for ($i = 0; $i -lt 4; $i++) {
        $p = Get-ProcInfo $cur
        if ($null -eq $p -or $p.ParentProcessId -eq 0) { break }
        $cur = [int]$p.ParentProcessId
        Write-Host ('  ↑ ' + (Format-Proc $cur)) -ForegroundColor DarkGray
    }
} else {
    Write-Host '8000 有监听，但无法解析 PID（会话 0 或权限受限），可能被非本项目进程占用。' -ForegroundColor Red
}
if ($null -eq $udpOwner) {
    Write-Host '39001（UDP 发现）：无监听。' -ForegroundColor Yellow
} else {
    Write-Host ('39001 监听：' + (Format-Proc $udpOwner))
}

Write-Host ''
Write-Host '=== 3. 健康与接口 ===' -ForegroundColor Cyan
$lanIp = '192.168.1.28'
try {
    $ip = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
        Select-Object -First 1
    if ($null -ne $ip) { $lanIp = $ip.IPAddress }
} catch { }

foreach ($base in @('http://127.0.0.1:8000', "http://${lanIp}:8000")) {
    try {
        $r = Invoke-WebRequest -Uri "$base/healthz" -UseBasicParsing -TimeoutSec 3
        Write-Host ("/healthz {0} -> {1} {2}" -f $base, $r.StatusCode, $r.Content) -ForegroundColor Green
    } catch {
        Write-Host ("/healthz {0} -> 失败：{1}" -f $base, $_.Exception.Message) -ForegroundColor Red
    }
}

# 地图版本：假登录取 token 后查询（PATHAI_WECHAT_FAKE=1 生效说明环境变量注入正常）
$token = $null
try {
    $login = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/v1/auth/wechat' -Method Post `
        -ContentType 'application/json' -Body '{"code":"fake-daemon-check"}' -TimeoutSec 5
    $token = $login.accessToken
} catch {
    Write-Host ("假登录失败：{0}" -f $_.Exception.Message) -ForegroundColor Red
}
if ($null -ne $token) {
    Write-Host '假登录：OK（PATHAI_WECHAT_FAKE 注入生效）' -ForegroundColor Green
    try {
        $map = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/v1/sites/school-building-01/map?version=latest' `
            -Headers @{ Authorization = "Bearer $token" } -TimeoutSec 10
        Write-Host ("地图：mapVersion={0} dataSha256={1}" -f $map.mapVersion, $map.dataSha256) -ForegroundColor Green
        if ($map.mapVersion -ne '2026-11-07.1') {
            Write-Host '[警告] mapVersion 与预期 2026-11-07.1 不一致。' -ForegroundColor Yellow
        }
    } catch {
        Write-Host ("地图接口失败：{0}" -f $_.Exception.Message) -ForegroundColor Red
    }
}

try {
    $admin = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/admin/' -UseBasicParsing -TimeoutSec 3
    Write-Host ("/admin/ -> {0}（{1} 字节）" -f $admin.StatusCode, $admin.RawContentLength) -ForegroundColor Green
} catch {
    Write-Host ("/admin/ -> 失败：{0}" -f $_.Exception.Message) -ForegroundColor Red
}

Write-Host ''
Write-Host '=== 4. 结论 ===' -ForegroundColor Cyan
if ($listening8000 -and $null -ne $udpOwner) {
    Write-Host '后端运行中（8000 + 39001 均在监听）。' -ForegroundColor Green
} elseif ($listening8000) {
    Write-Host '后端部分运行（8000 监听，39001 未监听——UDP 发现不可用）。' -ForegroundColor Yellow
} elseif ($null -ne $udpOwner) {
    Write-Host '后端部分运行（39001 监听，8000 未监听——HTTP 接口不可用）。' -ForegroundColor Yellow
} else {
    Write-Host '后端未运行。启动：powershell -ExecutionPolicy Bypass -File backend\.local\daemon-start.ps1' -ForegroundColor Yellow
}

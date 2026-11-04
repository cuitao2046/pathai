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
    $c = Get-NetUDPEndpoint -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $c) { return $null }
    $owner = [int]$c.OwningProcess
    if ($owner -eq 0) { return $null }
    return $owner
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
function Get-BackendProcesses {
    return Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*pathai_backend*' }
}

# ---------- 1. 停止计划任务并禁用看门狗 ----------
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
    # 看门狗触发器每分钟重复；必须禁用任务，否则 1 分钟内会被重新拉起（daemon-start 会重新启用）
    try {
        Disable-ScheduledTask -TaskName $taskName | Out-Null
        Write-Host "[信息] 已禁用计划任务 $taskName（看门狗不会自动拉起；再次 daemon-start.ps1 会启用）。" -ForegroundColor Green
    } catch {
        Write-Host "[警告] 禁用计划任务失败：$($_.Exception.Message)" -ForegroundColor Yellow
    }
} else {
    Write-Host "[提示] 未发现计划任务 $taskName（可能为会话内启动的实例）。"
}

# ---------- 2. 等待端口释放 ----------
$deadline = (Get-Date).AddSeconds($TimeoutSec)
while ((Get-Date) -lt $deadline -and ((Test-PortListening 8000) -or ($null -ne (Get-UdpOwner 39001)))) {
    Start-Sleep -Seconds 1
}

# ---------- 3. 残留处理（会话 0 下 TCP owner 可能为 0，逐级兜底） ----------
$backendPid = Get-BackendPid
if ($null -ne $backendPid -and (Test-OurBackend $backendPid)) {
    Write-Host "[信息] 端口仍被本项目残留进程占用（PID $backendPid），强制终止。" -ForegroundColor Yellow
    try {
        Stop-Process -Id $backendPid -Force -ErrorAction Stop
    } catch {
        Write-Host "[错误] 终止 PID $backendPid 失败：$($_.Exception.Message)" -ForegroundColor Red
    }
} elseif ((Test-PortListening 8000) -and (Test-Healthz)) {
    # TCP owner 不可见（=0）但 /healthz 仍 200：确定是本项目后端，按命令行清理
    $procs = Get-BackendProcesses
    foreach ($p in $procs) {
        Write-Host "[信息] 按命令行识别到本项目残留进程 PID $($p.ProcessId)，强制终止。" -ForegroundColor Yellow
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if (@($procs).Count -eq 0) {
        Write-Host '[警告] /healthz 仍可访问，但未找到 pathai_backend 进程（可能权限受限）。' -ForegroundColor Yellow
    }
} else {
    $tcpOwnerNow = Get-TcpOwner 8000
    $udpOwnerNow = Get-UdpOwner 39001
    $ownerShown = 0
    if ($null -ne $udpOwnerNow -and $udpOwnerNow -ne 0) { $ownerShown = [int]$udpOwnerNow }
    elseif ($null -ne $tcpOwnerNow -and $tcpOwnerNow -ne 0) { $ownerShown = [int]$tcpOwnerNow }
    if ($ownerShown -gt 0) {
        $p = Get-ProcInfo $ownerShown
        $name = if ($null -ne $p) { $p.Name } else { '<已退出>' }
        $cmd = if ($null -ne $p -and $null -ne $p.CommandLine) { $p.CommandLine } else { '<不可读>' }
        Write-Host "[错误] 端口被非本项目进程占用（PID $ownerShown 名称=$name），未终止。" -ForegroundColor Red
        Write-Host "       命令行：$cmd" -ForegroundColor DarkGray
    } elseif ((Test-PortListening 8000) -or ($null -ne $udpOwnerNow)) {
        Write-Host '[错误] 端口仍被占用，但会话 0 下无法解析 PID，且 /healthz 不通（非本项目进程），未终止。' -ForegroundColor Red
        Write-Host '       定位：netstat -ano | findstr :8000 ；tasklist /FI "PID eq <pid>"' -ForegroundColor DarkGray
    } else {
        Write-Host '[信息] 端口已释放，无残留进程。' -ForegroundColor DarkGray
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
    ((Test-PortListening 8000) -or ($null -ne (Get-UdpOwner 39001)))) {
    Start-Sleep -Milliseconds 500
}
Remove-Item $pidFile -Force -ErrorAction SilentlyContinue

$t = Test-PortListening 8000
$u = Get-UdpOwner 39001
if ($t -or $null -ne $u) {
    Write-Host "[失败] 停止后端口仍被占用：TCP 8000=$(if ($t) { '监听中' } else { '空闲' })；UDP 39001=$(if ($null -eq $u) { '空闲' } else { $u })" -ForegroundColor Red
    exit 1
}

Write-Host '[完成] 后端已停止，TCP 8000 / UDP 39001 均已释放。' -ForegroundColor Green
exit 0

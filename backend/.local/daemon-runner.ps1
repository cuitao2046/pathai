# 守护进程主体：由「计划任务 PathAI-Backend」拉起（父进程是 Task Scheduler，不是任何终端）。
# 关键约束：**必须调用 start-local.ps1**（环境变量注入 + alembic + uvicorn + UTF-8 BOM 都在那里），
#           本脚本只做进程托管、日志追加与重启循环，不直接调 python/uvicorn。
# 崩溃自恢复：start-local.ps1（uvicorn）退出后等待 15 秒重新调用（同一次任务运行内闭环，
#              不依赖 Task Scheduler 的 RestartOnFailure —— 实测外部杀进程时它不触发）。
# 退出码约定：2=找不到 python；3=端口被占；4=缺少 start-local.ps1；正常只在任务被停止时退出。

# 注意：不能设为 Stop —— PowerShell 5.1 对原生命令执行 2>&1 时，stderr 会被包装成
# NativeCommandError 错误记录；Stop 会让 runner 在 alembic 第一条 stderr 日志处直接终止
# （表现为日志只有 runner 启动行、任务结果 1、端口无人监听）。
$ErrorActionPreference = 'Continue'
$here = $PSScriptRoot
$logDir = Join-Path $here 'logs'
$runDir = Join-Path $here 'run'
foreach ($d in @($logDir, $runDir)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
}
$logFile = Join-Path $logDir ('backend-' + (Get-Date -Format 'yyyyMMdd') + '.log')
$pidFile = Join-Path $runDir 'backend.pid.json'
$startScript = Join-Path $here 'start-local.ps1'

function Write-RunnerLog([string]$message) {
    ('[{0}] [runner] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $message) |
        Out-File -FilePath $logFile -Encoding utf8 -Append
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
function Get-ProcInfo([int]$procId) {
    return Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
}
function Format-Occupant([int]$procId) {
    # 端口占用者画像：是谁、什么时候起的、在哪个会话、是不是本项目 —— 避免下次又只看到"已有监听"。
    $p = Get-ProcInfo $procId
    if ($null -eq $p) { return ("PID={0}（进程已退出）" -f $procId) }
    $cmd = if ($null -ne $p.CommandLine) { $p.CommandLine } else { '<不可读>' }
    $created = if ($null -ne $p.CreationDate) { ([datetime]$p.CreationDate).ToString('yyyy-MM-dd HH:mm:ss') } else { '<未知>' }
    $kind = if ($p.CommandLine -like '*pathai_backend*') { '本项目后端' } else { '非本项目' }
    $sid = $null
    try { $sid = (Get-Process -Id $procId -ErrorAction SilentlyContinue).SessionId } catch { }
    $sidText = if ($null -eq $sid) { '<未知>' } else { $sid.ToString() }
    return ("PID={0} 归属={1} 启动时间={2} SessionId={3} 命令行={4}" -f $procId, $kind, $created, $sidText, $cmd)
}

# 单实例保护：8000 已有监听则让位（不覆盖、不重启、不静默失败）
if (Test-PortListening 8000) {
    $ownerId = Get-TcpOwner 8000
    $detail = if ($null -ne $ownerId) { Format-Occupant $ownerId } else { 'PID 不可见（会话 0 下 owner 可能为 0）' }
    Write-RunnerLog ("检测到端口占用，等待释放：{0}；runner 放弃启动（退出码 3；看门狗 1 分钟后重试）" -f $detail)
    exit 3
}

if (-not (Test-Path $startScript)) {
    Write-RunnerLog '缺少 start-local.ps1（环境变量/迁移/启动入口），runner 退出'
    exit 4
}

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if ($null -eq $python) {
    Write-RunnerLog 'PATH 中找不到 python，runner 退出'
    exit 2
}

$info = [ordered]@{
    runnerPid    = $PID
    startedAt    = (Get-Date).ToString('o')
    pythonExe    = $python
    startScript  = $startScript
    taskName     = 'PathAI-Backend'
}
$info | ConvertTo-Json | Out-File -FilePath $pidFile -Encoding utf8
Write-RunnerLog ("runner 启动 pid=$PID python=$python，调用 start-local.ps1")

$psExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$retrySec = 15
$waitCount = 0
$lastOccupant = ''
try {
    $attempt = 0
    while ($true) {
        $attempt++
        # 若端口已被其他实例占用，先等待而不是反复拉起注定失败的 uvicorn。
        # 必须写明占用者画像，否则日志里只有"已有监听"，事后无法判断是不是旧代码进程。
        if (Test-PortListening 8000) {
            $waitCount++
            $ownerId = Get-TcpOwner 8000
            $detail = if ($null -ne $ownerId) { Format-Occupant $ownerId } else { 'PID 不可见（会话 0 下 owner 可能为 0）' }
            if ($waitCount -eq 1 -or $detail -ne $lastOccupant -or ($waitCount % 4) -eq 0) {
                Write-RunnerLog ("检测到端口占用，等待释放：{0}；{1} 秒后重试（不重复拉起）" -f $detail, $retrySec)
            }
            $lastOccupant = $detail
            Start-Sleep -Seconds $retrySec
            continue
        }
        $waitCount = 0
        $lastOccupant = ''

        Write-RunnerLog ("第 {0} 次调用 start-local.ps1" -f $attempt)
        # 交给 start-local.ps1 完成环境变量注入 + alembic + uvicorn，输出按行追加到日志
        & $psExe -NoProfile -ExecutionPolicy Bypass -File $startScript 2>&1 |
            ForEach-Object { $_.ToString() } |
            Out-File -FilePath $logFile -Encoding utf8 -Append
        $code = $LASTEXITCODE
        Write-RunnerLog ("start-local.ps1 退出（退出码 {0}），{1} 秒后重启" -f $code, $retrySec)
        Start-Sleep -Seconds $retrySec
    }
} finally {
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

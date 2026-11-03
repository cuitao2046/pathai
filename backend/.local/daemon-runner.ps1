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

# 单实例保护：8000 已有监听则让位（不覆盖、不重启、不静默失败）
$owner = (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1).OwningProcess
if ($null -eq $owner) {
    # 兼容 State 字段为空的环境
    $owner = (Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue |
        Select-Object -First 1).OwningProcess
}
if ($null -ne $owner) {
    Write-RunnerLog "端口 8000 已被 PID $owner 占用，runner 放弃启动（退出码 3）"
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
try {
    $attempt = 0
    while ($true) {
        $attempt++
        # 若端口已被其他实例占用，先等待而不是反复拉起注定失败的 uvicorn
        $busy = (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 1).OwningProcess
        if ($null -eq $busy) {
            $busy = (Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue |
                Select-Object -First 1).OwningProcess
        }
        if ($null -ne $busy) {
            Write-RunnerLog "端口 8000 被 PID $busy 占用，$retrySec 秒后重试（不重复拉起）"
            Start-Sleep -Seconds $retrySec
            continue
        }

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

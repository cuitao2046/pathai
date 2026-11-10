# 守护化状态：计划任务状态 + runner/uvicorn 进程与父子链 + 8000/39001 监听 + /healthz + 地图版本自洽性 + /admin/
# 用法：powershell -ExecutionPolicy Bypass -File backend\.local\daemon-status.ps1 [-ExpectedMapVersion <version>]
# 地图校验：默认只做自洽性核对（下发版本在 maps_data/<site>/ 有文件、能解析、sha/大小与元数据一致）；
#           仅当显式传入 -ExpectedMapVersion 时，才把"不等于该版本"判为 FAIL。
# 退出码：0=正常或有 WARN；1=地图校验 FAIL（真故障）；其余检查只打印不改变退出码。
# 编码：必须保存为 UTF-8 with BOM。

param([string]$ExpectedMapVersion = '')

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
function ConvertTo-VersionKey([string]$version) {
    # "2026-11-08.10" → @(2026,11,8,10)：按点/连字符拆段后逐段转数字，
    # 避免字符串比较把 "2026-11-08.10" 排在 "2026-11-08.2" 之前。
    if ([string]::IsNullOrWhiteSpace($version)) { return $null }
    $nums = @()
    foreach ($seg in ($version.Trim() -split '[.\-]')) {
        $n = 0
        if (-not [int]::TryParse($seg, [ref]$n)) { return $null }
        $nums += $n
    }
    return , $nums
}
function Compare-Version([string]$a, [string]$b) {
    $ka = ConvertTo-VersionKey $a
    $kb = ConvertTo-VersionKey $b
    if ($null -eq $ka -or $null -eq $kb) { return [Math]::Sign([string]::CompareOrdinal($a, $b)) }
    $len = [Math]::Max($ka.Count, $kb.Count)
    for ($i = 0; $i -lt $len; $i++) {
        $x = if ($i -lt $ka.Count) { $ka[$i] } else { 0 }
        $y = if ($i -lt $kb.Count) { $kb[$i] } else { 0 }
        if ($x -ne $y) { return [Math]::Sign($x - $y) }
    }
    return 0
}
function Get-LatestVersion($versions) {
    $best = $null
    foreach ($v in @($versions)) {
        if ($null -eq $best) { $best = [string]$v; continue }
        if ((Compare-Version ([string]$v) $best) -gt 0) { $best = [string]$v }
    }
    return $best
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

# 地图版本自洽性校验（不再与任何写死版本比较）：
#   FAIL = 下发版本在 maps_data/<site>/ 无文件 / 文件解析失败 / sha 或大小与元数据不符 / 与显式 -ExpectedMapVersion 不符
#   WARN = 下发版本 ≠ 目录最新版本（例如只更新台账未发图、回滚过），只提示人工确认，不算失败
$mapVerdict = 'SKIP'
$mapSiteId = 'school-building-01'
$mapDir = if ($env:PATHAI_MAP_DATA_DIR) { $env:PATHAI_MAP_DATA_DIR } else { Join-Path (Split-Path $here -Parent) 'maps_data' }

# 假登录取 token 后查询（PATHAI_WECHAT_FAKE=1 生效说明环境变量注入正常）
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
        $map = Invoke-RestMethod -Uri ("http://127.0.0.1:8000/api/v1/sites/{0}/map?version=latest" -f $mapSiteId) `
            -Headers @{ Authorization = "Bearer $token" } -TimeoutSec 10
        $served = [string]$map.mapVersion
        $servedSha = ([string]$map.dataSha256).ToLowerInvariant()

        $siteDir = Join-Path $mapDir $mapSiteId
        $localVersions = @()
        if (Test-Path $siteDir) {
            $localVersions = @(Get-ChildItem $siteDir -Filter '*.json' -File | ForEach-Object { $_.BaseName })
        }
        $latest = Get-LatestVersion $localVersions
        $expected = if ($ExpectedMapVersion) { $ExpectedMapVersion } else { $latest }

        # 三值摘要：served=服务端实际下发；latest=目录最新；expected=显式期望或目录最新
        Write-Host ("地图版本摘要：served={0}  latest={1}  expected={2}" -f `
            $(if ($served) { $served } else { '—' }), `
            $(if ($latest) { $latest } else { '<目录无文件>' }), `
            $(if ($expected) { $expected } else { '—' })) -ForegroundColor Cyan
        Write-Host ("地图目录：{0}（{1} 个版本）" -f $siteDir, @($localVersions).Count) -ForegroundColor DarkGray

        $problems = @()
        if ([string]::IsNullOrWhiteSpace($served)) {
            $problems += '服务端未返回 mapVersion'
        } else {
            $file = Join-Path $siteDir ($served + '.json')
            if (-not (Test-Path $file)) {
                $problems += ("下发版本 {0} 在 {1} 中找不到对应文件" -f $served, $siteDir)
            } else {
                $localSize = (Get-Item $file).Length
                $hash = (Get-FileHash -Path $file -Algorithm SHA256).Hash.ToLowerInvariant()
                if ($hash -ne $servedSha) {
                    $problems += ("sha256 与元数据不符：文件 {0} / 服务端 {1}" -f $hash, $servedSha)
                    # 内容对不上时再分辨：是文件坏了，还是只是版本内容与台账不一致
                    try { Get-Content $file -Raw -Encoding UTF8 | ConvertFrom-Json | Out-Null }
                    catch { $problems += ("地图包解析失败：{0}" -f $_.Exception.Message) }
                }
                # 大小核对：Range 只取 1 字节，从 Content-Range 读服务端真实总大小，避免整包下载
                $servedSize = $null
                try {
                    $probe = Invoke-WebRequest -Uri ("http://127.0.0.1:8000/api/v1/sites/{0}/map/data?version={1}" -f $mapSiteId, $served) `
                        -Headers @{ Authorization = "Bearer $token"; Range = 'bytes=0-0' } -UseBasicParsing -TimeoutSec 15
                    if ($probe.StatusCode -eq 206) {
                        $cr = [string]($probe.Headers['Content-Range'])
                        if ($cr -match '/(\d+)\s*$') { $servedSize = [int64]$Matches[1] }
                    } elseif ($probe.StatusCode -eq 200) {
                        $servedSize = [int64]$probe.RawContentLength
                    }
                } catch { }
                if ($null -ne $servedSize -and $servedSize -ne $localSize) {
                    $problems += ("大小与元数据不符：文件 {0} 字节 / 服务端 {1} 字节" -f $localSize, $servedSize)
                }
            }
        }
        if ($ExpectedMapVersion -and (Compare-Version $served $ExpectedMapVersion) -ne 0) {
            $problems += ("与 -ExpectedMapVersion={0} 不一致（实际下发 {1}）" -f $ExpectedMapVersion, $served)
        }

        if (@($problems).Count -gt 0) {
            $mapVerdict = 'FAIL'
            foreach ($p in $problems) { Write-Host ('[FAIL] ' + $p) -ForegroundColor Red }
        } elseif (-not $ExpectedMapVersion -and $null -ne $latest -and (Compare-Version $served $latest) -ne 0) {
            $mapVerdict = 'WARN'
            Write-Host ("[WARN] 下发版本 {0} 不是目录最新版本 {1}；served={0} / latest={1} / expected={2}。" -f $served, $latest, $expected) -ForegroundColor Yellow
            Write-Host '       常见原因：只更新了台账未发布图包、回滚过、或运行进程未加载新文件（需重启后端）。' -ForegroundColor DarkGray
        } else {
            $mapVerdict = 'OK'
            Write-Host ("[OK] 下发版本 {0} 与目录一致（served=latest={1}），sha/大小校验通过。" -f $served, $latest) -ForegroundColor Green
        }
    } catch {
        $mapVerdict = 'FAIL'
        Write-Host ("[FAIL] 地图接口失败：{0}" -f $_.Exception.Message) -ForegroundColor Red
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

switch ($mapVerdict) {
    'OK' { Write-Host '地图版本校验：OK（下发版本与目录自洽）。' -ForegroundColor Green }
    'WARN' { Write-Host '地图版本校验：WARN（served/latest/expected 有差异，需人工确认；不影响后端运行判定）。' -ForegroundColor Yellow }
    'FAIL' { Write-Host '地图版本校验：FAIL（见上方 [FAIL] 项，属真故障）。' -ForegroundColor Red }
    default { Write-Host '地图版本校验：SKIP（假登录失败，未能查询下发版本）。' -ForegroundColor DarkGray }
}
if ($mapVerdict -eq 'FAIL') { exit 1 }

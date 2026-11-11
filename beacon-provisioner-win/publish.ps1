param(
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$repoRoot = Split-Path -Parent $projectRoot
$outDir = Join-Path $repoRoot "dist\BeaconProvisioner"

# Stop the tool if it is running from this repo, so the exe is not locked during publish.
# Only processes with a matching name and an executable path inside the repo are stopped.
$running = @(Get-Process -Name "BeaconProvisioner" -ErrorAction SilentlyContinue)
$toStop = @()
foreach ($proc in $running) {
    $procPath = $null
    try { $procPath = $proc.Path } catch { }
    if (-not $procPath) {
        Write-Host "Skip PID $($proc.Id): executable path unavailable"
        continue
    }
    $fullPath = [System.IO.Path]::GetFullPath($procPath)
    $repoPrefix = $repoRoot.TrimEnd('\') + [System.IO.Path]::DirectorySeparatorChar
    if ($fullPath.StartsWith($repoPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        $toStop += $proc
    } else {
        Write-Host "Skip PID $($proc.Id): $fullPath (outside repo)"
    }
}

if ($toStop.Count -eq 0) {
    Write-Host "No running BeaconProvisioner to stop."
} else {
    foreach ($proc in $toStop) {
        Write-Host "Stopping PID $($proc.Id): $($proc.Path)"
        try {
            Stop-Process -Id $proc.Id -Force -ErrorAction Stop
        } catch {
            Write-Host "Failed to stop PID $($proc.Id): $($_.Exception.Message)"
        }
    }
    Start-Sleep -Milliseconds 500
    $stillRunning = @(Get-Process -Id $toStop.Id -ErrorAction SilentlyContinue)
    if ($stillRunning.Count -gt 0) {
        $detail = ($stillRunning | ForEach-Object { "PID $($_.Id)" }) -join ", "
        Write-Error "Cannot stop BeaconProvisioner process(es): $detail. Stop them manually and retry."
        exit 1
    }
    Write-Host "Stopped $($toStop.Count) process(es)."
}

$dotnet = (Get-Command dotnet -ErrorAction SilentlyContinue).Source
if (-not $dotnet) {
    $candidate = Join-Path $env:USERPROFILE ".dotnet\dotnet.exe"
    if (Test-Path $candidate) { $dotnet = $candidate }
}
if (-not $dotnet) {
    throw "dotnet SDK not found. Install .NET 8 SDK or set PATH."
}

& $dotnet publish (Join-Path $projectRoot "src\BeaconProvisioner\BeaconProvisioner.csproj") `
    -c $Configuration -r win-x64 --self-contained true `
    -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true `
    -p:EnableCompressionInSingleFile=true -o $outDir
if ($LASTEXITCODE -ne 0) {
    throw "publish failed (exit $LASTEXITCODE)"
}

$exe = Join-Path $outDir "BeaconProvisioner.exe"
if (-not (Test-Path $exe)) {
    throw "publish failed: $exe not found"
}

$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "BeaconProvisioner.lnk"
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($lnkPath)
$lnk.TargetPath = $exe
$lnk.WorkingDirectory = $outDir
$lnk.IconLocation = "$exe,0"
$lnk.Description = "Minew E5 beacon provisioning tool (portable)"
$lnk.Save()

Write-Host "Published : $exe"
Write-Host "Shortcut  : $lnkPath"

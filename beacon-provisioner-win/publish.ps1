param(
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$repoRoot = Split-Path -Parent $projectRoot
$outDir = Join-Path $repoRoot "dist\BeaconProvisioner"

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
    throw "publish failed (exit $LASTEXITCODE); close the running BeaconProvisioner.exe and retry"
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

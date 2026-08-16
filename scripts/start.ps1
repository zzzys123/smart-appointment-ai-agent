param(
    [switch]$Reload,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$frontendRoot = Join-Path $projectRoot "frontend"
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$distIndex = Join-Path $frontendRoot "dist\index.html"

Set-Location $projectRoot

if (-not (Test-Path $pythonExe)) {
    throw ".venv was not found. Create it and install requirements.txt first."
}

if (-not $SkipBuild) {
    if (-not (Test-Path (Join-Path $frontendRoot "node_modules"))) {
        Write-Host "[1/3] Installing frontend dependencies..." -ForegroundColor Cyan
        Push-Location $frontendRoot
        try { & npm.cmd install } finally { Pop-Location }
    }

    $sourceFiles = Get-ChildItem (Join-Path $frontendRoot "src") -Recurse -File
    $configFiles = Get-ChildItem $frontendRoot -File | Where-Object {
        $_.Name -in @("package.json", "package-lock.json", "vite.config.ts", "index.html")
    }
    $latestSource = ($sourceFiles + $configFiles | Measure-Object LastWriteTime -Maximum).Maximum
    $needsBuild = -not (Test-Path $distIndex)
    if (-not $needsBuild) {
        $needsBuild = $latestSource -gt (Get-Item $distIndex).LastWriteTime
    }

    if ($needsBuild) {
        Write-Host "[2/3] Building the React frontend..." -ForegroundColor Cyan
        Push-Location $frontendRoot
        try { & npm.cmd run build } finally { Pop-Location }
    } else {
        Write-Host "[2/3] Frontend build is current; skipping." -ForegroundColor DarkGray
    }
}

Write-Host "[3/3] Starting Smart Appointment AI..." -ForegroundColor Green
Write-Host "Open http://127.0.0.1:8000" -ForegroundColor Green

$uvicornArgs = @("-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8000")
if ($Reload) { $uvicornArgs += "--reload" }
& $pythonExe @uvicornArgs

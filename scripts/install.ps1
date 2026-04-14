# ============================================================
#  WeChat Bridge - Windows Installer
#  Usage: powershell -c "irm https://raw.githubusercontent.com/yuuouu/WeChat-Bridge/main/scripts/install.ps1 | iex"
# ============================================================

$ErrorActionPreference = "Continue"
$REPO = "yuuouu/WeChat-Bridge"
if ($env:WECHAT_BRIDGE_DIR) { $INSTALL_DIR = $env:WECHAT_BRIDGE_DIR } else { $INSTALL_DIR = Join-Path (Get-Location) "wechat-bridge" }
if ($env:WECHAT_BRIDGE_PORT) { $PORT = $env:WECHAT_BRIDGE_PORT } else { $PORT = "5200" }

function Write-Info  { param($msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "  [!] $msg" -ForegroundColor Yellow }
function Write-Err   { param($msg) Write-Host "  [X] $msg" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "  WeChat Bridge Installer" -ForegroundColor Cyan
Write-Host "  ================================" -ForegroundColor Cyan
Write-Host ""

# ── 1. Check Python (min 3.10) ──
$pythonCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd --version 2>&1 | Out-String
        if ($ver -match "Python 3\.(\d+)") {
            $minor = [int]$Matches[1]
            if ($minor -ge 10) {
                $pythonCmd = $cmd
                break
            }
        }
    } catch {}
}

if (-not $pythonCmd) {
    Write-Warn "Python 3.10+ not found, trying winget..."
    $wingetAvailable = $null -ne (Get-Command winget -ErrorAction SilentlyContinue)
    if ($wingetAvailable) {
        $result = Start-Process -FilePath "winget" -ArgumentList "install Python.Python.3.12 --accept-package-agreements --accept-source-agreements --silent" -Wait -PassThru -NoNewWindow
        if ($result.ExitCode -eq 0) {
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
            $pythonCmd = "python"
            Write-Info "Python installed via winget"
        }
        else {
            Write-Err ("Python install failed (exit code: " + $result.ExitCode + ").`nPlease install manually: https://www.python.org/downloads/`nTip: check 'Add Python to PATH' during install")
        }
    }
    else {
        Write-Err "No winget and no Python found. Please install Python 3.10+: https://www.python.org/downloads/"
    }
}

$pyVer = & $pythonCmd --version 2>&1 | Out-String
Write-Info ("Python: " + $pyVer.Trim())

# ── 2. Download code ──
$hasGit = $null -ne (Get-Command git -ErrorAction SilentlyContinue)

if ($hasGit) {
    $isGitRepo = (Test-Path $INSTALL_DIR) -and (Test-Path (Join-Path $INSTALL_DIR ".git"))
    if ($isGitRepo) {
        Write-Warn "Directory exists, updating..."
        Push-Location $INSTALL_DIR
        $pullResult = & git pull --ff-only 2>&1 | Out-String
        if ($pullResult -match "Already up to date") {
            Write-Info "Code is up to date"
        }
        elseif ($pullResult -match "Updating") {
            Write-Info "Code updated"
        }
        else {
            Write-Warn ("git pull: " + $pullResult.Trim())
        }
        Pop-Location
    }
    elseif (Test-Path $INSTALL_DIR) {
        Write-Warn "Directory exists but not a git repo, re-cloning..."
        Remove-Item $INSTALL_DIR -Recurse -Force
        & git clone --depth 1 ("https://github.com/" + $REPO + ".git") $INSTALL_DIR 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    }
    else {
        Write-Info "Cloning repository..."
        & git clone --depth 1 ("https://github.com/" + $REPO + ".git") $INSTALL_DIR 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    }
}
else {
    Write-Info "Downloading source (no git)..."
    New-Item -ItemType Directory -Force -Path $INSTALL_DIR | Out-Null
    $zipUrl = "https://github.com/" + $REPO + "/archive/refs/heads/main.zip"
    $zipFile = Join-Path $env:TEMP "wechat-bridge.zip"
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipFile -UseBasicParsing
    Expand-Archive -Path $zipFile -DestinationPath $env:TEMP -Force
    $extracted = Join-Path $env:TEMP "WeChat-Bridge-main"
    Get-ChildItem -Path $extracted | Copy-Item -Destination $INSTALL_DIR -Recurse -Force
    Remove-Item $zipFile, $extracted -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Info ("Install directory: " + $INSTALL_DIR)

# ── 3. Install dependencies ──
Push-Location $INSTALL_DIR
New-Item -ItemType Directory -Force -Path "data" | Out-Null

Write-Host "  [..] Installing Python dependencies..." -ForegroundColor DarkGray -NoNewline
$pipOutput = & $pythonCmd -m pip install -q -r app/requirements.txt 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Err ("Dependency install failed:`n" + $pipOutput)
}
else {
    Write-Host ("`r  [OK] Python dependencies installed          ") -ForegroundColor Green
}

Pop-Location

# ── 4. Done ──
Write-Host ""
Write-Host "  ================================" -ForegroundColor Green
Write-Host "  WeChat Bridge installed!" -ForegroundColor Green
Write-Host "  ================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Start:    " -NoNewline
Write-Host ("cd " + $INSTALL_DIR + " ; .\scripts\start.bat") -ForegroundColor Cyan
Write-Host "  Web UI:   " -NoNewline
Write-Host ("http://localhost:" + $PORT) -ForegroundColor Cyan
Write-Host ""
Write-Host "  Optional env vars:" -ForegroundColor Yellow
Write-Host "    API_TOKEN     - API auth token"
Write-Host "    WEBHOOK_URL   - Inbound message webhook URL"
Write-Host ""

# ── 5. Ask to start ──
$start = Read-Host "  Start now? [Y/n]"
if (($start -ne "n") -and ($start -ne "N")) {
    Set-Location $INSTALL_DIR

    # 查找 pythonw（无窗口版本）用于后台运行
    $pythonwCmd = Get-Command pythonw -ErrorAction SilentlyContinue
    $mainScript = Join-Path $INSTALL_DIR "app\main.py"

    if ($pythonwCmd) {
        Start-Process -FilePath $pythonwCmd.Source -ArgumentList $mainScript -WorkingDirectory $INSTALL_DIR
    }
    else {
        Start-Process -FilePath $pythonCmd -ArgumentList $mainScript -WorkingDirectory $INSTALL_DIR -WindowStyle Hidden
    }

    Write-Info "Service started in background"
    Write-Host "  Logs:     " -NoNewline
    Write-Host (Join-Path $INSTALL_DIR "data\run.log") -ForegroundColor Cyan
    Write-Host "  Stop:     " -NoNewline
    Write-Host (Join-Path $INSTALL_DIR "scripts\stop.bat") -ForegroundColor Cyan
    Write-Host ""

    # 等服务启动后打开浏览器
    Start-Sleep -Seconds 2
    Start-Process ("http://localhost:" + $PORT)
    Write-Host "  Browser opened. Enjoy!" -ForegroundColor Green
    Write-Host ""
}


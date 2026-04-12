# ============================================================
#  WeChat Bridge — Windows 一键安装脚本
#  用法: powershell -c "irm https://raw.githubusercontent.com/yuuouu/WeChat-Bridge/main/install.ps1 | iex"
# ============================================================

$ErrorActionPreference = "Continue"
$REPO = "yuuouu/WeChat-Bridge"
$INSTALL_DIR = if ($env:WECHAT_BRIDGE_DIR) { $env:WECHAT_BRIDGE_DIR } else { Join-Path (Get-Location) "wechat-bridge" }
$PORT = if ($env:WECHAT_BRIDGE_PORT) { $env:WECHAT_BRIDGE_PORT } else { "5200" }

function Write-Info  { param($msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "  [!] $msg" -ForegroundColor Yellow }
function Write-Err   { param($msg) Write-Host "  [X] $msg" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "  WeChat Bridge Installer" -ForegroundColor Cyan
Write-Host "  ================================" -ForegroundColor Cyan
Write-Host ""

# ── 1. 检查 Python (最低 3.10) ──
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
    Write-Warn "未检测到 Python 3.10+，正在尝试通过 winget 安装..."
    $wingetAvailable = $null -ne (Get-Command winget -ErrorAction SilentlyContinue)
    if ($wingetAvailable) {
        $result = Start-Process -FilePath "winget" -ArgumentList "install Python.Python.3.12 --accept-package-agreements --accept-source-agreements --silent" -Wait -PassThru -NoNewWindow
        if ($result.ExitCode -eq 0) {
            # 刷新 PATH
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
            $pythonCmd = "python"
            Write-Info "Python 已自动安装"
        } else {
            Write-Err "Python 安装失败 (退出码: $($result.ExitCode))。`n  请手动安装: https://www.python.org/downloads/`n  提示: 安装时务必勾选 'Add Python to PATH'"
        }
    } else {
        Write-Err "未找到 winget 且无 Python。请手动安装 Python 3.10+: https://www.python.org/downloads/"
    }
}

$pyVer = & $pythonCmd --version 2>&1 | Out-String
Write-Info "Python: $($pyVer.Trim())"

# ── 2. 下载代码 ──
$hasGit = $null -ne (Get-Command git -ErrorAction SilentlyContinue)

if ($hasGit) {
    if (Test-Path $INSTALL_DIR) {
        Write-Warn "目录已存在，正在更新..."
        Push-Location $INSTALL_DIR
        & git pull --ff-only 2>&1 | Out-Null
        Pop-Location
    } else {
        Write-Info "正在克隆仓库..."
        & git clone --depth 1 "https://github.com/$REPO.git" $INSTALL_DIR 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    }
} else {
    Write-Info "正在下载源码..."
    New-Item -ItemType Directory -Force -Path $INSTALL_DIR | Out-Null
    $zipUrl = "https://github.com/$REPO/archive/refs/heads/main.zip"
    $zipFile = Join-Path $env:TEMP "wechat-bridge.zip"
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipFile -UseBasicParsing
    Expand-Archive -Path $zipFile -DestinationPath $env:TEMP -Force
    $extracted = Join-Path $env:TEMP "WeChat-Bridge-main"
    Get-ChildItem -Path $extracted | Copy-Item -Destination $INSTALL_DIR -Recurse -Force
    Remove-Item $zipFile, $extracted -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Info "安装目录: $INSTALL_DIR"

# ── 3. 安装依赖 ──
Push-Location $INSTALL_DIR
New-Item -ItemType Directory -Force -Path "data" | Out-Null

Write-Host "  [..] 正在安装 Python 依赖..." -ForegroundColor DarkGray -NoNewline
# pip 的 warning 走 stderr，用 2>&1 统一捕获避免 PowerShell 报红
$pipOutput = & $pythonCmd -m pip install -q -r app/requirements.txt 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Err "依赖安装失败:`n$pipOutput"
} else {
    Write-Host "`r  [OK] Python 依赖已安装          " -ForegroundColor Green
}

# ── 4. 检查端口 ──
$portInUse = Get-NetTCPConnection -LocalPort $PORT -ErrorAction SilentlyContinue
if ($portInUse) {
    Write-Warn "端口 $PORT 已被占用，启动后可能失败"
}

Pop-Location

# ── 5. 完成 ──
Write-Host ""
Write-Host "  ================================" -ForegroundColor Green
Write-Host "  WeChat Bridge installed!" -ForegroundColor Green
Write-Host "  ================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Start:    " -NoNewline; Write-Host "cd $INSTALL_DIR && .\start.bat" -ForegroundColor Cyan
Write-Host "  Web UI:   " -NoNewline; Write-Host "http://localhost:$PORT" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Optional env vars:" -ForegroundColor Yellow
Write-Host "    API_TOKEN     - API auth token"
Write-Host "    WEBHOOK_URL   - Inbound message webhook URL"
Write-Host ""

# ── 6. 询问是否立即启动 ──
$start = Read-Host "  Start now? [Y/n]"
if ($start -ne "n" -and $start -ne "N") {
    Set-Location $INSTALL_DIR
    & $pythonCmd app/main.py
}

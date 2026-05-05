# ============================================================
#  WeChat Bridge - Uninstaller (Windows)
#  Usage: powershell -ExecutionPolicy Bypass -File wechat-bridge\scripts\uninstall.ps1
#
#  默认不删除 data\ 目录（含消息数据库和配置）。
#  如需彻底清除，卸载完成后手动执行：
#    Remove-Item -Recurse -Force <安装目录>\data
# ============================================================

$ErrorActionPreference = "Continue"

function Write-Info  { param($msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "  [!] $msg" -ForegroundColor Yellow }

# 定位安装目录
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$INSTALL_DIR = Split-Path -Parent $SCRIPT_DIR

Write-Host ""
Write-Host "  WeChat Bridge Uninstaller" -ForegroundColor Cyan
Write-Host "  ================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Install directory: $INSTALL_DIR"
Write-Host ""

# ── 1. 停止服务 ──
$stopScript = Join-Path $INSTALL_DIR "scripts\stop.bat"
if (Test-Path $stopScript) {
    Write-Info "Stopping service..."
    & $stopScript 2>$null
    Start-Sleep -Seconds 1
}

# 也尝试直接杀进程
$procs = Get-Process -ErrorAction SilentlyContinue | Where-Object {
    try { $_.MainModule.FileName -like "*wechat-bridge*" } catch { $false }
}
if ($procs) {
    Write-Info "Stopping running processes..."
    $procs | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}

# ── 2. 保留 data 目录 ──
$DATA_DIR = Join-Path $INSTALL_DIR "data"
$BACKUP_DATA = ""
if (Test-Path $DATA_DIR) {
    $BACKUP_DATA = Join-Path (Split-Path $INSTALL_DIR -Parent) "wechat-bridge-data-backup"
    Write-Warn "Preserving data directory: data\ -> $BACKUP_DATA"
    if (Test-Path $BACKUP_DATA) { Remove-Item -Recurse -Force $BACKUP_DATA }
    Move-Item -Path $DATA_DIR -Destination $BACKUP_DATA
}

# ── 3. 删除安装目录 ──
Write-Info "Removing install directory: $INSTALL_DIR"
# 先退出安装目录以防锁定
Set-Location (Split-Path $INSTALL_DIR -Parent)
Remove-Item -Recurse -Force $INSTALL_DIR -ErrorAction SilentlyContinue

# ── 4. 恢复 data 到原位 ──
if ($BACKUP_DATA -and (Test-Path $BACKUP_DATA)) {
    New-Item -ItemType Directory -Force -Path $INSTALL_DIR | Out-Null
    Move-Item -Path $BACKUP_DATA -Destination $DATA_DIR
    Write-Info "Data directory preserved at: $DATA_DIR"
}

Write-Host ""
Write-Host "  ================================" -ForegroundColor Green
Write-Host "  WeChat Bridge uninstalled!" -ForegroundColor Green
Write-Host "  ================================" -ForegroundColor Green
Write-Host ""
if (Test-Path $DATA_DIR) {
    Write-Host "  Data directory NOT deleted: $DATA_DIR" -ForegroundColor Yellow
    Write-Host "  To fully remove: Remove-Item -Recurse -Force $INSTALL_DIR" -ForegroundColor Cyan
    Write-Host ""
}

@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo PuriPuly Heart QQ Auth Beta 认证诊断
echo 1. 离线自检（默认，不向生产服务申请密钥）
echo 2. 检查本 Beta 已有凭据（不联网，不重新领取）
echo 3. 使用 App 同链路单次认证（需输入现成凭证和明确确认）
echo 4. 恢复本 Beta 已有操作（不创建新申请）
echo 5. 检查已有凭据并执行一次只读用量查询（联网，不申请密钥）
set "mode=offline"
set "extra="
set /p "choice=请选择 [1]："
if "%choice%"=="2" set "mode=inspect"
if "%choice%"=="3" set "mode=app"
if "%choice%"=="4" set "mode=recover"
if "%choice%"=="5" set "mode=inspect"
if "%choice%"=="5" set "extra=--refresh-usage"
"%~dp0runtime\python.exe" -B "%~dp0app\qq_auth_entry.py" probe --mode %mode% %extra%
echo 只回传生成的脱敏 JSON 或 TXT，不要发送 settings.json、密钥库或运行目录。
pause

@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 优先 py launcher（只装 Python 官方安装器的机器没有 python 命令），回退 python
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 main.py %*
) else (
    python main.py %*
)
if errorlevel 1 pause

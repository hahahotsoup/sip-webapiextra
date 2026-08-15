@echo off
rem sip-web 启动脚本（Windows）
rem 用法：把本文件连同 sip-web.py、index.html 放到 sip.exe 所在文件夹，双击运行。
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel%==0 (
    python sip-web.py %*
) else (
    where py >nul 2>nul
    if %errorlevel%==0 (
        py sip-web.py %*
    ) else (
        echo [错误] 未找到 Python，请先安装 Python 3.10+ 并加入 PATH。
        pause
        exit /b 1
    )
)

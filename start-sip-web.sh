#!/usr/bin/env sh
# sip-web 启动脚本（macOS / Linux）
# 用法：把本文件连同 sip-web.py、index.html 放到 sip 可执行文件所在文件夹，然后：
#   chmod +x start-sip-web.sh && ./start-sip-web.sh
cd "$(dirname "$0")" || exit 1
exec python3 sip-web.py "$@"

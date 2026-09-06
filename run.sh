#!/usr/bin/env bash
# macOS/Linux 启动脚本（对应 Windows 的 run.bat）
cd "$(dirname "$0")" || exit 1

# 项目内 .venv 优先
if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
else
    PY="python3"
fi

"$PY" main.py "$@"
code=$?
if [ "$code" -ne 0 ]; then
    echo "程序异常退出（退出码 $code），按回车键关闭窗口..."
    read -r
fi

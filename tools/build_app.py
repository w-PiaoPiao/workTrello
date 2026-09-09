"""打包入口（Python 侧执行 PyInstaller）

从 app/config.py 读取版本号生成中文包名。打包逻辑放 Python 侧，
避免 Windows PowerShell 传中文参数给 pyinstaller 时的编码乱码
（曾把 "桌宠看板" 变成 "妗屽疇鐪嬫澘" 文件名）。

用法：python tools/build_app.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    # 控制台可能不是 UTF-8（如 GitHub Actions 的 cp1252）：中文包名/日志
    # 打印会 UnicodeEncodeError，先把 stdout/stderr 切成 UTF-8
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass
    cfg = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    match = re.search(r'APP_VERSION = "([^"]+)"', cfg)
    if not match:
        print("未能从 app/config.py 读取 APP_VERSION", file=sys.stderr)
        return 1
    name = f"peTTrello-v{match.group(1)}"
    print(f"打包: {name}")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile", "--windowed",
        "--name", name,
        "--exclude-module", "PySide6.QtWebEngineCore",
        "--exclude-module", "PySide6.QtWebEngineWidgets",
        "--exclude-module", "PySide6.QtQml",
        "--exclude-module", "PySide6.QtQuick",
        "--exclude-module", "PySide6.QtMultimedia",
        "--exclude-module", "PySide6.Qt3DCore",
        "--exclude-module", "PySide6.QtNetwork",
        "--clean", "--noconfirm",
        str(ROOT / "main.py"),
    ]
    subprocess.run(cmd, cwd=ROOT, check=True)
    print(f"完成: dist\\{name}.exe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

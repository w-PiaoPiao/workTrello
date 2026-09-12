"""打包入口（Python 侧执行 PyInstaller）

从 app/config.py 读取版本号生成包名。打包逻辑放 Python 侧，
避免 Windows PowerShell 传中文参数给 pyinstaller 时的编码乱码
（曾把 "桌宠看板" 变成 "妗屽疇鐪嬫澘" 文件名）。

onefile 产物是单个 exe，无法打包后清理内部文件，所以这里动态
生成 spec，在 Analysis 结果上过滤未使用的 Qt 组件后再打出 onefile
（--exclude-module 只挡 Python 绑定，Qt 的 C++ dll 仍会被 PySide6
hook 全量收集；应用只用 QtCore/QtGui/QtWidgets）。

用法：python tools/build_app.py
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# dist_name 里路径分隔符统一按 / 匹配（已小写）。命中即从产物剔除：
# Qt6* 为 C++ dll（PySide6/Qt/bin），PySide6/Qt* 为绑定扩展，
# imageformats/iconengines/platforminputcontexts 为按需 dlopen 的插件，
# pyside6/qt/qml 为 QML 模块目录。若 Windows 上启动异常，
# 把对应条目从这里移除即可。
DEAD_PATTERNS = (
    "qt6pdf",
    "qt6qml",
    "qt6quick",
    "qt6network",
    "qt6dbus",
    "qt6opengl",
    "qt6svg",
    "qt6virtualkeyboard",
    "pyside6/qtdbus",
    "pyside6/qtnetwork",
    "pyside6/qtqml",
    "pyside6/qtquick",
    "pyside6/qtpdf",
    "pyside6/qtopengl",
    "pyside6/qtsvg",
    "pyside6/qtvirtualkeyboard",
    "imageformats/libqpdf",
    "imageformats/libqsvg",
    "imageformats/libqtiff",
    "imageformats/libqwbmp",
    "imageformats/libqtga",
    "iconengines/",
    "platforminputcontexts/qtvirtualkeyboard",
    "pyside6/qt/qml",
)

# Qt 自带翻译只留中英兜底标准对话框文案（应用自带中英 i18n）
KEEP_TRANSLATIONS = ("qtbase_zh_cn.qm", "qtbase_en.qm")

SPEC_TEMPLATE = '''# -*- mode: python ; coding: utf-8 -*-
# 由 tools/build_app.py 自动生成，勿手改（Windows onefile + Qt 死重过滤）

DEAD_PATTERNS = {dead!r}
KEEP_TRANSLATIONS = {keep!r}


def _is_dead(dest_name):
    n = dest_name.replace("\\\\", "/").lower()
    for p in DEAD_PATTERNS:
        if p in n:
            return True
    if "pyside6/qt/translations/" in n and not n.endswith(KEEP_TRANSLATIONS):
        return True
    return False


a = Analysis(
    [{entry!r}],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
        'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtMultimedia',
        'PySide6.Qt3DCore', 'PySide6.QtNetwork',
    ],
    noarchive=False,
    optimize=0,
)

# PyInstaller 6 起 TOC 已移除，a.binaries / a.datas 是普通 list
a.binaries = [x for x in a.binaries if not _is_dead(x[0])]
a.datas = [x for x in a.datas if not _is_dead(x[0])]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name={name!r},
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon={icon!r},
)
'''


def _read_app_version() -> str:
    cfg = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    match = re.search(r'APP_VERSION = "([^"]+)"', cfg)
    if not match:
        raise SystemExit("未能从 app/config.py 读取 APP_VERSION")
    return match.group(1)


def main() -> int:
    # 控制台可能不是 UTF-8（如 GitHub Actions 的 cp1252）：中文包名/日志
    # 打印会 UnicodeEncodeError，先把 stdout/stderr 切成 UTF-8
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass

    name = f"peTTrello-v{_read_app_version()}"
    print(f"打包: {name}")

    spec = SPEC_TEMPLATE.format(
        dead=DEAD_PATTERNS,
        keep=KEEP_TRANSLATIONS,
        entry=str(ROOT / "main.py"),
        name=name,
        icon=str(ROOT / "assets" / "app.ico"),
    )
    spec_path = ROOT / "build_app.generated.spec"
    spec_path.write_text(spec, encoding="utf-8")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--clean", "--noconfirm",
        str(spec_path),
    ]
    try:
        subprocess.run(cmd, cwd=ROOT, check=True)
    finally:
        spec_path.unlink(missing_ok=True)

    print(f"完成: dist\\{name}.exe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

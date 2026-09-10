"""生成应用图标资源：assets/app.ico（Windows）、assets/app.icns（macOS）、
assets/icon_256.png（通用）

与 app/services/app_icon.py 共用同一份「桌宠探头」矢量绘制，
保证运行时图标与打包资源一致。ICO/ICNS 容器为纯 Python 写入，
不依赖 iconutil，Windows 上即可产出全部资源。

用法：python tools/make_icons.py
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.services.app_icon import render_pixmap  # noqa: E402

_qapp = QApplication.instance() or QApplication([])

ASSETS = ROOT / "assets"


def png_bytes(size: int) -> bytes:
    pm = render_pixmap(size)
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    pm.save(buf, "PNG")
    return bytes(buf.data())


def write_ico(path: Path, sizes: tuple[int, ...]) -> None:
    """多尺寸 ICO（Vista+ 的 PNG 压缩条目）"""
    images = [(s, png_bytes(s)) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blob = b"", b""
    for s, data in images:
        dim = 0 if s >= 256 else s
        entries += struct.pack(
            "<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset
        )
        blob += data
        offset += len(data)
    path.write_bytes(header + entries + blob)


# macOS icns 条目：类型 → 实际像素尺寸（@2x 语义见 Apple 文档）
ICNS_TYPES = {
    "icp4": 16,   # 16x16
    "icp5": 32,   # 32x32
    "ic07": 128,
    "ic08": 256,
    "ic09": 512,
    "ic10": 1024,  # 512@2x
    "ic11": 32,   # 16@2x
    "ic12": 64,   # 32@2x
    "ic13": 256,  # 128@2x
    "ic14": 512,  # 256@2x
}


def write_icns(path: Path) -> None:
    chunks = b""
    for ostype, size in ICNS_TYPES.items():
        data = png_bytes(size)
        chunks += ostype.encode("ascii") + struct.pack(">I", 8 + len(data)) + data
    path.write_bytes(b"icns" + struct.pack(">I", 8 + len(chunks)) + chunks)


def main() -> int:
    ASSETS.mkdir(exist_ok=True)
    write_ico(ASSETS / "app.ico", (16, 24, 32, 48, 64, 128, 256))
    write_icns(ASSETS / "app.icns")
    (ASSETS / "icon_256.png").write_bytes(png_bytes(256))
    for f in ("app.ico", "app.icns", "icon_256.png"):
        p = ASSETS / f
        print(f"{p.name}: {p.stat().st_size / 1024:.0f} KB")
    print("完成: assets/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

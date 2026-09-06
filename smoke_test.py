# -*- coding: utf-8 -*-
"""冒烟测试：离屏渲染双模式 UI 并截图（数据目录隔离到 ./data）"""

import os
import sys
import traceback
from pathlib import Path

os.environ["PET_BOARD_DATA_DIR"] = str(Path(__file__).parent / "data")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
elif sys.platform == "darwin":
    os.environ.setdefault("QT_QPA_FONTDIR", "/System/Library/Fonts")

sys.path.insert(0, str(Path(__file__).parent))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

app = QApplication(sys.argv)
app.setApplicationName("桌宠看板")
app.setQuitOnLastWindowClosed(False)

from app.config import AppConfig
from app.controllers.app_controller import AppController
from app.views.theme import AppTheme

controller = AppController()
window = controller.window()

# 注入演示数据（若看板为空）
board = controller._store.load()
if board.total_cards() == 0:
    from app.models.board import Card
    demo = {
        "待办": [
            ("整理季度 OKR", ["blue", "red"], "2026-09-10", "周五评审前完成初稿"),
            ("给桌宠换新皮肤", ["purple"], None, ""),
            ("阅读《设计心理学》", ["green"], "2026-09-01", "第三章开始"),
        ],
        "进行中": [
            ("看板拖拽交互打磨", ["orange"], "2026-09-06", "卡片悬浮阴影效果"),
            ("写单元测试", ["teal"], None, ""),
        ],
        "已完成": [
            ("项目脚手架搭建", ["green"], None, "PySide6 + JSON 存储"),
            ("深色主题适配", ["blue"], None, ""),
        ],
    }
    for list_title, cards in demo.items():
        for lst in board.lists:
            if lst.title == list_title:
                for title, labels, due, notes in cards:
                    c = Card(title=title)
                    c.labels = labels
                    c.due_date = due
                    c.notes = notes
                    c.done = list_title == "已完成"
                    lst.cards.append(c)
    controller._after_data_change(None)

failures = []

def safe(fn, label):
    try:
        fn()
    except Exception:
        print(f"[smoke] ERROR in {label}:")
        traceback.print_exc()
        failures.append(label)

def take_dark() -> None:
    window.repaint()
    pix = window.grab()
    out = Path(__file__).parent / "smoke_board_dark.png"
    pix.save(str(out))
    print(f"[smoke] board_dark: saved={out.exists()} size={pix.width()}x{pix.height()}")
    print("[smoke] ALL DONE")
    app.quit()

def take_board_dark_async() -> None:
    safe(lambda: AppTheme.set_mode("dark"), "set_mode_dark")
    QTimer.singleShot(400, take_dark)

def take(mode: str) -> None:
    window.repaint()
    pix = window.grab()
    out = Path(__file__).parent / f"smoke_{mode}.png"
    pix.save(str(out))
    print(f"[smoke] {mode}: saved={out.exists()} size={pix.width()}x{pix.height()}")
    if mode == "pet":
        safe(window.expand, "expand")
        QTimer.singleShot(500, lambda: take("board"))
    else:
        QTimer.singleShot(300, take_board_dark_async)

def start() -> None:
    safe(window.collapse, "collapse")
    QTimer.singleShot(400, lambda: take("pet"))

QTimer.singleShot(400, start)
# 硬超时保护：12 秒后强制退出
QTimer.singleShot(12000, lambda: (print("[smoke] TIMEOUT"), app.quit()))
app.exec()

print("[smoke] FAILURES:", failures if failures else "none")
pngs = ["smoke_pet.png", "smoke_board.png", "smoke_board_dark.png"]
missing = [p for p in pngs if not (Path(__file__).parent / p).exists()]
print("[smoke] missing:", missing if missing else "none")
sys.exit(0 if not failures and not missing else 1)

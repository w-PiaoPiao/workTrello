#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视觉皮肤预览：把 5 套皮肤各渲染一张真实看板截图

为什么要有这个脚本
------------------
皮肤改的是「色表 + 圆角 + 字重 + 徽章形态 + 板底纹理」，只看 QSS 字符串
判断不出效果：Qt 会静默丢弃解析失败的样式表片段（画面上直接退回原生
外观），圆角、描边宽、字体回退也只有真渲染才看得见。所以改皮肤后**必须**
离屏渲染一遍再肉眼核对。

这与 smoke_test.py 的分工：smoke_test 跑的是完整 AppController 的冒烟
截图（验收用）；本脚本只构造一个独立 BoardView + 假数据，用来横向比较
皮肤，不启动托盘/桌宠/快捷键等系统服务。

用法：
    QT_QPA_PLATFORM=offscreen python scripts/theme_preview.py [--out DIR]
    QT_QPA_PLATFORM=offscreen python scripts/theme_preview.py --skin warm --mode dark
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 数据目录隔离：预览脚本绝不能读写真实看板
os.environ.setdefault("PET_BOARD_DATA_DIR", tempfile.mkdtemp())

from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout   # noqa: E402

_qapp = QApplication.instance() or QApplication([])

from app.config import AppConfig                                   # noqa: E402
from app.models.board import BoardList, Card                       # noqa: E402
from app.views import motion, tokens                               # noqa: E402
from app.views.board_view import BoardView                         # noqa: E402
from app.views.theme import AppTheme                               # noqa: E402


def _demo_board(today: date) -> BoardList:
    """与 smoke_test 同口径的示例数据（含标签/优先级/截止/清单/附件/备注）"""
    todo = BoardList(title="待办")
    doing = BoardList(title="进行中")
    done = BoardList(title="已完成")
    c1 = Card(title="整理季度 OKR", labels=["red", "blue"], priority=1,
              due_date=(today - timedelta(days=6)).isoformat())
    c1.checklist = [{"text": "梳理目标", "done": True},
                    {"text": "对齐负责人", "done": False}]
    c1.attachments = [{"name": "okr.md", "path": "/tmp/okr.md"}]
    c1.notes = "季度目标：拉动转化率"
    c2 = Card(title="给桌宠换新皮肤", labels=["red"], priority=2,
              due_date=(today - timedelta(days=5)).isoformat())
    c3 = Card(title="阅读《设计心理学》", labels=["purple"], priority=3,
              due_date=(today - timedelta(days=3)).isoformat())
    c3.notes = "第 3 章：可供性"
    c4 = Card(title="看板拖拽交互打磨", labels=["orange", "teal"], priority=2,
              due_date=(today - timedelta(days=2)).isoformat())
    c5 = Card(title="写单元测试", labels=["orange"], priority=2,
              due_date=(today + timedelta(days=2)).isoformat())
    c6 = Card(title="项目脚手架搭建", labels=["green"], done=True,
              notes="已完成初版")
    c7 = Card(title="深色主题适配", labels=["green"], priority=2, done=True)
    todo.cards.extend([c1, c2, c3])
    doing.cards.extend([c4, c5])
    done.cards.extend([c6, c7])
    return [todo, doing, done]


class _Host(QWidget):
    """把 BoardView 装进一个带主题底色的窗口（截图时四边不透明）"""

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        c = AppTheme.colors()
        self.setStyleSheet(f"QWidget#host {{ background: {c['bg_primary']}; }}")
        self.setObjectName("host")
        self.view = BoardView()
        # 与真实运行一致：BoardView 的整块样式表由 reapply_theme/reapply_skin
        # 下发到自身。真实 AppController 在构造后就会调一次；这里必须显式
        # 调（否则列/卡片拿到的是空 parents 样式表 = 透明）
        self.view.reapply_skin()
        lay.addWidget(self.view)
        self.resize(tokens.size(820), tokens.size(600))


def render(skin: str, mode: str, out_dir: Path) -> Path:
    # 关掉过渡动效：卡片"新增淡入"是 QGraphicsOpacityEffect，离屏 grab
    # 时动画没跑完就截到了全透明（截图里卡片凭空消失）。预览要的是
    # 稳定终态，不是某一帧。
    motion.set_enabled(False)
    AppTheme.set_skin(skin, touch_pref=False)
    AppTheme.set_mode(mode)
    BoardView._bg_gradient_cache.clear()
    BoardView._chrome_path_cache.clear()
    host = _Host()
    # 先 show 再 refresh：看板重建列/卡片时会读控件的可见性与几何，未上屏
    # 的控件在部分平台（实测 macOS 离屏）重绘路径会踩空指针
    host.show()
    _qapp.processEvents()
    host.view.refresh(_demo_board(date.today()),
                      stats={"today": 3, "done": 2, "total": 7})
    host.view.reapply_skin()
    for _ in range(4):
        _qapp.processEvents()
    # 布局完成后再刷一次：徽章宽度要等 re-polish 后的字体度量才定得下来
    host.view.reapply_skin()
    for _ in range(3):
        _qapp.processEvents()
    host.repaint()
    _qapp.processEvents()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"skin-{skin}-{mode}.png"
    host.grab().save(str(path))
    host.close()
    host.deleteLater()
    _qapp.processEvents()
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="渲染各视觉皮肤的真实看板截图")
    ap.add_argument("--out", default=str(ROOT / "design" / "style-previews"))
    ap.add_argument("--skin", default="", help="只渲染某套皮肤")
    ap.add_argument("--mode", default="", choices=["", "light", "dark"])
    args = ap.parse_args(argv)
    out_dir = Path(args.out)
    skins = [args.skin] if args.skin else list(AppConfig.UI_SKINS)
    modes = [args.mode] if args.mode else ["light", "dark"]
    base_skin = AppConfig.get_ui_skin()
    for skin in skins:
        for mode in modes:
            path = render(skin, mode, out_dir)
            print(f"saved {path}")
    # 还原：预览脚本不该把偏好改成最后渲染的那套
    AppTheme.set_skin(base_skin, touch_pref=False)
    motion.set_enabled(True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

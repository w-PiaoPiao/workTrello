# -*- coding: utf-8 -*-
"""
主题调色板与原生外观测试：调色板跟随主题，避免系统深色时
未样式化控件（日期框/勾选框）与应用浅色主题错乱

用法：QT_QPA_PLATFORM=offscreen python tests/test_theme.py
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 数据目录隔离：单文件 / discover / pytest 运行方式下
# 都不得读写真实用户数据（防止全量回归清空 %LOCALAPPDATA% 看板）
os.environ["PET_BOARD_DATA_DIR"] = tempfile.mkdtemp()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

_qapp = QApplication.instance() or QApplication([])

from app.views.theme import AppTheme


class ThemePaletteTest(unittest.TestCase):
    def test_palette_follows_light_theme(self):
        AppTheme.set_mode("light")
        pal = AppTheme._build_palette()
        c = AppTheme.colors()
        self.assertEqual(pal.color(QPalette.ColorRole.Base), QColor(c["bg_card"]))
        self.assertEqual(pal.color(QPalette.ColorRole.WindowText),
                         QColor(c["text_primary"]))

    def test_palette_follows_dark_theme(self):
        AppTheme.set_mode("dark")
        pal = AppTheme._build_palette()
        c = AppTheme.colors()
        self.assertEqual(pal.color(QPalette.ColorRole.Base), QColor(c["bg_card"]))
        self.assertEqual(pal.color(QPalette.ColorRole.Highlight),
                         QColor(c["accent"]))
        AppTheme.set_mode("light")   # 还原全局单例

    def test_qss_covers_unstyled_controls(self):
        """全局 QSS 需覆盖日期框/勾选框（曾因缺规则在系统深色下错乱）"""
        qss = AppTheme.global_qss()
        self.assertIn("QDateEdit", qss)
        self.assertIn("QCheckBox", qss)

    def test_first_set_mode_applies_global_qss(self):
        """首次 set_mode 必须真正下发全局 QSS（哪怕模式与初始值相同）

        回归：set_mode 曾按"模式未变"直接 return，而首次启动恰好是
        "浅色→浅色" → 全局 QSS 从不下发；此前靠 MainWindow 复制一份
        窗口级 QSS 兜底。窗口级副本已删除，必须由 apply() 保证下发，
        否则滚动条等全局样式退化成 Qt 默认外观（实测 18px 宽 + 箭头）。
        """
        qapp = QApplication.instance()
        saved_qss = qapp.styleSheet()
        saved_applied = AppTheme._applied
        try:
            qapp.setStyleSheet("")           # 模拟"尚未下发"的初始态
            AppTheme._applied = False
            AppTheme.set_mode(AppTheme.mode())   # 与当前模式相同
            self.assertTrue(AppTheme._applied)
            self.assertEqual(qapp.styleSheet(), AppTheme.global_qss())
            self.assertIn("QScrollBar", qapp.styleSheet())
        finally:
            qapp.setStyleSheet(saved_qss)
            AppTheme._applied = saved_applied

    def test_scrollbar_handle_uses_color_token(self):
        """滚动条把手取色必须走配色表，而非写死灰值

        此前 rgba(128,128,128,α) 在 5 个文件各写一遍（改一处要改 13 处）。
        浅色主题下 token 值与旧字面量恰好相同，故用"改 token → QSS 跟随"
        来验证真的走了取色，而不是碰巧字面量相等。
        """

        c = AppTheme.colors()
        self.assertIn("scroll_handle", c)
        self.assertIn("scroll_handle_hover", c)
        qss = AppTheme.global_qss()
        self.assertIn(c["scroll_handle"], qss)
        self.assertIn(c["scroll_handle_hover"], qss)
        # 改 token → QSS 必须跟着变（证明确实引用 token）
        old = c["scroll_handle"]
        try:
            c["scroll_handle"] = "rgba(1, 2, 3, 0.5)"
            self.assertIn("rgba(1, 2, 3, 0.5)", AppTheme.global_qss())
        finally:
            c["scroll_handle"] = old
        self.assertIn(old, AppTheme.global_qss())


if __name__ == "__main__":
    unittest.main(verbosity=2)

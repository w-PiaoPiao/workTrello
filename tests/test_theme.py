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


if __name__ == "__main__":
    unittest.main(verbosity=2)

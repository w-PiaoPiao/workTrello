# -*- coding: utf-8 -*-
"""
红绿灯绘制回归测试：悬停符号绘制不得抛 ValueError

背景：setBrush(Qt.NoPen) 在 PySide6 6.10 下无法解析参数，paintEvent 内
抛异常会跳过 painter.end()，泄漏的 painter 损坏 backing store，
下一次 flush 直接段错误（表现为鼠标悬停红绿灯时应用闪退）。

用法：QT_QPA_PLATFORM=offscreen python tests/test_traffic_lights.py
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

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QApplication

_qapp = QApplication.instance() or QApplication([])

from app.views.traffic_lights import TrafficLights


class TrafficLightsPaintTest(unittest.TestCase):
    def setUp(self):
        self.tl = TrafficLights()
        self.pix = QPixmap(self.tl.size())
        self.pix.fill(Qt.transparent)

    def _paint_all_symbols(self):
        p = QPainter(self.pix)
        try:
            for i in range(3):
                self.tl._paint_symbol(p, i,
                                      self.tl._circle_rect(i).center())
        finally:
            p.end()

    def test_paint_symbols_do_not_raise(self):
        """三个悬停符号（✕/−/⤢）在真实 painter 上绘制不抛异常"""
        self._paint_all_symbols()  # 抛 ValueError 即失败

    def test_full_paint_event_with_hover(self):
        """悬停态走完整 paintEvent 不抛异常"""
        self.tl._hover = 1
        self.tl.grab()
        self.tl._hover = 2
        self.tl.grab()

    def test_geometry_contains_all_circles(self):
        for i in range(3):
            r = self.tl._circle_rect(i)
            self.assertTrue(r.right() <= self.tl.width())
            self.assertEqual(r.height(), 12)


if __name__ == "__main__":
    unittest.main(verbosity=2)

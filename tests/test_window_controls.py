# -*- coding: utf-8 -*-
"""
Windows 窗口控制键测试：三键信号发射、按下拖出释放不触发、
最大化图标状态切换、hover/主题刷新绘制不抛异常。

用法：QT_QPA_PLATFORM=offscreen python tests/test_window_controls.py
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

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtWidgets import QApplication

_qapp = QApplication.instance() or QApplication([])

from app.views.theme import AppTheme
from app.views.window_controls import WindowControls


class WindowControlsSignalTest(unittest.TestCase):
    def setUp(self):
        self.wc = WindowControls()

    def tearDown(self):
        self.wc.deleteLater()

    def _release_at(self, index: int, x_offset: float = 0.0):
        """模拟按下并释放第 index 键，x_offset 使释放点偏离（测试反悔）"""
        center = self.wc._btn_rect(index).center()
        press = QPointF(center.x(), center.y())
        release = QPointF(center.x() + x_offset, center.y())
        self.wc.mousePressEvent(
            _mouse_event(QEvent.Type.MouseButtonPress, press))
        self.wc.mouseReleaseEvent(
            _mouse_event(QEvent.Type.MouseButtonRelease, release))

    def test_minimize_signal_emitted(self):
        emitted = []
        self.wc.signal_minimize.connect(lambda: emitted.append(True))
        self._release_at(0)
        self.assertEqual(emitted, [True])

    def test_zoom_signal_emitted(self):
        emitted = []
        self.wc.signal_zoom.connect(lambda: emitted.append(True))
        self._release_at(1)
        self.assertEqual(emitted, [True])

    def test_close_signal_emitted(self):
        emitted = []
        self.wc.signal_close.connect(lambda: emitted.append(True))
        self._release_at(2)
        self.assertEqual(emitted, [True])

    def test_release_outside_button_does_not_emit(self):
        """按下后拖出键区再释放：不触发（对齐原生可反悔行为）"""
        emitted = []
        for sig in (self.wc.signal_minimize,
                    self.wc.signal_zoom, self.wc.signal_close):
            sig.connect(lambda: emitted.append(True))
        self._release_at(1, x_offset=self.wc._BTN_W)
        self.assertEqual(emitted, [])

    def test_press_only_no_emit(self):
        emitted = []
        self.wc.signal_close.connect(lambda: emitted.append(True))
        center = self.wc._btn_rect(2).center()
        self.wc.mousePressEvent(
            _mouse_event(QEvent.Type.MouseButtonPress, center))
        self.assertEqual(emitted, [])

    def test_geometry_three_buttons(self):
        self.assertEqual(self.wc.width(), 46 * 3)
        for i in range(3):
            r = self.wc._btn_rect(i)
            self.assertLessEqual(r.right(), self.wc.width())
            self.assertLessEqual(r.bottom(), self.wc.height())
            if i > 0:
                self.assertEqual(r.left(),
                                 self.wc._btn_rect(i - 1).right())


class WindowControlsStateTest(unittest.TestCase):
    def setUp(self):
        self.wc = WindowControls()

    def tearDown(self):
        self.wc.deleteLater()

    def test_zoomed_state_toggles(self):
        self.assertFalse(self.wc._zoomed)
        self.wc.set_zoomed(True)
        self.assertTrue(self.wc._zoomed)
        self.wc.set_zoomed(False)
        self.assertFalse(self.wc._zoomed)

    def test_hover_paint_does_not_raise(self):
        """hover/关闭键红底等完整 paintEvent 不抛异常"""
        for i in range(3):
            self.wc._hover = i
            self.wc.grab()
        self.wc._pressed = 2
        self.wc.grab()
        self.wc._pressed = -1

    def test_zoomed_paint_does_not_raise(self):
        self.wc.set_zoomed(True)
        self.wc._hover = 1
        self.wc.grab()

    def test_reapply_updates_painter(self):
        self.wc.reapply()  # 主题刷新路径不崩
        AppTheme.set_mode("dark")
        self.wc.grab()
        AppTheme.set_mode("light")
        self.wc.grab()


def _mouse_event(kind, pos: QPointF):
    """构造最小 mouse 事件（kind 取 QEvent.Type.MouseButtonPress/Release）"""
    from PySide6.QtGui import QMouseEvent

    button = Qt.LeftButton
    return QMouseEvent(kind, pos, button, button, Qt.NoModifier)


if __name__ == "__main__":
    unittest.main(verbosity=2)

# -*- coding: utf-8 -*-
"""
窗口置顶切换测试：Qt.WindowStaysOnTopHint 的设置/恢复、可见性保持、
勾选态同步（blockSignals 防回环）

用法：QT_QPA_PLATFORM=offscreen python tests/test_main_window.py
"""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

_qapp = QApplication.instance() or QApplication([])

from app.config import AppConfig
from app.views.main_window import MainWindow
from app.views.pet_view import PetView


class AlwaysOnTopTest(unittest.TestCase):
    def setUp(self):
        self.w = MainWindow()

    def tearDown(self):
        self.w.hide()
        self.w.deleteLater()

    def test_default_is_on_top(self):
        self.assertTrue(self.w.is_always_on_top())
        self.assertTrue(self.w.windowFlags() & Qt.WindowStaysOnTopHint)

    def test_toggle_off_and_on(self):
        self.w.set_always_on_top(False)
        self.assertFalse(self.w.is_always_on_top())
        self.assertFalse(self.w.windowFlags() & Qt.WindowStaysOnTopHint)
        self.w.set_always_on_top(True)
        self.assertTrue(self.w.is_always_on_top())

    def test_toggle_preserves_visibility(self):
        self.w.show()
        self.assertTrue(self.w.isVisible())
        self.w.set_always_on_top(False)
        self.assertTrue(self.w.isVisible())
        self.w.set_always_on_top(True)
        self.assertTrue(self.w.isVisible())

    def test_toggle_while_hidden_stays_hidden(self):
        self.assertFalse(self.w.isVisible())
        self.w.set_always_on_top(False)
        self.assertFalse(self.w.isVisible())
        self.w.set_always_on_top(True)
        self.assertFalse(self.w.isVisible())

    def test_idempotent_toggle_is_noop(self):
        self.w.show()
        self.assertTrue(self.w.isVisible())
        self.w.set_always_on_top(True)  # 已是置顶：不应触发隐藏/重建
        self.assertTrue(self.w.isVisible())


class PetAlwaysTopMenuTest(unittest.TestCase):
    def test_checked_sync_does_not_recurse(self):
        pet = PetView()
        emitted = []
        pet.signal_always_top_toggled.connect(emitted.append)

        pet.set_always_top_checked(False)
        self.assertFalse(pet._act_always_top.isChecked())
        self.assertEqual(emitted, [])  # 同步不触发信号回环

        pet.set_always_top_checked(True)
        self.assertTrue(pet._act_always_top.isChecked())
        self.assertEqual(emitted, [])

        pet.deleteLater()


@unittest.skipUnless(AppConfig.IS_WINDOWS, "仅 Windows 启用边缘缩放")
class WindowsEdgeResizeTest(unittest.TestCase):
    def setUp(self):
        self.w = MainWindow()
        self.w.show()
        # 直接进入展开态（跳过动画），布局就绪后可测边缘
        self.w._mode = "expanded"
        self.w._expanding = False
        self.w._animation_running = False
        self.w.setFixedSize(AppConfig.BOARD_WIDTH, AppConfig.BOARD_HEIGHT)

    def tearDown(self):
        self.w.hide()
        self.w.deleteLater()

    def test_edge_at_returns_edges(self):
        m = AppConfig.RESIZE_MARGIN
        w, h = self.w.width(), self.w.height()
        # 四边与四角
        self.assertTrue(self.w._edge_at(QPoint(m // 2, h // 2)) & Qt.LeftEdge)
        self.assertTrue(self.w._edge_at(QPoint(w - 1, h // 2)) & Qt.RightEdge)
        self.assertTrue(self.w._edge_at(QPoint(w // 2, m // 2)) & Qt.TopEdge)
        self.assertTrue(self.w._edge_at(QPoint(w // 2, h - 1)) & Qt.BottomEdge)
        edge_tl = self.w._edge_at(QPoint(0, 0))
        self.assertTrue(edge_tl & Qt.LeftEdge and edge_tl & Qt.TopEdge)
        edge_br = self.w._edge_at(QPoint(w - 1, h - 1))
        self.assertTrue(edge_br & Qt.RightEdge and edge_br & Qt.BottomEdge)
        # 中心不是边缘
        self.assertIsNone(self.w._edge_at(QPoint(w // 2, h // 2)))

    def test_edge_at_collapsed_returns_none(self):
        self.w._mode = "collapsed"
        self.assertIsNone(self.w._edge_at(QPoint(0, 0)))

    def test_edge_at_zoomed_returns_none(self):
        """最大化状态下不允许边缘缩放"""
        self.w._zoomed = True
        self.assertIsNone(self.w._edge_at(QPoint(0, 0)))
        self.assertIsNone(self.w._edge_at(
            QPoint(self.w.width() - 1, self.w.height() - 1)))
        self.w._zoomed = False

    def test_resize_release_resets_cursor(self):
        """系统缩放结束后光标必须恢复（不能残留缩放样式）"""
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QCursor, QMouseEvent
        # 把窗口挪到系统鼠标正下方，确保 release 时鼠标在窗口内部（远离边缘）
        gp = QCursor.pos()
        self.w.setGeometry(gp.x() - 400, gp.y() - 300, 800, 600)
        _qapp.processEvents()
        # 模拟系统缩放中的状态：光标已被设为缩放样式
        self.w.setCursor(Qt.SizeHorCursor)
        self.w._resize_active = True
        release = QMouseEvent(QEvent.Type.MouseButtonRelease,
                              QPointF(400, 300), Qt.LeftButton,
                              Qt.NoButton, Qt.NoModifier)
        self.w.mouseReleaseEvent(release)
        self.assertFalse(self.w._resize_active)
        self.assertEqual(self.w.cursor().shape(), Qt.ArrowCursor)

    def test_mouse_in_expanded_guard(self):
        self.assertTrue(self.w._mouse_in_expanded())
        self.w._mode = "collapsed"
        self.assertFalse(self.w._mouse_in_expanded())
        self.w._mode = "expanded"
        self.w._animation_running = True
        self.assertFalse(self.w._mouse_in_expanded())


@unittest.skipUnless(AppConfig.IS_WINDOWS, "仅 Windows 有该信号联动")
class WindowsZoomSignalTest(unittest.TestCase):
    def setUp(self):
        self.w = MainWindow()
        self.w.setGeometry(300, 200, AppConfig.BOARD_WIDTH,
                           AppConfig.BOARD_HEIGHT)  # ≥ min，可还原
        self.w.show()
        self.w._mode = "expanded"
        self.w._animation_running = False

    def tearDown(self):
        self.w.hide()
        self.w.deleteLater()

    def test_zoom_state_changed_emitted(self):
        states = []
        self.w.zoom_state_changed.connect(states.append)
        self.w.toggle_zoom()
        self.assertEqual(states, [True])
        self.w.toggle_zoom()
        self.assertEqual(states, [True, False])

    def test_toggle_zoom_fills_workarea_on_windows(self):
        """Windows 最大化：铺满屏幕工作区（无 SCREEN_MARGIN 留边）"""
        self.w.toggle_zoom()
        self.assertTrue(self.w._zoomed)
        screen = self.w._current_screen()
        if screen is not None:
            self.assertEqual(self.w.geometry(), screen.availableGeometry())
        self.w.toggle_zoom()
        self.assertFalse(self.w._zoomed)

    def test_drag_maximized_restores_window(self):
        """拖动最大化的窗口 = 还原（Windows 原生惯例）"""
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QMouseEvent
        self.w.toggle_zoom()
        self.assertTrue(self.w._zoomed)
        # 按下（非边缘处）开始拖动
        press = QMouseEvent(QEvent.Type.MouseButtonPress,
                            QPointF(400, 28), Qt.LeftButton,
                            Qt.LeftButton, Qt.NoModifier)
        self.w.mousePressEvent(press)
        self.assertTrue(self.w._is_dragging)
        # 移动 → 应先还原再跟随
        move = QMouseEvent(QEvent.Type.MouseMove, QPointF(380, 20),
                           Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        self.w.mouseMoveEvent(move)
        self.assertFalse(self.w._zoomed)
        release = QMouseEvent(QEvent.Type.MouseButtonRelease,
                              QPointF(380, 20), Qt.LeftButton,
                              Qt.NoButton, Qt.NoModifier)
        self.w.mouseReleaseEvent(release)
        self.assertFalse(self.w._is_dragging)

    def test_toggle_zoom_restores_geometry(self):
        before = self.w.geometry()
        self.w.toggle_zoom()
        self.assertTrue(self.w._zoomed)
        # 还原后回到进入最大化前的几何
        self.w.toggle_zoom()
        self.assertFalse(self.w._zoomed)
        self.assertEqual(self.w.geometry(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)

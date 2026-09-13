# -*- coding: utf-8 -*-
"""
窗口置顶切换测试：Qt.WindowStaysOnTopHint 的设置/恢复、可见性保持、
勾选态同步（blockSignals 防回环）

用法：QT_QPA_PLATFORM=offscreen python tests/test_main_window.py
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 数据目录隔离：单文件 / discover / pytest 运行方式下
# 都不得读写真实用户数据（防止全量回归清空 %LOCALAPPDATA% 看板）
os.environ["PET_BOARD_DATA_DIR"] = tempfile.mkdtemp()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QWidget

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


class ExpandedSizeDebounceTest(unittest.TestCase):
    """展开尺寸持久化防抖：连续调整只写一次 QSettings，折叠时冲刷"""

    def setUp(self):
        self.w = MainWindow()
        self.w.show()   # 隐藏窗口不派发 resize 事件，须先显示
        self.w._mode = "expanded"
        self.w._expanding = False
        self.w._animation_running = False
        self.w._zoomed = False

    def tearDown(self):
        self.w.hide()
        self.w.deleteLater()

    def test_resize_burst_persists_once_on_timeout(self):
        with patch.object(AppConfig, "save_expanded_size") as save:
            self.w.resize(900, 600)
            self.w.resize(910, 610)
            self.w.resize(920, 620)
            save.assert_not_called()                # 连续调整中不写盘
            self.assertTrue(self.w._size_save_timer.isActive())
            self.w._size_save_timer.timeout.emit()  # 防抖到点 → 只写一次
            self.assertEqual(save.call_count, 1)

    def test_collapse_flushes_pending_size(self):
        with patch.object(AppConfig, "save_expanded_size") as save:
            self.w.resize(930, 630)
            save.assert_not_called()
            self.w.collapse()
            self.assertEqual(save.call_count, 1)


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

    def test_poll_resets_stuck_cursor_inside_window(self):
        """缩放后事件链断裂导致的光标粘滞：常驻轮询按真实位置回正"""
        from PySide6.QtGui import QCursor
        gp = QCursor.pos()
        self.w.setGeometry(gp.x() - 400, gp.y() - 300, 800, 600)
        _qapp.processEvents()
        # 模拟粘滞残留：光标与缓存都停在缩放样式，之后无任何 move 事件
        self.w.setCursor(Qt.SizeHorCursor)
        self.w._last_edge_cursor = Qt.SizeHorCursor
        self.w._edge_cursor_timer.timeout.emit()   # 轮询到点（鼠标在窗口中央）
        self.assertIsNone(self.w._last_edge_cursor)
        self.assertEqual(self.w.cursor().shape(), Qt.ArrowCursor)

    def test_refresh_cursor_outside_window_unsets(self):
        """鼠标在窗口外：不把窗外全局坐标误判成边缘，一律还原默认"""
        from unittest.mock import patch
        from PySide6.QtGui import QCursor
        self.w.setCursor(Qt.SizeVerCursor)
        self.w._last_edge_cursor = Qt.SizeVerCursor
        with patch.object(QCursor, "pos",
                          return_value=QPoint(-9999, -9999)):
            self.w._refresh_edge_cursor()
        self.assertIsNone(self.w._last_edge_cursor)
        self.assertEqual(self.w.cursor().shape(), Qt.ArrowCursor)

    def test_edge_cursor_ignored_when_collapsed(self):
        """折叠态不设缩放光标（守卫空转）"""
        self.w._mode = "collapsed"
        self.w.setCursor(Qt.SizeHorCursor)
        self.w._last_edge_cursor = Qt.SizeHorCursor
        self.w._refresh_edge_cursor()
        self.assertIsNone(self.w._last_edge_cursor)
        self.assertEqual(self.w.cursor().shape(), Qt.ArrowCursor)
        self.w._mode = "expanded"

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


class _FakeExpandedView:
    """主窗口展开视图桩：记录重命名/搜索/多选状态，供 Esc 链分支测试"""

    def __init__(self):
        self.rename_open = False
        self.search_text = ""
        self.search_focus = False
        self.selection_active = False

    def finish_rename(self, cancel=False):
        if self.rename_open:
            self.rename_open = False
            return True
        return False

    def search_has_focus(self):
        return self.search_focus

    def clear_search_if_active(self):
        if self.search_text:
            self.search_text = ""
            return True
        return False

    def clear_selection_if_active(self):
        if self.selection_active:
            self.selection_active = False
            return True
        return False


class EscCollapseChainTest(unittest.TestCase):
    """Esc 折叠链：取消重命名 →（焦点在搜索框时清空搜索）→ 折叠"""

    def setUp(self):
        self.w = MainWindow()
        self.w._mode = "expanded"
        self.view = _FakeExpandedView()
        self.w._expanded_view = self.view

    def tearDown(self):
        self.w.hide()
        self.w.deleteLater()

    def _press_esc(self):
        with patch.object(AppConfig, "save_expanded_size"):
            self.w._on_esc_pressed()

    def test_esc_chain_rename_first_then_collapse(self):
        self.view.rename_open = True
        self._press_esc()
        self.assertFalse(self.view.rename_open)      # 第一下：取消重命名
        self.assertEqual(self.w.mode, "expanded")
        self._press_esc()
        self.assertEqual(self.w.mode, "collapsed")   # 第二下：折叠

    def test_esc_clears_selection_before_search_and_collapse(self):
        """Esc 链新环节：有多选时先清多选（再按才轮到搜索/折叠）"""
        self.view.selection_active = True
        self.view.search_text = "待办"
        self.view.search_focus = True
        self._press_esc()
        self.assertFalse(self.view.selection_active)  # 第一下：清多选
        self.assertEqual(self.view.search_text, "待办")   # 搜索未动
        self.assertEqual(self.w.mode, "expanded")
        self._press_esc()
        self.assertEqual(self.view.search_text, "")   # 第二下：清搜索
        self.assertEqual(self.w.mode, "expanded")
        self._press_esc()
        self.assertEqual(self.w.mode, "collapsed")    # 第三下：折叠

    def test_esc_clears_focused_search_before_collapse(self):
        self.view.search_text = "待办"
        self.view.search_focus = True
        self._press_esc()
        self.assertEqual(self.view.search_text, "")  # 焦点在搜索框：先清空
        self.assertEqual(self.w.mode, "expanded")
        self._press_esc()
        self.assertEqual(self.w.mode, "collapsed")

    def test_esc_collapses_when_search_text_unfocused(self):
        """回归：搜索有字但焦点不在搜索框时，一下 Esc 即折叠（原需三次）"""
        self.view.search_text = "待办"
        self.view.search_focus = False
        self._press_esc()
        self.assertEqual(self.w.mode, "collapsed")
        self.assertEqual(self.view.search_text, "待办")  # 不误清搜索

    def test_esc_collapses_when_nothing_active(self):
        self._press_esc()
        self.assertEqual(self.w.mode, "collapsed")


@unittest.skipIf(AppConfig.IS_MACOS, "键盘入口仅非 macOS（macOS 由全局菜单栏承担）")
class NonMacKeyboardShortcutTest(unittest.TestCase):
    def setUp(self):
        self.w = MainWindow()

    def tearDown(self):
        self.w.hide()
        self.w.deleteLater()

    def test_ctrl_z_registered_and_fires_undo_signal(self):
        self.assertEqual(self.w._undo_shortcut.key(), QKeySequence.Undo)
        fired = []
        self.w.undo_shortcut.connect(lambda: fired.append(1))
        self.w._undo_shortcut.activated.emit()
        self.assertEqual(fired, [1])

    def test_ctrl_n_registered_and_fires_new_card_signal(self):
        self.assertEqual(self.w._new_card_shortcut.key(), QKeySequence.New)
        fired = []
        self.w.new_card_shortcut.connect(lambda: fired.append(1))
        self.w._new_card_shortcut.activated.emit()
        self.assertEqual(fired, [1])

class CollapseInterruptTest(unittest.TestCase):
    """折叠动画被 hide() 打断（托盘隐藏）时：强制回折叠尺寸、收起缩放把手"""

    def setUp(self):
        self.w = MainWindow()
        self.w.set_views(PetView(), QWidget())
        self.w.show()

    def tearDown(self):
        self.w.hide()
        self.w.deleteLater()

    def test_hide_during_collapse_restores_collapsed_size(self):
        self.w.expand()
        self.w.collapse()                 # 启动折叠动画（240ms）
        if self.w._animation_running:
            self.w.hide()                 # hide 打断动画
        self.assertEqual(self.w.mode, "collapsed")
        self.assertEqual(self.w.width(), AppConfig.PET_WIDTH)
        self.assertEqual(self.w.height(), AppConfig.PET_HEIGHT)
        if self.w._resize_grip is not None:
            self.assertFalse(self.w._resize_grip.isVisible())



if __name__ == "__main__":
    unittest.main(verbosity=2)

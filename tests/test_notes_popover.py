# -*- coding: utf-8 -*-
"""
备注悬浮预览测试：有备注卡片才有「≡ 有备注」徽章；
悬停徽章弹出浮层并完整展示备注（保留换行）；移开延迟隐藏。

用法：QT_QPA_PLATFORM=offscreen python tests/test_notes_popover.py
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

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

_qapp = QApplication.instance() or QApplication([])

from app.models.board import BoardList, Card
from app.views.board_view import BoardView
from app.views.notes_popover import (
    _reset_popover,
    hide_notes_popover,
    notes_popover,
)


def _make_view():
    view = BoardView()
    view.refresh([BoardList(title="待办", cards=[
        Card(title="带备注", notes="第一行\n09-08 14:30 完成 A\n第二行"),
        Card(title="无备注"),
        Card(title="另一条备注", notes="B 卡备注内容"),
    ])])
    view.resize(900, 600)
    view.show()
    return view


class NotesBadgeTest(unittest.TestCase):
    def setUp(self):
        _reset_popover()
        self.view = _make_view()

    def tearDown(self):
        hide_notes_popover()
        self.view.hide()
        self.view.deleteLater()
        _reset_popover()

    def _cards(self):
        return self.view._columns[0]._card_widgets

    def test_badge_only_when_notes(self):
        c1, c2, _c3 = self._cards()
        self.assertIsNotNone(c1._notes_badge)
        self.assertEqual(c1._notes_badge.text(), "≡ 有备注")
        self.assertIsNone(c2._notes_badge)      # 无备注无徽章

    def test_hover_shows_full_notes(self):
        c1, *_ = self._cards()
        c1.eventFilter(c1._notes_badge, QEvent(QEvent.Enter))
        pop = notes_popover()
        self.assertTrue(pop.isVisible())
        self.assertEqual(pop._body.text(), "第一行\n09-08 14:30 完成 A\n第二行")
        pop.hide_now()

    def test_leave_schedules_hide(self):
        """Leave 只延迟不立即隐藏；hide_now 立即生效"""
        c1, *_ = self._cards()
        c1.eventFilter(c1._notes_badge, QEvent(QEvent.Enter))
        pop = notes_popover()
        c1.eventFilter(c1._notes_badge, QEvent(QEvent.Leave))
        self.assertTrue(pop.isVisible())        # 延迟期内仍可见
        pop.hide_now()
        self.assertFalse(pop.isVisible())

    def test_click_pins_and_hover_does_not_steal(self):
        """点击徽章固定展示；固定期间悬停其他徽章不抢占内容、移开不关闭"""
        c1, _c2, c3 = self._cards()
        press = QMouseEvent(QEvent.Type.MouseButtonPress,
                            QPoint(0, 0), Qt.LeftButton, Qt.LeftButton,
                            Qt.NoModifier)
        c1.eventFilter(c1._notes_badge, press)
        pop = notes_popover()
        self.assertTrue(pop.isVisible())
        self.assertTrue(pop.is_pinned())
        self.assertTrue(pop.pinned_for(c1.card().id))
        self.assertEqual(pop._body.text(), c1.card().notes)
        # 固定中：Leave 不自动隐藏
        c1.eventFilter(c1._notes_badge, QEvent(QEvent.Leave))
        self.assertTrue(pop.isVisible())
        # 固定中：悬停另一卡徽章不切换内容
        c3.eventFilter(c3._notes_badge, QEvent(QEvent.Enter))
        self.assertTrue(pop.pinned_for(c1.card().id))
        self.assertEqual(pop._body.text(), c1.card().notes)
        pop.hide_now()

    def test_click_again_unpins(self):
        """同一徽章再点一次收起；点击不冒泡打开卡片编辑"""
        c1, *_ = self._cards()
        press = QMouseEvent(QEvent.Type.MouseButtonPress,
                            QPoint(0, 0), Qt.LeftButton, Qt.LeftButton,
                            Qt.NoModifier)
        edited = []
        c1.signal_edit_requested.connect(edited.append)
        c1.eventFilter(c1._notes_badge, press)
        pop = notes_popover()
        self.assertTrue(pop.isVisible())
        self.assertEqual(edited, [])            # 点击徽章不误触编辑对话框
        c1.eventFilter(c1._notes_badge, press)  # 再点一次收起
        self.assertFalse(pop.isVisible())
        self.assertFalse(pop.is_pinned())

    def test_pin_switches_to_other_badge(self):
        """固定于 A 时点击 B 徽章：固定归属切到 B"""
        c1, _c2, c3 = self._cards()
        press = QMouseEvent(QEvent.Type.MouseButtonPress,
                            QPoint(0, 0), Qt.LeftButton, Qt.LeftButton,
                            Qt.NoModifier)
        c1.eventFilter(c1._notes_badge, press)
        pop = notes_popover()
        self.assertTrue(pop.pinned_for(c1.card().id))
        c3.eventFilter(c3._notes_badge, press)
        self.assertTrue(pop.is_pinned())
        self.assertTrue(pop.pinned_for(c3.card().id))
        self.assertEqual(pop._body.text(), c3.card().notes)
        pop.hide_now()

    def test_model_update_unpins_pinned_preview(self):
        """本卡模型刷新（如编辑保存）后收起其固定预览，避免残留旧备注文本"""
        c1, *_ = self._cards()
        press = QMouseEvent(QEvent.Type.MouseButtonPress,
                            QPoint(0, 0), Qt.LeftButton, Qt.LeftButton,
                            Qt.NoModifier)
        c1.eventFilter(c1._notes_badge, press)
        pop = notes_popover()
        self.assertTrue(pop.is_pinned())
        c1.card().notes = "编辑后的备注"
        c1.update_from_model(c1.card())         # 模拟控制器编辑保存后的刷新
        self.assertFalse(pop.isVisible())
        self.assertFalse(pop.is_pinned())

    def test_popover_theme_repaint(self):
        """深浅主题切换后浮层可正常绘制"""
        from app.views.theme import AppTheme
        c1, *_ = self._cards()
        c1.eventFilter(c1._notes_badge, QEvent(QEvent.Enter))
        pop = notes_popover()
        AppTheme.set_mode("dark")
        pop.grab()
        AppTheme.set_mode("light")
        pop.grab()
        pop.hide_now()

    # ── 今日清单浮窗的主题跟随 ────────────────────────────

    def test_today_popover_follows_theme(self):
        """切主题后今日浮窗配色跟随（回归：此前未注册主题回调，配色冻结）

        浮窗外壳与每行的配色都是构建时的快照，必须由 AppTheme.register
        的回调重刷；只测外壳不足以覆盖"行还是旧主题"的情形。
        """
        from app.views.theme import AppTheme
        from app.views.today_popover import TodayPopover
        from datetime import date
        card = Card(title="今日卡", due_date=date.today().isoformat())
        lst = BoardList(title="待办", cards=[card])
        pop = TodayPopover()
        try:
            pop.set_items([(lst, card)])
            AppTheme.set_mode("dark")
            self.assertIn(AppTheme.colors()["bg_card"],
                          pop._frame.styleSheet())          # 外壳已跟随
            dark_row = pop._rows[0][2]
            self.assertIn(AppTheme.colors()["text_primary"],
                          dark_row.findChildren(type(pop._title_label))[0]
                          .styleSheet())                     # 行文字已跟随
            # 行仍是同一份数据（重建后行数不减）
            self.assertEqual(len(pop._rows), 1)
            AppTheme.set_mode("light")
            self.assertIn(AppTheme.colors()["bg_card"],
                          pop._frame.styleSheet())
        finally:
            pop.close()
            pop.deleteLater()


class PopoverUnitTest(unittest.TestCase):
    def setUp(self):
        _reset_popover()

    def tearDown(self):
        hide_notes_popover()
        _reset_popover()

    def test_empty_text_hides(self):
        pop = notes_popover()
        pop.show_for("   ", pop.geometry())
        self.assertFalse(pop.isVisible())

    def test_multiline_kept_and_wrapped_width(self):
        pop = notes_popover()
        pop.show_for("短", pop.geometry())
        self.assertTrue(pop.isVisible())
        w_short = pop.width()
        pop.show_for("很长" * 300, pop.geometry())
        self.assertTrue(pop.isVisible())
        self.assertGreaterEqual(pop.width(), w_short)   # 长文至少不更窄
        self.assertLessEqual(pop.width(), pop._MAX_WIDTH + 40)  # 封顶（含边距）
        pop.hide_now()

    def test_show_twice_reuses_singleton(self):
        self.assertIs(notes_popover(), notes_popover())

    def test_repeated_show_same_content_skips_rebuild(self):
        """同内容同锚重复展示跳过样式/排版重建；变化或隐藏后重建"""
        from PySide6.QtCore import QRect
        pop = notes_popover()
        calls = []
        orig = pop.reapply_style
        pop.reapply_style = lambda: (calls.append(1), orig())
        anchor = QRect(200, 200, 60, 18)
        pop.show_for("同一段备注", anchor)
        self.assertEqual(len(calls), 1)        # 首次展示需重建
        pop.show_for("同一段备注", anchor)      # 重复展示 → 跳过
        self.assertEqual(len(calls), 1)
        pop.show_for("换一段备注", anchor)      # 内容变化 → 重建
        self.assertEqual(len(calls), 2)
        pop.hide_now()
        pop.show_for("同一段备注", anchor)      # 隐藏后重新展示 → 重建
        self.assertEqual(len(calls), 3)
        pop.hide_now()


if __name__ == "__main__":
    unittest.main(verbosity=2)

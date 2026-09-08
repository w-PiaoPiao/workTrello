# -*- coding: utf-8 -*-
"""
备注悬浮预览测试：有备注卡片才有「≡ 有备注」徽章；
悬停徽章弹出浮层并完整展示备注（保留换行）；移开延迟隐藏。

用法：QT_QPA_PLATFORM=offscreen python tests/test_notes_popover.py
"""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEvent
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
        c1, c2 = self._cards()
        self.assertIsNotNone(c1._notes_badge)
        self.assertEqual(c1._notes_badge.text(), "≡ 有备注")
        self.assertIsNone(c2._notes_badge)      # 无备注无徽章

    def test_hover_shows_full_notes(self):
        c1, _ = self._cards()
        c1.eventFilter(c1._notes_badge, QEvent(QEvent.Enter))
        pop = notes_popover()
        self.assertTrue(pop.isVisible())
        self.assertEqual(pop._body.text(), "第一行\n09-08 14:30 完成 A\n第二行")
        pop.hide_now()

    def test_leave_schedules_hide(self):
        """Leave 只延迟不立即隐藏；hide_now 立即生效"""
        c1, _ = self._cards()
        c1.eventFilter(c1._notes_badge, QEvent(QEvent.Enter))
        pop = notes_popover()
        c1.eventFilter(c1._notes_badge, QEvent(QEvent.Leave))
        self.assertTrue(pop.isVisible())        # 延迟期内仍可见
        pop.hide_now()
        self.assertFalse(pop.isVisible())

    def test_popover_theme_repaint(self):
        """深浅主题切换后浮层可正常绘制"""
        from app.views.theme import AppTheme
        c1, _ = self._cards()
        c1.eventFilter(c1._notes_badge, QEvent(QEvent.Enter))
        pop = notes_popover()
        AppTheme.set_mode("dark")
        pop.grab()
        AppTheme.set_mode("light")
        pop.grab()
        pop.hide_now()


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


if __name__ == "__main__":
    unittest.main(verbosity=2)

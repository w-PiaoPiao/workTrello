# -*- coding: utf-8 -*-
"""
日历视图测试：月历网格结构（补位格/今日高亮）、补位格拒拖放、
拖放改期信号、条目点击编辑、超量折叠、翻月信号

用法（离屏环境）：
    QT_QPA_PLATFORM=offscreen python tests/test_calendar_view.py
"""

import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PET_BOARD_DATA_DIR"] = tempfile.mkdtemp()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication

_qapp = QApplication.instance() or QApplication([])

from app.models.board import Card
from app.views.board_view import MIME_CARD
from app.views.calendar_view import _MAX_CHIPS_PER_CELL, _DayCell, CalendarDialog


_LAST_MIME = None   # QDropEvent 不持有 mime 所有权：必须持住引用防 GC 悬空


def _card_drag_event(kind, card_id="card-1"):
    """构造携带 MIME_CARD 的拖放事件（kind: dragEnter / drop）

    mime 存模块级引用——事件对象不接管 mime 所有权，函数局部变量被
    回收后 dropEvent 里访问 mimeData() 是悬空指针（段错误）。
    注意 QDragEnterEvent 签名收 QPoint（整数），与 QDropEvent 的
    QPointF 重载不同。
    """
    global _LAST_MIME
    mime = QMimeData()
    mime.setData(MIME_CARD, card_id.encode("utf-8"))
    _LAST_MIME = mime
    if kind == "dragEnter":
        return QDragEnterEvent(QPoint(10, 10), Qt.DropAction.MoveAction,
                               mime, Qt.LeftButton, Qt.NoModifier)
    return QDropEvent(QPointF(10, 10), Qt.MoveAction, mime,
                      Qt.LeftButton, Qt.NoModifier)


class DayCellTest(unittest.TestCase):
    def setUp(self):
        self.cell = _DayCell(13, in_month=True, is_today=True,
                             iso="2026-09-13")

    def test_drop_emits_due_change(self):
        got = []
        self.cell.signal_drop_card.connect(
            lambda cid, iso: got.append((cid, iso)))
        self.cell.dropEvent(_card_drag_event("drop", "c1"))
        self.assertEqual(got, [("c1", "2026-09-13")])

    def test_drop_accepts_board_drag_too(self):
        """看板拖来的卡（同 MIME）落在日历上同样改期——共享 MIME 的红利"""
        event = _card_drag_event("drop", "from-board")
        self.cell.dropEvent(event)
        self.assertTrue(event.isAccepted())

    def test_out_of_month_cell_rejects_drop(self):
        """上月/下月补位格（iso 为空）不接受拖放，防改出空日期"""
        filler = _DayCell(31, in_month=False, is_today=False, iso="")
        event = _card_drag_event("dragEnter")
        filler.dragEnterEvent(event)
        self.assertFalse(event.isAccepted())
        drop = _card_drag_event("drop")
        filler.dropEvent(drop)
        self.assertFalse(drop.isAccepted())

    def test_in_month_cell_accepts_drag_enter(self):
        event = _card_drag_event("dragEnter")
        self.cell.dragEnterEvent(event)
        self.assertTrue(event.isAccepted())

    def test_set_cards_caps_and_more_label(self):
        """单格最多展示 N 个条目，超出折叠为"还有 N 项…\""""
        cards = [Card(title=f"卡{i}") for i in range(_MAX_CHIPS_PER_CELL + 2)]
        self.cell.set_cards(cards)
        self.assertEqual(len(self.cell._chip_widgets), _MAX_CHIPS_PER_CELL)
        labels = [w.text() for w in self.cell.findChildren(type(self.cell._head))
                  if w.objectName() == "dayMore"]
        self.assertTrue(any("2" in t for t in labels))

    def test_set_cards_clears_old_chips(self):
        self.cell.set_cards([Card(title="旧")])
        self.cell.set_cards([])
        self.assertEqual(self.cell._chip_widgets, [])

    def test_chip_click_emits_edit(self):
        got = []
        self.cell.signal_edit_card.connect(got.append)
        self.cell.set_cards([Card(title="点我")])
        chip = self.cell._chip_widgets[0]
        chip.signal_edit.emit(chip.card_id())
        self.assertEqual(len(got), 1)


class CalendarDialogTest(unittest.TestCase):
    def setUp(self):
        self.dlg = CalendarDialog()

    def tearDown(self):
        self.dlg.close()
        self.dlg.deleteLater()

    def _due_map(self):
        return {"2026-09-13": [Card(title="今天截止"),
                               Card(title="第二张")]}

    def test_set_month_grid_structure(self):
        """2026-09：9/1 周二（偏移 1）→ 首格补 8/31，网格 5 行；今日高亮"""
        today = date.today()
        self.dlg.set_month(2026, 9, self._due_map())
        cells = self.dlg._cells
        self.assertEqual(len(cells), 35)          # offset 1 + 30 天 → 5 行
        self.assertEqual(cells[0].iso(), "")      # 首格 = 8/31 补位
        self.assertEqual(cells[0]._head.text(), "31")
        self.assertEqual(cells[1].iso(), "2026-09-01")
        self.assertEqual(cells[13].iso(), "2026-09-13")
        self.assertEqual(cells[13]._head.text(), "13")
        # 今日恰在本月 → 该格样式含 accent 描边与加粗日期号
        # （高亮定义在格的整块样式表 #dayNum 规则里，不在 head 自身）
        if (today.year, today.month) == (2026, 9):
            today_cell = next(c for c in cells if c.iso() == today.isoformat())
            self.assertIn("bold", today_cell.styleSheet())
            self.assertIn("2px solid", today_cell.styleSheet())

    def test_set_month_fills_cards_by_date(self):
        self.dlg.set_month(2026, 9, self._due_map())
        cell13 = self.dlg._cells[13]
        self.assertEqual(len(cell13._chip_widgets), 2)
        empty = self.dlg._cells[14]               # 9/14 无卡
        self.assertEqual(empty._chip_widgets, [])

    def test_drop_on_cell_signals_due_change(self):
        got = []
        self.dlg.signal_due_change.connect(
            lambda cid, iso: got.append((cid, iso)))
        self.dlg.set_month(2026, 9, {})
        self.dlg._cells[13].dropEvent(_card_drag_event("drop", "c9"))
        self.assertEqual(got, [("c9", "2026-09-13")])

    def test_chip_click_bubbles_to_edit_signal(self):
        got = []
        self.dlg.signal_edit_requested.connect(got.append)
        self.dlg.set_month(2026, 9, self._due_map())
        chip = self.dlg._cells[13]._chip_widgets[0]
        chip.signal_edit.emit(chip.card_id())
        self.assertEqual(len(got), 1)

    def test_shift_month_emits_range(self):
        got = []
        # 模拟控制器回路：翻月信号 → 重新注入数据 → label 随 set_month 更新
        self.dlg.signal_month_changed.connect(
            lambda y, m: self.dlg.set_month(y, m, {}))
        self.dlg.signal_month_changed.connect(
            lambda y, m: got.append((y, m)))
        self.dlg.set_month(2026, 9, {})
        self.dlg._prev_btn.click()                # 9 月 → 8 月
        self.assertEqual(got, [(2026, 8)])
        self.assertEqual(self.dlg._month_label.text(), "2026 年 8 月")
        self.dlg._next_btn.click()
        self.dlg._next_btn.click()
        self.assertEqual(got[-1], (2026, 10))
        # 12 月 → 次年 1 月
        self.dlg.set_month(2026, 12, {})
        self.dlg._next_btn.click()
        self.assertEqual(got[-1], (2027, 1))
        # 1 月 → 去年 12 月
        self.dlg.set_month(2027, 1, {})
        self.dlg._prev_btn.click()
        self.assertEqual(got[-1], (2026, 12))
        self.assertEqual(self.dlg._month_label.text(), "2026 年 12 月")

    def test_go_today(self):
        got = []
        self.dlg.signal_month_changed.connect(
            lambda y, m: self.dlg.set_month(y, m, {}))
        self.dlg.signal_month_changed.connect(
            lambda y, m: got.append((y, m)))
        self.dlg.set_month(2020, 1, {})
        self.dlg._today_btn.click()
        t = date.today()
        self.assertEqual(got[-1], (t.year, t.month))
        self.assertEqual(self.dlg._month_label.text(),
                         f"{t.year} 年 {t.month} 月")

    def test_reapply_theme_restyles_cells(self):
        """主题切换后已有格与条目配色重刷（不抛异常即可）"""
        from app.views.theme import AppTheme
        self.dlg.set_month(2026, 9, self._due_map())
        AppTheme.set_mode("dark")
        self.dlg.reapply_theme()
        AppTheme.set_mode("light")
        self.dlg.reapply_theme()


if __name__ == "__main__":
    unittest.main(verbosity=2)

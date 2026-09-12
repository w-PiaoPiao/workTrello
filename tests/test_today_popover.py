# -*- coding: utf-8 -*-
"""今日清单浮窗测试：行控件复用、消失行退场、空态高度

需要 Qt 离屏环境；数据目录隔离到临时目录（在导入 app 模块前设置）。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PET_BOARD_DATA_DIR"] = tempfile.mkdtemp()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

_qapp = QApplication.instance() or QApplication([])

import shiboken6

from app.models.board import BoardList, Card
from app.views.today_popover import TodayPopover


def _row_ids(pop: TodayPopover) -> list[str]:
    return [cid for _lid, cid, _row in pop._rows]


class TodayPopoverTest(unittest.TestCase):
    def setUp(self):
        self.pop = TodayPopover()
        self.lst = BoardList(title="待办")

    def test_rows_reused_not_destroyed(self):
        """重复 set_items：复用行必须是同一存活控件（回归：此前先对全部
        行 deleteLater 再"复用"，复用控件回事件循环即被销毁）"""
        a, b = Card(title="A"), Card(title="B")
        self.pop.set_items([(self.lst, a), (self.lst, b)])
        rows1 = {cid: row for _lid, cid, row in self.pop._rows}
        self.pop.set_items([(self.lst, b), (self.lst, a)])
        rows2 = {cid: row for _lid, cid, row in self.pop._rows}
        self.assertIs(rows2[a.id], rows1[a.id])
        self.assertIs(rows2[b.id], rows1[b.id])
        self.assertTrue(shiboken6.isValid(rows2[a.id]))
        self.assertTrue(shiboken6.isValid(rows2[b.id]))

    def test_removed_row_leaves_rows_and_shrinks(self):
        """勾掉一行：该行退出 _rows 并安排销毁，高度收缩（不可见态立即）"""
        a, b = Card(title="A"), Card(title="B")
        self.pop.set_items([(self.lst, a), (self.lst, b)])
        self.assertEqual(len(_row_ids(self.pop)), 2)
        self.pop.set_items([(self.lst, a)])
        # 退场行已脱离行清单（deleteLater 排队）
        self.assertEqual(_row_ids(self.pop), [a.id])
        self.assertLess(self.pop.height(), 62 + 42 * 2)

    def test_empty_state_height_and_label(self):
        """空态：高度收缩到空态模板，标题计数归零"""
        self.pop.set_items([(self.lst, Card(title="A"))])
        self.pop.set_items([])
        self.assertEqual(self.pop.height(), 110)
        self.assertEqual(_row_ids(self.pop), [])
        self.assertIn("0", self.pop._title_label.text())

    def test_done_count_label(self):
        """回顾行：done_count>0 时显示"今日已完成 N 张\""""
        self.pop.set_items([], done_count=3)
        self.assertEqual(self.pop._done_label.text(), "今日已完成 3 张 🎉")


if __name__ == "__main__":
    import unittest
    unittest.main()

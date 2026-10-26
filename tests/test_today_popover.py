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
from app.views.today_popover import (
    _CHROME_H,
    _EMPTY_CHROME_H,
    _ROW_MIN_H,
    TodayPopover,
)


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
        self.assertLess(self.pop.height(), _CHROME_H + _ROW_MIN_H * 2)

    def test_empty_state_height_and_label(self):
        """空态：高度收缩到空态模板，标题计数归零"""
        self.pop.set_items([(self.lst, Card(title="A"))])
        self.pop.set_items([])
        self.assertEqual(self.pop.height(), _EMPTY_CHROME_H)
        self.assertEqual(_row_ids(self.pop), [])
        self.assertIn("0", self.pop._title_label.text())

    def test_done_count_row(self):
        """今日完成回顾行：N>0 显示、N=0 隐藏（含空态分支）

        空态（全部勾完）时完成行同样显示——庆祝时刻正是回顾入口。
        """
        card = Card(title="任务")
        self.pop.set_items([(self.lst, card)], done_count=3)
        self.assertFalse(self.pop._done_label.isHidden())
        self.assertIn("3", self.pop._done_label.text())
        self.pop.set_items([(self.lst, card)], done_count=0)
        self.assertTrue(self.pop._done_label.isHidden())
        self.pop.set_items([], done_count=5)
        self.assertFalse(self.pop._done_label.isHidden())
        self.assertIn("5", self.pop._done_label.text())
        self.pop.set_items([], done_count=0)
        self.assertTrue(self.pop._done_label.isHidden())


if __name__ == "__main__":
    import unittest
    unittest.main()


class TodayPopoverEnhanceTest(unittest.TestCase):
    """就地新建 / 关键词筛选 / 与看板一致的排序"""

    def setUp(self):
        self.pop = TodayPopover()
        self.lst = BoardList(title="待办")

    def tearDown(self):
        self.pop.close()
        self.pop.deleteLater()

    def _titles(self):
        return [row._title.text() for _lid, _cid, row in self.pop._rows]

    def test_sorted_by_priority_then_due(self):
        """优先级 → 星标 → 截止日：与看板「今日聚焦」同一把尺子"""
        from datetime import date, timedelta

        low = Card(title="低优", priority=3)
        high = Card(title="高优", priority=1)
        starred = Card(title="星标")
        starred.starred = True
        overdue = Card(title="逾期",
                       due_date=(date.today() - timedelta(days=2)).isoformat())
        self.pop.set_items([(self.lst, low), (self.lst, high),
                            (self.lst, starred), (self.lst, overdue)])
        # 有优先级的先按 1/2/3 排，其后是无优先级者按星标、再按截止日
        self.assertEqual(self._titles(),
                         ["高优", "低优", "星标", "逾期"])

    def test_search_filters_rows(self):
        a, b = Card(title="写周报"), Card(title="买牛奶")
        self.pop.set_items([(self.lst, a), (self.lst, b)])
        self.pop._search_edit.setText("周报")
        self.assertEqual(self._titles(), ["写周报"])
        self.pop._search_edit.setText("")
        self.assertEqual(len(self._titles()), 2)

    def test_search_matches_notes(self):
        card = Card(title="开会")
        card.notes = "带上投影仪"
        self.pop.set_items([(self.lst, card)])
        self.pop._search_edit.setText("投影")
        self.assertEqual(self._titles(), ["开会"])

    def test_search_keeps_done_count_line(self):
        """筛选不该把"今日已完成 N 张"这句回顾吞掉"""
        card = Card(title="唯一")
        self.pop.set_items([(self.lst, card)], done_count=3)
        self.pop._search_edit.setText("不存在")
        self.assertIsNotNone(self.pop._done_label)
        self.assertIn("3", self.pop._done_label.text())

    def test_search_no_match_shows_empty_state(self):
        self.pop.set_items([(self.lst, Card(title="A"))])
        self.pop._search_edit.setText("zzz")
        self.assertEqual(self._titles(), [])
        self.assertIsNotNone(self.pop._empty_label)

    def test_new_items_respect_active_search(self):
        """筛选态下新灌数据同样按词过滤（否则刷新会"漏"出不匹配的行）"""
        self.pop.set_items([])
        self.pop._search_edit.setText("周报")
        self.pop.set_items([(self.lst, Card(title="写周报")),
                            (self.lst, Card(title="买牛奶"))])
        self.assertEqual(self._titles(), ["写周报"])

    def test_quick_add_emits_text_and_clears(self):
        got = []
        self.pop.set_items([])          # 外壳控件随首次 set_items 一起建
        self.pop.signal_quick_add.connect(got.append)
        self.pop._add_edit.setText("  明天 交周报  ")
        self.pop._on_quick_add()
        self.assertEqual(got, ["明天 交周报"])
        # 输入由控制器在创建成功后清空：失败（如无列表）时要保留原文
        self.assertEqual(self.pop._add_edit.text(), "  明天 交周报  ")
        self.pop.clear_add_edit()
        self.assertEqual(self.pop._add_edit.text(), "")

    def test_quick_add_blank_is_ignored(self):
        got = []
        self.pop.set_items([])
        self.pop.signal_quick_add.connect(got.append)
        self.pop._add_edit.setText("   ")
        self.pop._on_quick_add()
        self.assertEqual(got, [])

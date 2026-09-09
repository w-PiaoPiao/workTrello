# -*- coding: utf-8 -*-
"""
看板视图增量刷新测试：列/卡控件按 id 复用、内容指纹跳过重建、
增删移后的顺序对齐、空列提示与列头状态

用法（离屏环境）：
    QT_QPA_PLATFORM=offscreen python tests/test_board_view.py
"""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QPushButton

_qapp = QApplication.instance() or QApplication([])

from app.models.board import BoardList, Card
from app.views.board_view import BoardView


def make_lists(spec):
    """[("列名", ["卡1", ...]), ...] → list[BoardList]"""
    lists = []
    for title, cards in spec:
        lst = BoardList(title=title)
        lst.cards = [Card(title=t) for t in cards]
        lists.append(lst)
    return lists


class BoardViewRefreshTest(unittest.TestCase):
    def setUp(self):
        self.view = BoardView()
        self.lists = make_lists([("待办", ["A", "B"]), ("进行中", ["C"])])
        self.view.refresh(self.lists)

    def tearDown(self):
        self.view.deleteLater()

    def widget_titles(self, col):
        return [cw.card().title for cw in col._card_widgets]

    def _flush_search(self):
        """手动触发搜索防抖计时器（离屏无事件循环，直接驱动到点逻辑）"""
        self.view._search_timer.timeout.emit()

    # ── 复用性 ────────────────────────────────────────────

    def test_same_data_reuses_widgets(self):
        col_ids0 = [id(c) for c in self.view._columns]
        card_ids0 = [id(cw) for col in self.view._columns
                     for cw in col._card_widgets]
        self.view.refresh(self.lists)
        self.assertEqual(col_ids0, [id(c) for c in self.view._columns])
        card_ids1 = [id(cw) for col in self.view._columns
                     for cw in col._card_widgets]
        self.assertEqual(card_ids0, card_ids1)

    def test_add_card_keeps_existing_widgets(self):
        old_ids = {id(cw) for cw in self.view._columns[0]._card_widgets}
        self.lists[0].cards.insert(0, Card(title="新卡"))
        self.view.refresh(self.lists)
        col = self.view._columns[0]
        self.assertEqual(self.widget_titles(col), ["新卡", "A", "B"])
        self.assertTrue(old_ids.issubset(
            {id(cw) for cw in col._card_widgets}))
        self.assertEqual(col._header._count_label.text(), "3")

    def test_remove_card_deletes_widget(self):
        old_ids = {id(cw) for cw in self.view._columns[0]._card_widgets}
        self.lists[0].cards = [c for c in self.lists[0].cards
                               if c.title != "B"]
        self.view.refresh(self.lists)
        col = self.view._columns[0]
        self.assertEqual(self.widget_titles(col), ["A"])
        self.assertIn(next(id(cw) for cw in col._card_widgets), old_ids)

    def test_move_card_reorders_widgets(self):
        self.lists[0].cards.reverse()
        self.view.refresh(self.lists)
        self.assertEqual(self.widget_titles(self.view._columns[0]),
                         ["B", "A"])

    # ── 内容指纹 ──────────────────────────────────────────

    def test_toggle_done_updates_changed_card_only(self):
        col = self.view._columns[0]
        cw_a, cw_b = col._card_widgets
        self.lists[0].cards[0].done = True
        self.view.refresh(self.lists)
        self.assertIs(col._card_widgets[0], cw_a)      # 壳复用
        self.assertTrue(cw_a._card.done)
        self.assertIn("line-through", cw_a._title_label.styleSheet())
        self.assertIs(col._card_widgets[1], cw_b)      # 未变卡不重建
        self.assertFalse(cw_b._card.done)

    def test_fingerprint_skips_rebuild(self):
        cw = self.view._columns[0]._card_widgets[0]
        # 同内容的新模型对象（模拟 reload）：不触发 rebuild
        rebuilds = []
        orig = cw.rebuild
        cw.rebuild = lambda: (rebuilds.append(1), orig())
        clone = Card(title="A", id=cw.card().id)
        cw.update_from_model(clone)
        self.assertEqual(rebuilds, [])
        self.assertIs(cw.card(), clone)
        # 内容变化：触发一次 rebuild
        clone.done = True
        cw.update_from_model(clone)
        self.assertEqual(rebuilds, [1])

    def test_rebuild_cleans_up_delete_button(self):
        """重复 rebuild 不残留绝对定位的旧删除按钮"""
        cw = self.view._columns[0]._card_widgets[0]
        first_btn = cw._delete_btn
        cw._card.done = not cw._card.done
        cw.update_from_model(cw._card)
        self.assertIsNot(cw._delete_btn, first_btn)
        self.assertNotIn(first_btn, cw.findChildren(QPushButton))

    # ── 模型对象重指向（备份恢复场景） ────────────────────

    def test_reload_repoints_new_model_objects(self):
        lists2 = [BoardList.from_dict(lst.to_dict()) for lst in self.lists]
        lists2[0].cards[0].done = True
        col_ids = [id(c) for c in self.view._columns]
        self.view.refresh(lists2)
        self.assertEqual(col_ids, [id(c) for c in self.view._columns])
        col = self.view._columns[0]
        self.assertIs(col._lst, lists2[0])
        self.assertIs(col._card_widgets[0].card(), lists2[0].cards[0])
        self.assertTrue(col._card_widgets[0]._card.done)

    # ── 列级增删与标题 ────────────────────────────────────

    def test_add_and_remove_list(self):
        new_lst = BoardList(title="新列")
        self.lists.append(new_lst)
        self.view.refresh(self.lists)
        self.assertEqual(len(self.view._columns), 3)
        self.assertEqual(self.view._columns[2].list_id(), new_lst.id)
        self.assertEqual(self.view._columns[2]._header._title_label.text(),
                         "新列")

        removed = self.lists.pop(0)
        self.view.refresh(self.lists)
        self.assertEqual(len(self.view._columns), 2)
        self.assertNotEqual(self.view._columns[0].list_id(), removed.id)

    def test_rename_list_updates_header(self):
        col = self.view._columns[0]
        self.lists[0].title = "改名了"
        self.view.refresh(self.lists)
        self.assertIs(self.view._columns[0], col)      # 列复用
        self.assertEqual(col._header._title_label.text(), "改名了")

    def test_empty_list_hint(self):
        self.lists[0].cards.clear()
        self.view.refresh(self.lists)
        col = self.view._columns[0]
        self.assertIsNotNone(col._hint)
        self.lists[0].cards.append(Card(title="回来了"))
        self.view.refresh(self.lists)
        self.assertIsNone(col._hint)
        self.assertEqual(self.widget_titles(col), ["回来了"])

    def test_all_lists_removed(self):
        self.view.refresh([])
        self.assertEqual(self.view._columns, [])
        self.assertEqual(self.view._lists, [])

    def test_stats(self):
        self.assertEqual(self.view._stats_label.text(),
                         "3 张卡片 · 完成 0")
        self.lists[0].cards[0].done = True
        self.view.refresh(self.lists)
        self.assertEqual(self.view._stats_label.text(),
                         "3 张卡片 · 完成 1")

    # ── 搜索过滤 ──────────────────────────────────────────

    def test_search_filters_columns_and_disables_drops(self):
        self.lists[0].cards.append(Card(title="ApplePie", notes="甜甜圈"))
        self.view.refresh(self.lists)
        self.view._search_edit.setText("apple")
        self._flush_search()
        col0, col1 = self.view._columns
        self.assertEqual([cw.card().title for cw in col0._card_widgets],
                         ["ApplePie"])
        self.assertFalse(col0.acceptDrops())      # 过滤态禁用拖放
        self.assertFalse(col1.acceptDrops())
        self.assertIsNotNone(col1._hint)
        self.assertIn("没有匹配", col1._hint.text())
        self.assertEqual(col0._header._count_label.text(), "1")

    def test_search_matches_notes_case_insensitive(self):
        self.lists[1].cards.append(Card(title="购物", notes="Buy Milk"))
        self.view.refresh(self.lists)
        self.view._search_edit.setText("MILK")
        self._flush_search()
        col1 = self.view._columns[1]
        self.assertEqual([cw.card().title for cw in col1._card_widgets],
                         ["购物"])

    def test_search_clear_restores_all(self):
        self.view._search_edit.setText("apple")
        self._flush_search()
        self.view._search_edit.clear()
        self._flush_search()
        col0, col1 = self.view._columns
        self.assertEqual([cw.card().title for cw in col0._card_widgets],
                         ["A", "B"])
        self.assertEqual([cw.card().title for cw in col1._card_widgets], ["C"])
        self.assertTrue(col0.acceptDrops())
        self.assertIsNone(col0._hint)

    def test_search_survives_data_refresh(self):
        self.view._search_edit.setText("a")
        self._flush_search()
        self.lists[0].cards.append(Card(title="Papaya"))
        self.view.refresh(self.lists)
        col0 = self.view._columns[0]
        self.assertEqual([cw.card().title for cw in col0._card_widgets],
                         ["A", "Papaya"])   # 新数据仍按当前关键词过滤

    def test_search_debounces_keystroke_bursts(self):
        """连续输入只重启计时器，停顿后仅触发一次过滤刷新"""
        self.view.refresh(make_lists([("待办", ["Apple", "Banana", "Avocado"])]))
        col0 = self.view._columns[0]
        calls = []
        orig = col0.refresh_cards
        col0.refresh_cards = lambda: (calls.append(1), orig())
        self.view._search_edit.setText("a")
        self.view._search_edit.setText("ap")
        self.view._search_edit.setText("app")
        self.assertEqual(calls, [])                   # 输入中未触发过滤
        self.assertTrue(self.view._search_timer.isActive())
        self.view._search_timer.timeout.emit()        # 停顿后只刷新一次
        self.assertEqual(calls, [1])
        self.assertEqual(self.widget_titles(col0), ["Apple"])

    # ── 今日聚焦 ──────────────────────────────────────────

    def test_today_filter(self):
        today = __import__("datetime").date.today()
        self.lists[0].cards.append(Card(title="星标卡", starred=True))
        self.lists[0].cards.append(Card(title="今天到期", due_date=today.isoformat()))
        self.lists[0].cards.append(Card(title="普通卡"))
        done_card = Card(title="已完成星标", starred=True, done=True)
        self.lists[0].cards.append(done_card)
        self.view.refresh(self.lists)
        self.view._today_btn.setChecked(True)
        col0, col1 = self.view._columns
        self.assertEqual([cw.card().title for cw in col0._card_widgets],
                         ["星标卡", "今天到期"])
        self.assertFalse(col0.acceptDrops())          # 过滤态禁用拖放
        self.assertIn("⭐ 今日 2", self.view._today_btn.text())

    def test_today_composes_with_search(self):
        today = __import__("datetime").date.today()
        self.lists[0].cards.append(Card(title="星标甲", starred=True))
        self.lists[0].cards.append(Card(title="星标乙", starred=True))
        self.view.refresh(self.lists)
        self.view._today_btn.setChecked(True)
        self.view._search_edit.setText("甲")
        self._flush_search()
        col0 = self.view._columns[0]
        self.assertEqual([cw.card().title for cw in col0._card_widgets],
                         ["星标甲"])

    def test_pomo_archive_signals_forward(self):
        received = {"pomo": None, "archive": None}
        self.view.signal_card_pomo.connect(
            lambda cid: received.__setitem__("pomo", cid))
        self.view.signal_card_archive.connect(
            lambda cid: received.__setitem__("archive", cid))
        cw = self.view._columns[0]._card_widgets[0]
        cw.signal_card_pomo.emit(cw.card().id)
        cw.signal_card_archive.emit(cw.card().id)
        self.assertEqual(received["pomo"], cw.card().id)
        self.assertEqual(received["archive"], cw.card().id)


if __name__ == "__main__":
    unittest.main(verbosity=2)

# -*- coding: utf-8 -*-
"""
归档对话框增强测试：搜索 / 排序 / 单卡彻底删除 / 清空归档

用法（离屏环境）：
    QT_QPA_PLATFORM=offscreen python tests/test_archive_dialog.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PET_BOARD_DATA_DIR"] = tempfile.mkdtemp()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

_qapp = QApplication.instance() or QApplication([])

from app.models.board import BoardList, Card
from app.views.archive_dialog import ArchiveDialog


def _items():
    """三张归档卡：标题/来源/完成态各不相同，便于验证三个排序维度"""
    la = BoardList(title="待办")
    lb = BoardList(title="进行中")
    return [
        (la, Card(title="周报", done=False)),
        (lb, Card(title="月报", done=True)),
        (la, Card(title="报销单", done=False, notes="打车费")),
    ]


class ArchiveDialogRenderTest(unittest.TestCase):
    def setUp(self):
        self.items = _items()      # 固定一份实例：重灌 set_items 时 id 不变
        self.dlg = ArchiveDialog()
        self.dlg.set_items(self.items, weekly_done=4)

    def tearDown(self):
        self.dlg.close()
        self.dlg.deleteLater()

    def _rendered(self):
        """当前渲染出的行标题（按显示顺序；去掉完成态前缀）"""
        out = []
        for i in range(self.dlg._rows_layout.count()):
            w = self.dlg._rows_layout.itemAt(i).widget()
            if w is not None and hasattr(w, "_title"):
                out.append(w._title._full.replace("\u2705 ", ""))
        return out

    def test_default_order_is_board_order(self):
        self.assertEqual(self._rendered(), ["周报", "月报", "报销单"])

    def test_stats_line(self):
        text = self.dlg._stats_label.text()
        self.assertIn("3", text)
        self.assertIn("4", text)

    def test_search_filters_by_title(self):
        self.dlg._search_edit.setText("报")
        self.assertEqual(len(self._rendered()), 3)
        self.dlg._search_edit.setText("周报")
        self.assertEqual(self._rendered(), ["周报"])

    def test_search_matches_notes_and_origin(self):
        self.dlg._search_edit.setText("打车")
        self.assertEqual(self._rendered(), ["报销单"])
        self.dlg._search_edit.setText("进行中")
        self.assertEqual(self._rendered(), ["月报"])

    def test_search_is_case_insensitive(self):
        self.items = [(BoardList(title="L"), Card(title="Report"))]
        self.dlg.set_items(self.items, 0)
        self.dlg._search_edit.setText("report")
        self.assertEqual(len(self._rendered()), 1)

    def test_search_stats_show_match_count(self):
        self.dlg._search_edit.setText("周报")
        text = self.dlg._stats_label.text()
        self.assertIn("1", text)
        self.assertIn("3", text)

    def test_empty_search_result_shows_empty_label(self):
        self.dlg._search_edit.setText("不存在的卡")
        self.assertEqual(self._rendered(), [])
        self.assertIsNotNone(self.dlg._empty_label)

    def test_sort_by_title_ascii_is_case_insensitive(self):
        """具体顺序只用 ASCII 标题断言：中文顺序取决于系统语言的排序规则"""
        self.items = [(BoardList(title="L"), Card(title=t))
                      for t in ("Gamma", "alpha", "Beta")]
        self.dlg.set_items(self.items, 0)
        self.dlg._sort_combo.setCurrentIndex(
            self.dlg._sort_combo.findData("title"))
        self.assertEqual(self._rendered(), ["alpha", "Beta", "Gamma"])

    def test_sort_by_title_keeps_all_items(self):
        self.dlg._sort_combo.setCurrentIndex(
            self.dlg._sort_combo.findData("title"))
        self.assertEqual(sorted(self._rendered()),
                         sorted(["周报", "月报", "报销单"]))

    def test_sort_by_origin_groups_same_list(self):
        """同来源列必须相邻（断言用 ASCII 列名：中文顺序随系统语言）"""
        lz = BoardList(title="Zeta")
        la = BoardList(title="Alpha")
        self.items = [(lz, Card(title="one")), (la, Card(title="two")),
                      (lz, Card(title="three"))]
        self.dlg.set_items(self.items, 0)
        self.dlg._sort_combo.setCurrentIndex(
            self.dlg._sort_combo.findData("origin"))
        # Alpha 组在前，组内保持原序（稳定排序）
        self.assertEqual(self._rendered(), ["two", "one", "three"])

    def test_sort_done_first(self):
        self.dlg._sort_combo.setCurrentIndex(
            self.dlg._sort_combo.findData("done_first"))
        self.assertEqual(self._rendered()[0], "月报")

    def test_search_and_sort_combine(self):
        self.dlg._search_edit.setText("报")
        self.dlg._sort_combo.setCurrentIndex(
            self.dlg._sort_combo.findData("done_first"))
        rows = self._rendered()
        self.assertEqual(sorted(rows), sorted(["周报", "月报", "报销单"]))
        self.assertEqual(rows[0], "月报")   # 已完成在前

    def test_search_state_survives_data_refresh(self):
        """控制器每次刷新都会重灌 set_items：搜索词不能被刷新冲掉"""
        self.dlg._search_edit.setText("周报")
        self.dlg.set_items(self.items, weekly_done=5)
        self.assertEqual(self._rendered(), ["周报"])

    def test_clear_button_disabled_without_items(self):
        self.dlg.set_items([], 0)
        self.assertFalse(self.dlg._clear_btn.isEnabled())
        self.dlg.set_items(self.items, 0)
        self.assertTrue(self.dlg._clear_btn.isEnabled())


class ArchiveDialogSignalTest(unittest.TestCase):
    def setUp(self):
        self.items = _items()
        self.dlg = ArchiveDialog()
        self.dlg.set_items(self.items, weekly_done=0)

    def tearDown(self):
        self.dlg.close()
        self.dlg.deleteLater()

    def _row_buttons(self, index: int):
        w = self.dlg._rows_layout.itemAt(index).widget()
        btns = w.findChildren(QPushButton)
        return btns[0], w._del_btn

    def test_restore_button_emits_restore(self):
        got = []
        self.dlg.signal_restore_requested.connect(got.append)
        restore, _del = self._row_buttons(0)
        restore.click()
        self.assertEqual(got, [self.items[0][1].id])

    def test_delete_button_emits_purge(self):
        got = []
        self.dlg.signal_purge_requested.connect(got.append)
        _restore, delete = self._row_buttons(0)
        delete.click()
        self.assertEqual(got, [self.items[0][1].id])

    def test_clear_asks_confirmation_then_emits(self):
        got = []
        self.dlg.signal_clear_requested.connect(lambda: got.append(True))
        with patch("app.views.archive_dialog.QMessageBox.question",
                   return_value=QMessageBox.Yes):
            self.dlg._on_clear_clicked()
        self.assertEqual(got, [True])

    def test_clear_declined_emits_nothing(self):
        got = []
        self.dlg.signal_clear_requested.connect(lambda: got.append(True))
        with patch("app.views.archive_dialog.QMessageBox.question",
                   return_value=QMessageBox.No):
            self.dlg._on_clear_clicked()
        self.assertEqual(got, [])

    def test_clear_without_items_does_not_ask(self):
        self.dlg.set_items([], 0)
        got = []
        self.dlg.signal_clear_requested.connect(lambda: got.append(True))
        with patch("app.views.archive_dialog.QMessageBox.question") as q:
            self.dlg._on_clear_clicked()
        q.assert_not_called()
        self.assertEqual(got, [])


class ArchivePurgeControllerTest(unittest.TestCase):
    """控制器：彻底删除 / 清空归档（含撤销）"""

    @classmethod
    def setUpClass(cls):
        from tests import shared_controller
        cls.c = shared_controller()

    def setUp(self):
        board = self.c._store.load()
        for lst in board.lists:
            lst.cards.clear()
        if not board.lists:
            board.lists.append(BoardList(title="待办"))
        # 直接摆好顺序（_on_card_add 是插到列首，连加两张会得到反序）
        board.lists[0].cards[:] = [Card(title="甲"), Card(title="乙")]
        self.c._after_data_change(None)
        self.c._undo_stack.clear()
        self.list_id = board.lists[0].id
        for card in self.c._store.load().lists[0].cards:
            card.archived = True          # 两张都归档，便于验证"只删指定"
        self.c._after_data_change(None)
        self.c._undo_stack.clear()

    def _cards(self):
        return self.c._store.load().lists[0].cards

    def _id_of(self, title: str) -> str:
        return next(c.id for c in self._cards() if c.title == title)

    def _archived_titles(self):
        return sorted(c.title for _l, c in self.c._store.load().archived_cards())

    def test_purge_removes_archived_card(self):
        self.c._on_card_purge(self._id_of("甲"))
        self.assertEqual([c.title for c in self._cards()], ["乙"])
        self.assertEqual(self._archived_titles(), ["乙"])

    def test_purge_is_undoable(self):
        self.c._on_card_purge(self._id_of("甲"))
        self.c._on_undo_requested()
        self.assertEqual([c.title for c in self._cards()], ["甲", "乙"])
        restored = next(c for c in self._cards() if c.title == "甲")
        self.assertTrue(restored.archived)   # 恢复的是"归档态"，不是回到看板

    def test_purge_ignores_non_archived_card(self):
        """看板上未归档的卡不走这条路（防误删：归档区只处理归档卡）"""
        board = self.c._store.load()
        board.lists[0].cards.append(Card(title="丙"))
        self.c._after_data_change(None)
        self.c._undo_stack.clear()
        self.c._on_card_purge(self._id_of("丙"))
        self.assertEqual([c.title for c in self._cards()], ["甲", "乙", "丙"])
        self.assertEqual(self.c._undo_stack, [])

    def test_purge_missing_card_is_noop(self):
        self.c._on_card_purge("不存在")
        self.assertEqual(self.c._undo_stack, [])

    def test_clear_removes_all_archived(self):
        self.c._on_archive_clear()
        self.assertEqual(self._cards(), [])
        self.assertEqual(self.c._store.load().archived_cards(), [])

    def test_clear_is_one_undo_step(self):
        self.c._on_archive_clear()
        self.c._on_undo_requested()
        self.assertEqual(self._archived_titles(), ["乙", "甲"])

    def test_clear_without_archive_is_noop(self):
        self.c._on_archive_clear()
        self.c._undo_stack.clear()
        self.c._on_archive_clear()
        self.assertEqual(self.c._undo_stack, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

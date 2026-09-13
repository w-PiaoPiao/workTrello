# -*- coding: utf-8 -*-
"""
控制器多看板与批量操作测试：重做、批量完成/移动/标签/删除/归档、
卡片复制、多看板切换/删除、Trello 与 Markdown 导入、日历改期、提醒提前量

需要 Qt 离屏环境；数据目录隔离到临时目录（在导入 app 模块前设置）。
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PET_BOARD_DATA_DIR"] = tempfile.mkdtemp()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

_qapp = QApplication.instance() or QApplication([])

from app.config import AppConfig
from app.controllers.app_controller import AppController
from app.models.board import Board, BoardStore, Card
from app.models.importers import board_from_trello
from app.models.workspace import Workspace


class ControllerMultiBoardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = AppController()
        cls.c._ensure_board_ui()

    def _list(self):
        return self.c._store.load().lists[0]

    def _reset(self):
        board = self.c._store.load()
        for lst in board.lists:
            lst.cards.clear()
        self.c._after_data_change(None)
        self.c._undo_stack.clear()
        self.c._redo_stack.clear()

    # ── 重做 ──────────────────────────────────────────────

    def test_undo_then_redo_restores_state(self):
        self._reset()
        self.c._on_card_add(self._list().id, "重做目标")
        self.assertEqual(self._titles(), ["重做目标"])
        self.c._on_undo_requested(False)
        self.assertEqual(self._titles(), [])
        self.c._on_redo_requested(False)
        self.assertEqual(self._titles(), ["重做目标"])
        # 重做后再次撤销仍可用（栈双向互通）
        self.c._on_undo_requested(False)
        self.assertEqual(self._titles(), [])
        self.c._redo_stack.clear()

    def test_new_mutation_clears_redo(self):
        self._reset()
        self.c._on_card_add(self._list().id, "A")
        self.c._on_undo_requested(False)
        self.assertEqual(len(self.c._redo_stack), 1)
        self.c._on_card_add(self._list().id, "B")
        self.assertEqual(self.c._redo_stack, [])
        self._reset()

    def test_redo_empty_stack_noop(self):
        self._reset()
        self.c._redo_stack.clear()
        self.c._on_redo_requested(True)   # 不应抛异常

    def _titles(self):
        return [x.title for x in self._list().cards]

    # ── 批量操作 ──────────────────────────────────────────

    def _add3(self):
        board = self.c._store.load()
        board.lists[0].cards += [Card(title="批1"), Card(title="批2"),
                                 Card(title="批3")]
        self.c._after_data_change(None)
        return [c.id for c in self._list().cards]

    def test_batch_done_toggles_all(self):
        self._reset()
        ids = self._add3()
        self.c._on_batch_done(ids, True)
        self.assertTrue(all(c.done for c in self._list().cards))
        self.c._on_undo_requested(False)
        self.assertFalse(any(c.done for c in self._list().cards))
        self._reset()

    def test_batch_move_to_target_list(self):
        self._reset()
        ids = self._add3()
        target = self.c._store.load().lists[1]
        self.c._on_batch_move(ids, target.id)
        board = self.c._store.load()
        self.assertEqual([c.title for c in board.lists[1].cards],
                         ["批1", "批2", "批3"])
        self.assertEqual(board.lists[0].cards, [])
        self._reset()

    def test_batch_label_and_delete(self):
        self._reset()
        ids = self._add3()
        self.c._on_batch_label(ids, "red")
        self.assertTrue(all("red" in c.labels for c in self._list().cards))
        self.c._on_batch_delete(ids)
        self.assertEqual(self._list().cards, [])
        # 撤销一次整批回滚
        self.c._on_undo_requested(False)
        self.assertEqual(len(self._list().cards), 3)
        self._reset()

    def test_batch_archive(self):
        self._reset()
        ids = self._add3()
        self.c._on_batch_archive(ids)
        self.assertTrue(all(c.archived for c in self._list().cards))
        self._reset()

    # ── 卡片复制 ──────────────────────────────────────────

    def test_duplicate_card_keeps_fields_new_id(self):
        self._reset()
        self.c._on_card_add(self._list().id, "原件")
        card = self._list().cards[0]
        card.checklist = [{"text": "子项", "done": True}]
        card.labels = ["blue"]
        self.c._after_data_change(None)
        self.c._on_card_duplicate(card.id)
        cards = self._list().cards
        self.assertEqual([c.title for c in cards], ["原件", "原件"])
        self.assertNotEqual(cards[1].id, cards[0].id)
        self.assertEqual(cards[1].checklist, cards[0].checklist)
        self.assertEqual(cards[1].labels, ["blue"])
        self._reset()

    # ── 多看板切换 / 删除 ─────────────────────────────────

    def test_board_switch_isolates_data_and_undo(self):
        self._reset()
        old_id = self.c._store_board_id
        self.c._on_card_add(self._list().id, "板A的卡")
        meta = self.c._workspace.create_board(name="板B",
                                              board=Board(name="板B"))
        self.c._activate_board(meta.id)
        self.assertEqual(self.c._store_board_id, meta.id)
        self.assertEqual(self.c._store.load().lists[0].cards, [])
        self.assertEqual(self.c._undo_stack, [])
        # 板 A 的数据还在
        self.c._activate_board(old_id)
        self.assertEqual(self._titles(), ["板A的卡"])
        self.c._workspace.delete_board(meta.id)
        self._reset()

    def test_board_delete_current_falls_back(self):
        self._reset()
        old_id = self.c._store_board_id
        meta = self.c._workspace.create_board(name="待删",
                                              board=Board(name="待删"))
        with patch.object(QMessageBox, "question",
                          return_value=QMessageBox.Yes):
            self.c._on_board_delete(meta.id)
        self.assertEqual(self.c._store_board_id, old_id)
        self.assertNotIn(meta.id, self.c._workspace.order())

    def test_board_delete_last_refused(self):
        only = self.c._store_board_id
        self.c._on_board_delete(only)   # 仅一块看板 → 无动作
        self.assertIn(only, self.c._workspace.order())

    # ── 导入 ──────────────────────────────────────────────

    def test_import_markdown_creates_and_switches(self):
        self._reset()
        old_id = self.c._store_board_id
        md = Path(tempfile.mkdtemp()) / "计划.md"
        md.write_text("## 待办\n- [ ] 导入卡A\n- [x] 导入卡B",
                      encoding="utf-8")
        with patch.object(QFileDialog, "getOpenFileName",
                          return_value=(str(md), "")):
            self.c._on_import_markdown()
        self.assertNotEqual(self.c._store_board_id, old_id)
        titles = [c.title for lst in self.c._store.load().lists
                  for c in lst.cards]
        self.assertEqual(titles, ["导入卡A", "导入卡B"])
        imported_id = self.c._store_board_id
        self.c._store = BoardStore(self.c._workspace.board_path(old_id))
        self.c._store_board_id = old_id
        self.c._workspace.set_current(old_id)
        self.c._workspace.delete_board(imported_id)
        self.c._apply_board_to_ui()
        self._reset()

    def test_import_trello_creates_board(self):
        self._reset()
        old_id = self.c._store_board_id
        doc = {"name": "T板", "lists": [{"id": "L1", "name": "列",
                                         "closed": False}],
               "cards": [{"id": "C1", "name": "特卡", "desc": "",
                          "idList": "L1", "closed": False, "labels": []}],
               "checklists": []}
        jf = Path(tempfile.mkdtemp()) / "trello.json"
        jf.write_text(json.dumps(doc), encoding="utf-8")
        with patch.object(QFileDialog, "getOpenFileName",
                          return_value=(str(jf), "")):
            self.c._on_import_trello()
        self.assertNotEqual(self.c._store_board_id, old_id)
        self.assertEqual(self.c._store.load().lists[0].cards[0].title, "特卡")
        imported_id = self.c._store_board_id
        self.c._store = BoardStore(self.c._workspace.board_path(old_id))
        self.c._store_board_id = old_id
        self.c._workspace.set_current(old_id)
        self.c._workspace.delete_board(imported_id)
        self.c._apply_board_to_ui()
        self._reset()

    def test_import_bad_file_shows_error(self):
        bad = Path(tempfile.mkdtemp()) / "bad.json"
        bad.write_text("{{{", encoding="utf-8")
        with patch.object(QFileDialog, "getOpenFileName",
                          return_value=(str(bad), "")), \
                patch.object(self.c, "_show_error") as err:
            self.c._on_import_trello()
        err.assert_called_once()

    # ── 日历改期 ──────────────────────────────────────────

    def test_calendar_due_change(self):
        self._reset()
        self.c._on_card_add(self._list().id, "改期卡")
        card_id = self._list().cards[0].id
        new_day = (date.today() + timedelta(days=5)).isoformat()
        self.c._on_calendar_due_change(card_id, new_day)
        self.assertEqual(self._list().cards[0].due_date, new_day)
        self.c._on_undo_requested(False)
        self.assertIsNone(self._list().cards[0].due_date)
        self._reset()

    # ── 提醒提前量 ────────────────────────────────────────

    def test_remind_advance_days_notifies_early(self):
        self._reset()
        with patch.object(AppConfig, "save_remind_advance") as save:
            save(2)
        future = (date.today() + timedelta(days=2)).isoformat()
        self.c._store.load().lists[0].cards.append(
            Card(title="提前提醒卡", due_date=future))
        self.c._after_data_change(None)
        with patch.object(AppConfig, "get_remind_advance", return_value=2), \
                patch.object(AppConfig, "save_remind_log"), \
                patch.object(type(self.c._tray), "show_notification") as note:
            self.c._check_due_dates()
        note.assert_called_once()
        self.assertIn("2 天后截止", note.call_args.args[0])
        self._reset()

    def test_remind_advance_zero_keeps_old_behavior(self):
        self._reset()
        future = (date.today() + timedelta(days=1)).isoformat()
        self.c._store.load().lists[0].cards.append(
            Card(title="明天截止卡", due_date=future))
        self.c._after_data_change(None)
        with patch.object(AppConfig, "get_remind_advance", return_value=0), \
                patch.object(AppConfig, "save_remind_log"), \
                patch.object(type(self.c._tray), "show_notification") as note:
            self.c._check_due_dates()
        note.assert_not_called()
        self._reset()


if __name__ == "__main__":
    unittest.main(verbosity=2)

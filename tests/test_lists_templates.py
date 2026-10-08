# -*- coding: utf-8 -*-
"""
复制列表与卡片模板测试：整列克隆、模板存取、按模板建卡、
"添加卡片"按钮在有无模板时的两条路径

用法（离屏环境）：
    QT_QPA_PLATFORM=offscreen python tests/test_lists_templates.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PET_BOARD_DATA_DIR"] = tempfile.mkdtemp()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

_qapp = QApplication.instance() or QApplication([])

from app.config import AppConfig
from app.models import templates as templates_mod
from app.models.board import BoardList, Card
from app.views import board_view as board_view_mod
from app.views.board_view import BoardView
from tests import capture_notifications, restore_notify


def _clear_templates():
    path = templates_mod.templates_path()
    if path.exists():
        path.unlink()


class TemplateStoreTest(unittest.TestCase):
    """模板文件读写（纯模型层）"""

    def setUp(self):
        _clear_templates()

    def tearDown(self):
        _clear_templates()

    def _card(self):
        card = Card(title="每周复盘")
        card.notes = "上周进展 / 本周计划"
        card.labels = ["blue"]
        card.priority = 2
        card.repeat = "weekly"
        card.starred = True
        card.checklist = [{"text": "看板", "done": False},
                          {"text": "周报", "done": True}]
        return card

    def test_save_and_load_roundtrip(self):
        self.assertTrue(templates_mod.save_template("复盘", self._card()))
        entries = templates_mod.load_templates()
        self.assertEqual([e["name"] for e in entries], ["复盘"])
        data = entries[0]["data"]
        self.assertEqual(data["notes"], "上周进展 / 本周计划")
        self.assertEqual(data["labels"], ["blue"])
        self.assertEqual(data["priority"], 2)
        self.assertEqual(data["repeat"], "weekly")
        self.assertEqual(len(data["checklist"]), 2)

    def test_same_name_overwrites(self):
        card = self._card()
        templates_mod.save_template("复盘", card)
        card.notes = "改过的内容"
        templates_mod.save_template("复盘", card)
        entries = templates_mod.load_templates()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["data"]["notes"], "改过的内容")

    def test_blank_name_rejected(self):
        self.assertFalse(templates_mod.save_template("   ", self._card()))
        self.assertEqual(templates_mod.load_templates(), [])

    def test_delete(self):
        templates_mod.save_template("A", self._card())
        templates_mod.save_template("B", self._card())
        self.assertTrue(templates_mod.delete_template("A"))
        self.assertEqual([e["name"] for e in templates_mod.load_templates()],
                         ["B"])
        self.assertFalse(templates_mod.delete_template("不存在"))

    def test_card_from_template_uses_new_identity(self):
        src = self._card()
        templates_mod.save_template("复盘", src)
        card = templates_mod.card_from_template("复盘", "本次复盘")
        self.assertIsNotNone(card)
        self.assertNotEqual(card.id, src.id)   # 新 id，不是原卡的引用
        self.assertEqual(card.title, "本次复盘")
        self.assertEqual(card.priority, 2)
        self.assertEqual(len(card.checklist), 2)
        self.assertTrue(card.starred)

    def test_template_excludes_attachments_and_due(self):
        """附件与截止日不进模板：附件文件会随孤儿回收失效，截止日是"那一次"的状态"""
        src = self._card()
        src.due_date = "2030-01-01"
        src.attachments = [{"id": "x", "name": "a.txt",
                            "path": "/tmp/a.txt", "is_image": False}]
        templates_mod.save_template("复盘", src)
        card = templates_mod.card_from_template("复盘", "新的")
        self.assertIsNone(card.due_date)
        self.assertEqual(card.attachments, [])

    def test_missing_template_returns_none(self):
        self.assertIsNone(templates_mod.card_from_template("没有这个", "标题"))

    def test_corrupt_file_yields_empty_list(self):
        templates_mod.templates_path().write_text("{ 坏掉的 json",
                                                 encoding="utf-8")
        self.assertEqual(templates_mod.load_templates(), [])

    def test_non_dict_entries_skipped(self):
        templates_mod.templates_path().write_text(
            json.dumps({"templates": ["坏", {"name": "好的", "data": {}}]}),
            encoding="utf-8")
        self.assertEqual([e["name"] for e in templates_mod.load_templates()],
                         ["好的"])


class AddCardButtonTest(unittest.TestCase):
    """列尾「+ 添加卡片」：无模板直接新建，有模板先弹菜单"""

    def setUp(self):
        _clear_templates()
        self.view = BoardView()
        self.lists = [BoardList(title="待办")]
        self.lists[0].cards = [Card(title="甲")]
        self.view.refresh(self.lists)
        self.col = self.view._columns[0]

    def tearDown(self):
        _clear_templates()
        self.view.deleteLater()

    def test_without_templates_creates_blank_card(self):
        got = []
        self.view.signal_card_add.connect(got.append)
        self.col._on_add_clicked()
        self.assertEqual(got, [self.lists[0].id])

    def test_with_templates_menu_offers_blank_and_template(self):
        templates_mod.save_template("复盘", Card(title="复盘"))
        shown = {}
        seen = []

        def fake(menu, pos):
            shown["items"] = [a.text() for a in menu.actions()
                              if a.menu() is None]
            seen.append(True)
            return menu.actions()[0]      # 选"空白卡片"

        got = []
        self.view.signal_card_add.connect(got.append)
        with patch.object(board_view_mod, "_popup_menu", fake):
            self.col._on_add_clicked()
        self.assertTrue(seen)
        self.assertIn("空白卡片", shown["items"])
        self.assertTrue(any("复盘" in t for t in shown["items"]))
        self.assertEqual(got, [self.lists[0].id])   # 选空白 → 走原路径

    def test_choosing_template_emits_template_signal(self):
        templates_mod.save_template("复盘", Card(title="复盘"))
        got = []
        self.view.signal_add_from_template.connect(
            lambda lid, name: got.append((lid, name)))
        with patch.object(board_view_mod, "_popup_menu",
                          lambda menu, pos: next(
                              a for a in menu.actions()
                              if "复盘" in a.text())):
            self.col._on_add_clicked()
        self.assertEqual(got, [(self.lists[0].id, "复盘")])

    def test_cancelled_menu_does_nothing(self):
        templates_mod.save_template("复盘", Card(title="复盘"))
        got = []
        self.view.signal_card_add.connect(got.append)
        with patch.object(board_view_mod, "_popup_menu",
                          lambda menu, pos: None):
            self.col._on_add_clicked()
        self.assertEqual(got, [])


class ListAndTemplateControllerTest(unittest.TestCase):
    """控制器：复制列表 / 另存为模板 / 按模板建卡"""

    @classmethod
    def setUpClass(cls):
        from tests import shared_controller
        cls.c = shared_controller()

    def setUp(self):
        _clear_templates()
        board = self.c._store.load()
        for lst in board.lists:
            lst.cards.clear()
        if not board.lists:
            board.lists.append(BoardList(title="待办"))
        board.lists[0].cards[:] = [Card(title="甲"), Card(title="乙")]
        board.lists[0].cards[0].priority = 1
        board.lists[0].cards[0].labels = ["red"]
        # created_at 是秒级时间戳：不显式拨旧，副本"新的创建时间"与原值
        # 会是同一秒，断言就验不出"副本不复用创建时间"这件事
        board.lists[0].cards[0].created_at = "2020-01-01T00:00:00"
        del board.lists[1:]
        self.c._after_data_change(None)
        self.c._undo_stack.clear()
        self.list_id = self.c._store.load().lists[0].id

    def tearDown(self):
        restore_notify(self.c)
        _clear_templates()

    def _lists(self):
        return self.c._store.load().lists

    def test_duplicate_list_inserts_after_source(self):
        self.c._on_list_duplicate(self.list_id)
        lists = self._lists()
        self.assertEqual(len(lists), 2)
        self.assertEqual(lists[1].title, lists[0].title + " 副本")
        self.assertEqual([c.title for c in lists[1].cards], ["甲", "乙"])

    def test_duplicate_list_cards_have_new_ids(self):
        self.c._on_list_duplicate(self.list_id)
        lists = self._lists()
        src_ids = {c.id for c in lists[0].cards}
        clone_ids = {c.id for c in lists[1].cards}
        self.assertFalse(src_ids & clone_ids)

    def test_duplicate_list_copies_fields_but_not_created_at(self):
        self.c._on_list_duplicate(self.list_id)
        lists = self._lists()
        clone = lists[1].cards[0]
        self.assertEqual(clone.priority, 1)
        self.assertEqual(clone.labels, ["red"])
        self.assertNotEqual(clone.created_at, lists[0].cards[0].created_at)

    def test_duplicate_list_is_one_undo_step(self):
        self.c._on_list_duplicate(self.list_id)
        self.c._on_undo_requested()
        self.assertEqual(len(self._lists()), 1)
        self.assertEqual([c.title for c in self._lists()[0].cards],
                         ["甲", "乙"])

    def test_duplicate_missing_list_is_noop(self):
        self.c._on_list_duplicate("不存在")
        self.assertEqual(self.c._undo_stack, [])

    def test_save_template_from_card(self):
        board = self.c._store.load()
        card_id = board.lists[0].cards[0].id
        with patch("app.views.quick_add_dialog.QuickAddDialog") as dlg:
            dlg.return_value.exec.return_value = 1
            dlg.return_value.text.return_value = "常用卡"
            self.c._on_card_save_template(card_id)
        self.assertEqual([e["name"] for e in templates_mod.load_templates()],
                         ["常用卡"])
        self.assertEqual(templates_mod.load_templates()[0]["data"]["priority"],
                         1)

    def test_save_template_cancel_writes_nothing(self):
        board = self.c._store.load()
        card_id = board.lists[0].cards[0].id
        with patch("app.views.quick_add_dialog.QuickAddDialog") as dlg:
            dlg.return_value.exec.return_value = 0
            self.c._on_card_save_template(card_id)
        self.assertEqual(templates_mod.load_templates(), [])

    def test_add_from_template_inserts_on_top(self):
        templates_mod.save_template("复盘", self._src_card())
        self.c._on_card_add_from_template(self.list_id, "复盘")
        cards = self._lists()[0].cards
        self.assertEqual(cards[0].title, "复盘")   # 模板名当初值标题
        self.assertEqual(cards[0].priority, 3)
        self.assertEqual([c.title for c in cards[1:]], ["甲", "乙"])

    def test_add_from_template_is_undoable(self):
        templates_mod.save_template("复盘", self._src_card())
        self.c._on_card_add_from_template(self.list_id, "复盘")
        self.c._on_undo_requested()
        self.assertEqual([c.title for c in self._lists()[0].cards],
                         ["甲", "乙"])

    def test_add_from_missing_template_notifies(self):
        notices = capture_notifications(self.c)
        self.c._on_card_add_from_template(self.list_id, "没有这个")
        self.assertEqual(len(self._lists()[0].cards), 2)
        self.assertEqual(self.c._undo_stack, [])
        self.assertTrue(notices)

    def _src_card(self):
        card = Card(title="模板卡")
        card.priority = 3
        return card


if __name__ == "__main__":
    unittest.main(verbosity=2)

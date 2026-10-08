# -*- coding: utf-8 -*-
"""
卡片快捷编辑测试：右键子菜单（优先级/截止日期/移动到列表）、
卡片键盘（1/2/3/0 设优先级、M 换列）、多选批量编辑对话框

用法（离屏环境）：
    QT_QPA_PLATFORM=offscreen python tests/test_quick_edit.py
"""

from __future__ import annotations

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

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu

_qapp = QApplication.instance() or QApplication([])

from app.config import AppConfig
from app.controllers.app_controller import _batch_rename
from app.models.board import BoardList, Card
from app.views import board_view as board_view_mod
from app.views.batch_edit_dialog import _NO_CHANGE, BatchEditDialog
from app.views.board_view import BoardView


def make_lists(spec):
    lists = []
    for title, cards in spec:
        lst = BoardList(title=title)
        lst.cards = [Card(title=t) for t in cards]
        lists.append(lst)
    return lists


def pick_in_menu(menu_text, item_pred):
    """_popup_menu 替身：在指定子菜单里找到匹配项并"选中"它

    真实菜单项在 exec() 返回后才派发，因此测试必须真的走一遍 exec 的
    调用点（patch 的是 _popup_menu 接缝，而不是 QMenu.exec——后者是
    Shiboken 槽，patch 类属性对它无效）。
    """
    def fake(menu, pos):
        for act in menu.actions():
            sub = act.menu()
            if sub is None or act.text() != menu_text:
                continue
            for item in sub.actions():
                if item_pred(item):
                    return item
        return None
    return fake


def pick_top_action(index=0):
    """_popup_menu 替身：直接选中顶层第 index 个菜单项"""
    def fake(menu, pos):
        acts = menu.actions()
        return acts[index] if 0 <= index < len(acts) else None
    return fake


class CardMenuQuickEditTest(unittest.TestCase):
    """右键菜单快捷编辑：菜单项 → 信号"""

    def setUp(self):
        self.view = BoardView()
        self.lists = make_lists([("待办", ["A", "B"]), ("进行中", ["C"])])
        self.view.refresh(self.lists)
        self.col_a = self.view._columns[0]
        self.cw = self.col_a._card_widgets[0]

    def tearDown(self):
        self.view.deleteLater()

    def test_priority_submenu_marks_current_and_emits(self):
        """优先级子菜单：当前值打勾；点「P1 高」发 (card_id, 1)"""
        self.cw.card().priority = 2
        got = []
        self.cw.signal_card_priority.connect(lambda cid, p: got.append((cid, p)))
        with patch.object(board_view_mod, "_popup_menu",
                          pick_in_menu("优先级",
                                       lambda a: a.text() == "P1 高")):
            self.cw._show_card_menu(self.cw.rect().center())
        self.assertEqual(got, [(self.cw.card().id, 1)])

    def test_priority_submenu_clear_entry(self):
        self.cw.card().priority = 1
        got = []
        self.cw.signal_card_priority.connect(lambda cid, p: got.append(p))
        with patch.object(board_view_mod, "_popup_menu",
                          pick_in_menu("优先级", lambda a: a.text() == "无")):
            self.cw._show_card_menu(self.cw.rect().center())
        self.assertEqual(got, [0])

    def test_due_submenu_presets(self):
        """截止日期子菜单：点「明天」发出明天的 ISO 日期"""
        got = []
        self.cw.signal_card_due_set.connect(lambda cid, iso: got.append(iso))
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        with patch.object(board_view_mod, "_popup_menu",
                          pick_in_menu("截止日期",
                                       lambda a: a.text() == "明天")):
            self.cw._show_card_menu(self.cw.rect().center())
        self.assertEqual(got, [tomorrow])

    def test_due_clear_absent_when_unset(self):
        """未设截止日时没有可点的「清除日期」（不做空操作）"""
        self.cw.card().due_date = None
        got = []
        self.cw.signal_card_due_set.connect(lambda cid, iso: got.append(iso))
        with patch.object(board_view_mod, "_popup_menu",
                          pick_in_menu("截止日期",
                                       lambda a: a.text() == "清除日期")):
            self.cw._show_card_menu(self.cw.rect().center())
        self.assertEqual(got, [])

    def test_due_clear_present_when_set(self):
        self.cw.card().due_date = date.today().isoformat()
        got = []
        self.cw.signal_card_due_set.connect(lambda cid, iso: got.append(iso))
        with patch.object(board_view_mod, "_popup_menu",
                          pick_in_menu("截止日期",
                                       lambda a: a.text() == "清除日期")):
            self.cw._show_card_menu(self.cw.rect().center())
        self.assertEqual(got, [""])

    def test_move_submenu_lists_other_lists_only(self):
        """移动到列表：列的是别的列，点选后按插到目标列顶部（index=0）"""
        got = []
        self.view.signal_card_move.connect(
            lambda cid, tid, idx: got.append((cid, tid, idx)))
        target_id = self.lists[1].id
        with patch.object(board_view_mod, "_popup_menu",
                          pick_in_menu("移动到列表",
                                       lambda a: a.text() == "进行中")):
            self.cw._show_card_menu(self.cw.rect().center())
        self.assertEqual(got, [(self.cw.card().id, target_id, 0)])

    def test_move_submenu_absent_with_single_list(self):
        """只有一列时不出现「移动到列表」（没有别的列可去）"""
        view = BoardView()
        lists = make_lists([("唯一列", ["X"])])
        view.refresh(lists)
        cw = view._columns[0]._card_widgets[0]
        got = []
        cw.signal_card_move_to.connect(lambda cid, tid: got.append(tid))
        shown = []
        with patch.object(board_view_mod, "_popup_menu",
                          lambda menu, pos: shown.append(True)):
            cw._show_move_menu()
        self.assertEqual(shown, [])   # 菜单根本没弹出
        self.assertEqual(got, [])
        view.deleteLater()

    def test_star_and_archive_still_work(self):
        """改造菜单后原有条目仍连通（回归护栏：exec 返回值比对路径）"""
        got = []
        self.cw.signal_card_star.connect(lambda cid: got.append(("star", cid)))
        with patch.object(board_view_mod, "_popup_menu", pick_top_action(0)):
            self.cw._show_card_menu(self.cw.rect().center())
        self.assertEqual(got, [("star", self.cw.card().id)])


class CardKeyboardQuickEditTest(unittest.TestCase):
    """卡片聚焦时的键盘快捷操作"""

    def setUp(self):
        self.view = BoardView()
        self.lists = make_lists([("待办", ["A", "B"]), ("进行中", ["C"])])
        self.view.refresh(self.lists)
        self.cw = self.view._columns[0]._card_widgets[0]

    def tearDown(self):
        self.view.deleteLater()

    def test_number_keys_set_priority(self):
        got = []
        self.cw.signal_card_priority.connect(lambda cid, p: got.append(p))
        for key, expected in ((Qt.Key_1, 1), (Qt.Key_2, 2), (Qt.Key_3, 3),
                              (Qt.Key_0, 0)):
            QTest.keyClick(self.cw, key)
            self.assertEqual(got[-1], expected)
        self.assertEqual(got, [1, 2, 3, 0])

    def test_m_key_opens_move_menu(self):
        """M 键换列：菜单点选后发移动到目标列"""
        got = []
        self.cw.signal_card_move_to.connect(lambda cid, tid: got.append(tid))
        with patch.object(board_view_mod, "_popup_menu",
                          pick_in_menu("移动到列表",
                                       lambda a: a.text() == "进行中")):
            QTest.keyClick(self.cw, Qt.Key_M)
        self.assertEqual(got, [self.lists[1].id])

    def test_m_key_without_other_list_is_noop(self):
        """只有一列时按 M 不弹菜单、不发信号"""
        view = BoardView()
        lists = make_lists([("唯一列", ["X"])])
        view.refresh(lists)
        cw = view._columns[0]._card_widgets[0]
        got = []
        cw.signal_card_move_to.connect(lambda cid, tid: got.append(tid))
        emitted = []
        with patch.object(board_view_mod, "_popup_menu",
                          lambda menu, pos: emitted.append(True)):
            QTest.keyClick(cw, Qt.Key_M)
        self.assertEqual(emitted, [])
        self.assertEqual(got, [])
        view.deleteLater()

    def test_space_and_enter_unchanged(self):
        """新增数字键分支不得吃掉原有空格/回车语义"""
        done, edited = [], []
        self.cw.signal_done_toggled.connect(lambda cid, d: done.append(d))
        self.cw.signal_edit_requested.connect(lambda c: edited.append(c))
        QTest.keyClick(self.cw, Qt.Key_Space)
        QTest.keyClick(self.cw, Qt.Key_Return)
        self.assertEqual(done, [True])
        self.assertEqual(len(edited), 1)


class BatchRenameHelperTest(unittest.TestCase):
    """批量改名纯函数"""

    def test_prefix_suffix(self):
        self.assertEqual(_batch_rename("周报", "", "[紧急] ", "prefix"),
                         "[紧急] 周报")
        self.assertEqual(_batch_rename("周报", "", "（v2）", "suffix"),
                         "周报（v2）")

    def test_replace(self):
        self.assertEqual(_batch_rename("9月周报", "9月", "10月", "replace"),
                         "10月周报")

    def test_empty_inputs_are_noop(self):
        """空 find / 空前缀 → 原样返回（不误改标题）"""
        self.assertEqual(_batch_rename("周报", "", "X", "replace"), "周报")
        self.assertEqual(_batch_rename("周报", "", "", "prefix"), "周报")

    def test_blank_result_falls_back_to_original(self):
        """整名替换成空白 → 保留原名（标题不可为空）"""
        self.assertEqual(_batch_rename("周报", "周报", "   ", "replace"),
                         "周报")


class BatchEditDialogTest(unittest.TestCase):
    """批量编辑对话框：只回传改动过的项"""

    def setUp(self):
        self.dlg = BatchEditDialog(3)

    def tearDown(self):
        self.dlg.deleteLater()

    def test_no_change_returns_empty(self):
        self.assertEqual(self.dlg.changes(), {})

    def test_priority_only(self):
        idx = self.dlg._priority_mode.findData("1")
        self.dlg._priority_mode.setCurrentIndex(idx)
        self.assertEqual(self.dlg.changes(), {"priority": 1})

    def test_priority_clear(self):
        idx = self.dlg._priority_mode.findData("0")
        self.dlg._priority_mode.setCurrentIndex(idx)
        self.assertEqual(self.dlg.changes(), {"priority": 0})

    def test_due_set_and_clear(self):
        self.dlg._due_mode.setCurrentIndex(
            self.dlg._due_mode.findData("set"))
        self.dlg._due_edit.setDate(
            self.dlg._due_edit.date().currentDate())
        self.assertIn("due", self.dlg.changes())
        self.assertTrue(self.dlg._due_edit.isEnabled())

        self.dlg._due_mode.setCurrentIndex(
            self.dlg._due_mode.findData(""))
        self.assertEqual(self.dlg.changes(), {"due": ""})
        self.assertFalse(self.dlg._due_edit.isEnabled())

    def test_quick_due_button_selects_set_mode(self):
        """快捷「明天」按钮：自动切到"设为指定日期"并填日期"""
        self.dlg._set_due_offset(1)
        self.assertEqual(self.dlg._due_mode.currentData(), "set")
        expected = (date.today() + timedelta(days=1)).isoformat()
        self.assertEqual(self.dlg.changes()["due"], expected)

    def test_next_monday_quick_button(self):
        self.dlg._set_due_offset(None)
        today = date.today()
        expected = today + timedelta(days=7 - today.weekday())
        self.assertEqual(self.dlg.changes()["due"], expected.isoformat())

    def test_rename_replace_shows_find_box(self):
        self.dlg._rename_mode.setCurrentIndex(
            self.dlg._rename_mode.findData("replace"))
        self.assertFalse(self.dlg._find_edit.isHidden())
        self.dlg._find_edit.setText("旧")
        self.dlg._repl_edit.setText("新")
        self.assertEqual(self.dlg.changes()["rename"],
                         {"mode": "replace", "find": "旧", "repl": "新"})

    def test_rename_prefix_hides_find_box(self):
        self.dlg._rename_mode.setCurrentIndex(
            self.dlg._rename_mode.findData("prefix"))
        self.assertTrue(self.dlg._find_edit.isHidden())
        self.dlg._repl_edit.setText("【未完成】")
        self.assertEqual(self.dlg.changes()["rename"]["mode"], "prefix")


class QuickEditControllerTest(unittest.TestCase):
    """控制器侧：快捷编辑落库 + 撤销 + 通知"""

    @classmethod
    def setUpClass(cls):
        from tests import shared_controller
        cls.c = shared_controller()

    def setUp(self):
        board = self.c._store.load()
        for lst in board.lists:
            lst.cards.clear()
        self.c._after_data_change(None)
        self.c._undo_stack.clear()
        self.list_id = self.c._store.load().lists[0].id
        self.c._on_card_add(self.list_id, "任务")
        self.card_id = self.c._store.load().lists[0].cards[0].id
        self.c._undo_stack.clear()

    def _card(self):
        _lst, card = self.c._store.load().find_card(self.card_id)
        return card

    def test_set_priority_pushes_undo(self):
        self.c._on_card_priority(self.card_id, 1)
        self.assertEqual(self._card().priority, 1)
        self.c._on_undo_requested()
        self.assertEqual(self._card().priority, 0)

    def test_same_priority_is_noop(self):
        self.c._on_card_priority(self.card_id, 2)
        self.c._undo_stack.clear()
        self.c._on_card_priority(self.card_id, 2)
        self.assertEqual(self.c._undo_stack, [])   # 不留空撤销步

    def test_set_and_clear_due(self):
        iso = date.today().isoformat()
        self.c._on_card_due_set(self.card_id, iso)
        self.assertEqual(self._card().due_date, iso)
        self.c._on_card_due_set(self.card_id, "")
        self.assertIsNone(self._card().due_date)

    def test_due_same_value_is_noop(self):
        self.c._on_card_due_set(self.card_id, "2030-01-01")
        self.c._undo_stack.clear()
        self.c._on_card_due_set(self.card_id, "2030-01-01")
        self.assertEqual(self.c._undo_stack, [])

    def test_batch_edit_applies_priority_and_rename(self):
        board = self.c._store.load()
        lst = board.lists[0]
        self.c._on_card_add(self.list_id, "周报")
        ids = [c.id for c in lst.cards]
        changes = {"priority": 3,
                   "rename": {"mode": "prefix", "find": "", "repl": "[月]"}}
        with patch("app.views.batch_edit_dialog.BatchEditDialog") as dlg:
            dlg.return_value.exec.return_value = 1   # QDialog.Accepted
            dlg.return_value.changes.return_value = changes
            self.c._on_batch_edit(ids)
        cards = self.c._store.load().lists[0].cards
        self.assertTrue(all(c.priority == 3 for c in cards))
        self.assertIn("[月]周报", [c.title for c in cards])
        self.c._on_undo_requested()
        self.assertFalse(any(c.priority == 3
                             for c in self.c._store.load().lists[0].cards))

    def test_batch_edit_cancel_changes_nothing(self):
        ids = [self.card_id]
        with patch("app.views.batch_edit_dialog.BatchEditDialog") as dlg:
            dlg.return_value.exec.return_value = 0   # Rejected
            dlg.return_value.changes.return_value = {"priority": 1}
            self.c._on_batch_edit(ids)
        self.assertEqual(self._card().priority, 0)
        self.assertEqual(self.c._undo_stack, [])

    def test_batch_edit_no_effective_change_leaves_no_undo(self):
        """对话框选了"设为高"，但卡片本来就是高 → 不算改动、不留撤销步"""
        self.c._on_card_priority(self.card_id, 1)
        self.c._undo_stack.clear()
        with patch("app.views.batch_edit_dialog.BatchEditDialog") as dlg:
            dlg.return_value.exec.return_value = 1
            dlg.return_value.changes.return_value = {"priority": 1}
            self.c._on_batch_edit([self.card_id])
        self.assertEqual(self.c._undo_stack, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

# -*- coding: utf-8 -*-
"""
捕获入口测试：看板内 Ctrl+V 粘贴建卡、外部文件拖到列建卡、
全局快捷键（序列映射 / 注册失败回滚，原生调用打桩）

用法（离屏环境）：
    QT_QPA_PLATFORM=offscreen python tests/test_capture.py
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

from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication

_qapp = QApplication.instance() or QApplication([])

from app.config import AppConfig
from app.models.board import BoardList
from app.services import global_hotkey as hotkey_mod
from tests import capture_notifications, restore_notify
from app.services.global_hotkey import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    MOD_SHIFT,
    GlobalHotkeyService,
    sequence_to_native,
)


class PasteToCreateTest(unittest.TestCase):
    """Ctrl+V 粘贴建卡"""

    @classmethod
    def setUpClass(cls):
        from tests import shared_controller
        cls.c = shared_controller()

    def setUp(self):
        # 本模块与其它测试模块共用同一份临时数据目录（进程内 DATA_DIR 只
        # 在首次导入 app.config 时确定一次），列的增减会跨模块可见
        self._ensure_lists()
        for lst in self.c._store.load().lists:
            lst.cards.clear()
        self.c._after_data_change(None)
        self.c._undo_stack.clear()
        self.list_id = self.c._store.load().lists[0].id
        self.clip = QApplication.clipboard()
        self._old_clip = self.clip.text()
        self.notices = capture_notifications(self.c)

    def _ensure_lists(self) -> None:
        """保证至少两列存在

        test_paste_without_lists 会清空整个 lists，而其它模块的用例直接
        索引 lists[1]（拖拽落点/资金泳道对照列）——本模块用完必须把列数
        恢复原状，否则会以"测试顺序"的形式污染后面的模块。
        """
        board = self.c._store.load()
        while len(board.lists) < 2:
            board.lists.append(BoardList(title=f"列{len(board.lists) + 1}"))
        self.c._after_data_change(None)

    def tearDown(self):
        restore_notify(self.c)
        self.clip.setText(self._old_clip or "")
        self._ensure_lists()

    def _titles(self):
        return [c.title for c in self.c._store.load().lists[0].cards]

    def test_multiline_paste_creates_cards_in_order(self):
        self.clip.setText("买牛奶\n写周报\n开会")
        self.c._on_paste_create()
        self.assertEqual(self._titles(), ["买牛奶", "写周报", "开会"])

    def test_paste_applies_quick_syntax(self):
        """每行走速记解析：!P1 / #红 / 明天 都生效"""
        self.clip.setText("交周报 !P1 #红 明天")
        self.c._on_paste_create()
        card = self.c._store.load().lists[0].cards[0]
        self.assertEqual(card.title, "交周报")
        self.assertEqual(card.priority, 1)
        self.assertEqual(card.labels, ["red"])
        self.assertIsNotNone(card.due_date)

    def test_paste_blank_lines_ignored(self):
        self.clip.setText("  第一行  \n\n\n第二行\n   ")
        self.c._on_paste_create()
        self.assertEqual(self._titles(), ["第一行", "第二行"])

    def test_paste_undo_is_single_step(self):
        """整批粘贴 = 一次撤销（与批量添加同语义）"""
        self.clip.setText("A\nB\nC")
        self.c._on_paste_create()
        self.assertEqual(len(self._titles()), 3)
        self.c._on_undo_requested()
        self.assertEqual(self._titles(), [])

    def test_empty_clipboard_notifies(self):
        self.clip.setText("")
        self.c._on_paste_create()
        self.assertEqual(self._titles(), [])
        self.assertEqual(self.c._undo_stack, [])   # 不留空撤销步
        self.assertTrue(self.notices)

    def test_whitespace_only_clipboard_notifies(self):
        self.clip.setText("   \n  \n")
        self.c._on_paste_create()
        self.assertEqual(self._titles(), [])
        self.assertTrue(self.notices)

    def test_paste_without_lists_notifies(self):
        board = self.c._store.load()
        board.lists.clear()
        self.c._after_data_change(None)
        self.clip.setText("无处安放")
        self.c._on_paste_create()
        self.assertEqual(self.c._undo_stack, [])
        self.assertTrue(self.notices)

    def test_paste_caps_line_count(self):
        self.clip.setText("\n".join(f"行{i}" for i in range(120)))
        self.c._on_paste_create()
        self.assertEqual(len(self._titles()), 100)

    def test_paste_respects_focused_list(self):
        """有焦点卡片时落到它所在的列，而不是首列"""
        self.c._on_card_add(self.list_id, "锚点")
        board = self.c._store.load()
        anchor = board.lists[0].cards[0]
        # 让 BoardView 报告"焦点在第二列"
        self.c._ensure_board_ui()
        second_id = board.lists[1].id
        with patch.object(self.c._board_view, "paste_target_list_id",
                          return_value=second_id):
            self.clip.setText("落到第二列")
            self.c._on_paste_create()
        second = self.c._store.load().find_list(second_id)
        self.assertEqual([c.title for c in second.cards], ["落到第二列"])


class FilesDroppedTest(unittest.TestCase):
    """拖外部文件到列 → 一文件一张卡 + 附件入库"""

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
        self.tmp = Path(tempfile.mkdtemp())
        self.notices = capture_notifications(self.c)

    def tearDown(self):
        restore_notify(self.c)

    def _mkfile(self, name: str, body: str = "x") -> str:
        p = self.tmp / name
        p.write_text(body, encoding="utf-8")
        return str(p)

    def test_one_card_per_file_with_attachment(self):
        a = self._mkfile("合同.txt")
        b = self._mkfile("截图.png")
        self.c._on_files_dropped(self.list_id, [a, b])
        cards = self.c._store.load().lists[0].cards
        self.assertEqual([c.title for c in cards], ["合同", "截图"])
        for card, src, is_img in ((cards[0], a, False), (cards[1], b, True)):
            self.assertEqual(len(card.attachments), 1)
            att = card.attachments[0]
            self.assertTrue(Path(att["path"]).is_file())
            self.assertNotEqual(att["path"], src)   # 已复制入库，不引用原路径
            self.assertEqual(att["is_image"], is_img)
            self.assertEqual(Path(att["path"]).read_text(encoding="utf-8"), "x")

    def test_attachment_lives_under_card_dir(self):
        path = self._mkfile("报表.csv")
        self.c._on_files_dropped(self.list_id, [path])
        card = self.c._store.load().lists[0].cards[0]
        att_path = Path(card.attachments[0]["path"])
        self.assertEqual(att_path.parent.name, card.id)
        self.assertEqual(att_path.parent.parent.name, "attachments")

    def test_undo_removes_created_cards(self):
        self.c._on_files_dropped(self.list_id, [self._mkfile("A.txt")])
        self.assertEqual(len(self.c._store.load().lists[0].cards), 1)
        self.c._on_undo_requested()
        self.assertEqual(self.c._store.load().lists[0].cards, [])

    def test_missing_file_is_skipped(self):
        gone = str(self.tmp / "已被删掉.txt")
        ok = self._mkfile("在的.txt")
        self.c._on_files_dropped(self.list_id, [gone, ok])
        cards = self.c._store.load().lists[0].cards
        self.assertEqual([c.title for c in cards], ["在的"])
        self.assertTrue(any("1" in n for n in self.notices))   # 提示未添加数

    def test_directory_is_ignored(self):
        """目录不作为卡片（挂目录的卡没有意义）"""
        d = self.tmp / "子目录"
        d.mkdir()
        self.c._on_files_dropped(self.list_id, [str(d)])
        self.assertEqual(self.c._store.load().lists[0].cards, [])
        self.assertEqual(self.c._undo_stack, [])

    def test_all_failures_leave_no_undo_step(self):
        self.c._on_files_dropped(self.list_id, [str(self.tmp / "没有.txt")])
        self.assertEqual(self.c._undo_stack, [])


class _FakeNative:
    """原生注册调用替身：记账并可控成败"""

    def __init__(self, ok: bool = True):
        self.ok = ok
        self.registered: list = []
        self.unregistered: list = []

    def register(self, hwnd, hotkey_id, mods, vk) -> bool:
        self.registered.append((hotkey_id, mods, vk))
        return self.ok

    def unregister(self, hwnd, hotkey_id) -> None:
        self.unregistered.append(hotkey_id)


@unittest.skipUnless(GlobalHotkeyService.is_available(), "全局热键仅 Windows")
class GlobalHotkeyServiceTest(unittest.TestCase):
    """注册/注销与失败回滚（原生调用全部打桩，不真占系统热键）"""

    def setUp(self):
        self.fake = _FakeNative()
        self._p1 = patch.object(hotkey_mod, "_native_register",
                                self.fake.register)
        self._p2 = patch.object(hotkey_mod, "_native_unregister",
                                self.fake.unregister)
        self._p1.start()
        self._p2.start()
        self.svc = GlobalHotkeyService()

    def tearDown(self):
        self.svc.dispose()
        self._p1.stop()
        self._p2.stop()

    def test_register_success(self):
        self.assertTrue(self.svc.register("Ctrl+Alt+N"))
        self.assertTrue(self.svc.is_registered())
        self.assertEqual(len(self.fake.registered), 1)

    def test_register_failure_reports_false(self):
        """组合被别的程序占用：返回 False 且不进入已注册态"""
        fake = _FakeNative(ok=False)
        with patch.object(hotkey_mod, "_native_register", fake.register):
            self.assertFalse(self.svc.register("Ctrl+Alt+N"))
        self.assertFalse(self.svc.is_registered())

    def test_unregister_calls_native(self):
        self.svc.register("Ctrl+Alt+N")
        self.svc.unregister()
        self.assertFalse(self.svc.is_registered())
        self.assertEqual(len(self.fake.unregistered), 1)

    def test_repeat_register_replaces_previous(self):
        self.svc.register("Ctrl+Alt+N")
        self.svc.register("Ctrl+Alt+M")
        self.assertTrue(self.svc.is_registered())
        self.assertEqual(len(self.fake.unregistered), 1)   # 先注销旧的

    def test_unsupported_sequence_rejected(self):
        """无修饰键 / 不支持的键：不注册（避免吞掉正常输入）"""
        self.assertFalse(self.svc.register("N"))
        self.assertFalse(self.svc.register("Ctrl+Alt+Left"))
        self.assertFalse(self.svc.is_registered())


class HotkeySequenceTest(unittest.TestCase):
    """QKeySequence → Win32 (mods, vk) 映射"""

    def test_default_sequence(self):
        native = sequence_to_native(
            QKeySequence(AppConfig.GLOBAL_HOTKEY_SEQUENCE))
        self.assertIsNotNone(native)
        mods, vk = native
        self.assertEqual(mods, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT)
        self.assertEqual(vk, 0x4E)   # 'N'

    def test_shift_and_letter(self):
        mods, vk = sequence_to_native(QKeySequence("Ctrl+Shift+A"))
        self.assertEqual(mods & MOD_SHIFT, MOD_SHIFT)
        self.assertEqual(vk, 0x41)   # 'A'

    def test_digit_and_function_keys(self):
        self.assertEqual(sequence_to_native(QKeySequence("Ctrl+Alt+7"))[1], 0x37)
        self.assertEqual(sequence_to_native(QKeySequence("Ctrl+Alt+F5"))[1], 0x74)

    def test_without_modifier_is_rejected(self):
        self.assertIsNone(sequence_to_native(QKeySequence("F5")))

    def test_unsupported_key_is_rejected(self):
        self.assertIsNone(sequence_to_native(QKeySequence("Ctrl+Alt+Up")))
        self.assertIsNone(sequence_to_native(QKeySequence("")))


class GlobalHotkeyToggleTest(unittest.TestCase):
    """设置页开关 → 控制器（失败回滚 + 不落盘）"""

    @classmethod
    def setUpClass(cls):
        from tests import shared_controller
        cls.c = shared_controller()

    def tearDown(self):
        restore_notify(self.c)
        self.c._hotkeys.unregister()
        AppConfig.save_global_hotkey(False)

    def test_enable_success_persists(self):
        with patch.object(self.c._hotkeys, "register", return_value=True):
            self.c._on_global_hotkey_toggled(True)
        self.assertTrue(AppConfig.get_global_hotkey())

    def test_enable_failure_rolls_back(self):
        """注册失败：偏好不落盘、开关回拨、给用户一句说明"""
        notices = capture_notifications(self.c)
        with patch.object(self.c._hotkeys, "register", return_value=False):
            self.c._on_global_hotkey_toggled(True)
        self.assertFalse(AppConfig.get_global_hotkey())
        self.assertTrue(notices)
        self.assertFalse(self.c._hotkeys.is_registered())

    def test_disable_unregisters(self):
        AppConfig.save_global_hotkey(True)
        self.c._on_global_hotkey_toggled(False)
        self.assertFalse(AppConfig.get_global_hotkey())
        self.assertFalse(self.c._hotkeys.is_registered())


if __name__ == "__main__":
    unittest.main(verbosity=2)

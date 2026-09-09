# -*- coding: utf-8 -*-
"""
卡片编辑对话框测试：截止日期可选（新建默认无日期/双向切换）、
备注内快速插入当前时间。

用法：QT_QPA_PLATFORM=offscreen python tests/test_card_dialog.py
"""

import os
import re
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 数据目录隔离：单文件 / discover / pytest 运行方式下
# 都不得读写真实用户数据（防止全量回归清空 %LOCALAPPDATA% 看板）
os.environ["PET_BOARD_DATA_DIR"] = tempfile.mkdtemp()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QApplication

_qapp = QApplication.instance() or QApplication([])

from app.models.board import Card
from app.views.card_dialog import CardDialog


class DueDateOptionalTest(unittest.TestCase):
    """截止日期可选：新建默认无日期，编辑有日期的卡回填，可双向切换"""

    def test_new_card_defaults_to_no_due(self):
        """新建卡片（card=None）：默认"未设置"（灰字标签而非日期框），提交 due_date=None"""
        dlg = CardDialog(None)
        self.assertTrue(dlg._due_cleared)
        self.assertTrue(dlg._due_none_label.isVisibleTo(dlg))
        self.assertFalse(dlg._due_edit.isVisibleTo(dlg))
        self.assertEqual(dlg._due_toggle_btn.text(), "设置日期")
        self.assertIsNone(dlg.result_card()["due_date"])
        dlg.deleteLater()

    def test_new_card_can_set_due(self):
        """新建默认无日期，点「设置日期」后出现日期框可选择日期并提交"""
        dlg = CardDialog(None)
        dlg._on_toggle_due()
        self.assertFalse(dlg._due_cleared)
        self.assertTrue(dlg._due_edit.isVisibleTo(dlg))
        self.assertFalse(dlg._due_none_label.isVisibleTo(dlg))
        self.assertEqual(dlg._due_toggle_btn.text(), "清除日期")
        dlg._due_edit.setDate(QDate(2026, 9, 30))
        self.assertEqual(dlg.result_card()["due_date"], "2026-09-30")
        dlg.deleteLater()

    def test_edit_existing_due_card_preserves_value(self):
        """编辑已有截止日期的卡：回填、非 cleared、提交保留原日期"""
        card = Card(title="x", due_date="2026-09-10")
        dlg = CardDialog(card)
        self.assertFalse(dlg._due_cleared)
        self.assertTrue(dlg._due_edit.isVisibleTo(dlg))
        self.assertEqual(dlg._due_edit.date().toString("yyyy-MM-dd"),
                         "2026-09-10")
        self.assertEqual(dlg._due_toggle_btn.text(), "清除日期")
        self.assertEqual(dlg.result_card()["due_date"], "2026-09-10")
        dlg.deleteLater()

    def test_clear_and_reset_roundtrip(self):
        """编辑有日期卡片：清除后显示"未设置"，再设置可改日期（双向不锁死）"""
        card = Card(title="x", due_date="2026-09-10")
        dlg = CardDialog(card)
        dlg._on_toggle_due()                 # 清除
        self.assertTrue(dlg._due_cleared)
        self.assertTrue(dlg._due_none_label.isVisibleTo(dlg))
        self.assertFalse(dlg._due_edit.isVisibleTo(dlg))
        self.assertIsNone(dlg.result_card()["due_date"])
        dlg._on_toggle_due()                 # 恢复设置
        self.assertFalse(dlg._due_cleared)
        dlg._due_edit.setDate(QDate(2026, 10, 1))
        self.assertEqual(dlg.result_card()["due_date"], "2026-10-01")
        dlg.deleteLater()


class InsertTimeTest(unittest.TestCase):
    """备注内快速插入当前时间（MM-DD HH:MM 紧凑格式）"""

    def test_insert_time_at_cursor(self):
        dlg = CardDialog(None)
        dlg._notes_edit.setPlainText("进度：\n")
        # 光标移到第二行开头
        cur = dlg._notes_edit.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        dlg._notes_edit.setTextCursor(cur)
        dlg._notes_edit.textCursor().insertText("")  # 确保光标在末尾

        dlg._on_insert_time()
        text = dlg._notes_edit.toPlainText()
        # 格式为 MM-DD HH:MM
        stamp = text.splitlines()[-1].strip()
        self.assertRegex(stamp, r"^\d{2}-\d{2} \d{2}:\d{2}$")
        # 与当前时间一致（同分钟）
        now = datetime.now().strftime("%m-%d %H:%M")
        self.assertEqual(stamp, now)
        dlg.deleteLater()

    def test_insert_time_replaces_selection(self):
        dlg = CardDialog(None)
        dlg._notes_edit.setPlainText("旧文本")
        cur = dlg._notes_edit.textCursor()
        cur.select(cur.SelectionType.Document)
        dlg._notes_edit.setTextCursor(cur)
        dlg._on_insert_time()
        text = dlg._notes_edit.toPlainText().strip()
        self.assertRegex(text, r"^\d{2}-\d{2} \d{2}:\d{2}$")
        dlg.deleteLater()

    def test_insert_button_visible_in_dialog(self):
        dlg = CardDialog(None)
        self.assertIsNotNone(dlg._insert_time_btn)
        self.assertIn("插入当前时间", dlg._insert_time_btn.text())
        dlg.deleteLater()


class KeyboardSaveTest(unittest.TestCase):
    """键盘保存：Ctrl+Return 直接保存（多行备注里 Enter 只换行）；打开自动聚焦标题"""

    def test_show_focuses_title_and_selects_all(self):
        dlg = CardDialog(Card(title="旧标题"))
        dlg.show()
        _qapp.processEvents()
        self.assertTrue(dlg._title_edit.hasFocus())
        self.assertEqual(dlg._title_edit.selectedText(), "旧标题")
        dlg.close()
        dlg.deleteLater()

    def test_ctrl_return_saves(self):
        dlg = CardDialog(None)
        dlg._title_edit.setText("回车保存")
        dlg._save_shortcut.activated.emit()
        self.assertEqual(dlg.result(), CardDialog.Accepted)
        self.assertEqual(dlg.result_card()["title"], "回车保存")
        dlg.deleteLater()

    def test_ctrl_return_rejects_empty_title(self):
        dlg = CardDialog(None)
        dlg._title_edit.setText("   ")
        dlg.show()
        _qapp.processEvents()
        dlg._save_shortcut.activated.emit()
        self.assertNotEqual(dlg.result(), CardDialog.Accepted)
        self.assertTrue(dlg.isVisible())      # 校验拦截：对话框未关闭
        self.assertTrue(dlg._title_edit.hasFocus())   # 焦点回到标题框
        dlg.close()
        dlg.deleteLater()

    @unittest.skipUnless(sys.platform == "darwin", "仅 macOS 注册 ⌘+Return")
    def test_mac_meta_return_shortcut_registered(self):
        dlg = CardDialog(None)
        self.assertIsNotNone(dlg._mac_save_shortcut)
        dlg.deleteLater()


if __name__ == "__main__":
    unittest.main(verbosity=2)

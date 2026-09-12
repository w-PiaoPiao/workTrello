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
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 数据目录隔离：单文件 / discover / pytest 运行方式下
# 都不得读写真实用户数据（防止全量回归清空 %LOCALAPPDATA% 看板）
os.environ["PET_BOARD_DATA_DIR"] = tempfile.mkdtemp()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QDate, QSize, Qt
from PySide6.QtWidgets import QApplication

_qapp = QApplication.instance() or QApplication([])

from app.config import AppConfig
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


class WorkDirOptionalTest(unittest.TestCase):
    """工作目录可选：新建默认未设置，编辑回填，浏览/清除，提交进 result_card"""

    def test_new_card_defaults_to_empty(self):
        """新建卡片：默认"未设置"，清除按钮隐藏，提交 workdir 为空串"""
        dlg = CardDialog(None)
        self.assertEqual(dlg._workdir, "")
        self.assertEqual(dlg._workdir_label.text(), "未设置")
        self.assertFalse(dlg._workdir_clear_btn.isVisibleTo(dlg))
        self.assertEqual(dlg.result_card()["workdir"], "")
        dlg.deleteLater()

    def test_edit_existing_card_backfills_path(self):
        """编辑已有目录的卡：回填路径 + tooltip 存全路径，提交保留"""
        card = Card(title="x", workdir="/Volumes/SSD/项目")
        dlg = CardDialog(card)
        self.assertEqual(dlg._workdir, "/Volumes/SSD/项目")
        self.assertIn("项目", dlg._workdir_label.text())   # 路径已显示（可能带省略）
        self.assertEqual(dlg._workdir_label.toolTip(), "/Volumes/SSD/项目")
        self.assertTrue(dlg._workdir_clear_btn.isVisibleTo(dlg))
        self.assertEqual(dlg.result_card()["workdir"], "/Volumes/SSD/项目")
        dlg.deleteLater()

    def test_clear_resets_to_not_set(self):
        """清除后回到"未设置"态，提交空串"""
        card = Card(title="x", workdir="/tmp/proj")
        dlg = CardDialog(card)
        dlg._on_clear_workdir()
        self.assertEqual(dlg._workdir, "")
        self.assertEqual(dlg._workdir_label.text(), "未设置")
        self.assertFalse(dlg._workdir_clear_btn.isVisibleTo(dlg))
        self.assertEqual(dlg.result_card()["workdir"], "")
        dlg.deleteLater()

    def test_browse_sets_path(self):
        """「浏览…」选中目录即写入状态（QFileDialog mock 掉）"""
        with patch("app.views.card_dialog.QFileDialog") as fd:
            fd.getExistingDirectory.return_value = "/tmp/picked"
            dlg = CardDialog(None)
            dlg._on_browse_workdir()
        self.assertEqual(dlg._workdir, "/tmp/picked")
        self.assertTrue(dlg._workdir_clear_btn.isVisibleTo(dlg))
        self.assertEqual(dlg.result_card()["workdir"], "/tmp/picked")
        dlg.deleteLater()

    def test_browse_cancelled_keeps_state(self):
        """浏览取消（返回空串）不改动现有状态"""
        card = Card(title="x", workdir="/tmp/proj")
        dlg = CardDialog(card)
        with patch("app.views.card_dialog.QFileDialog") as fd:
            fd.getExistingDirectory.return_value = ""
            dlg._on_browse_workdir()
        self.assertEqual(dlg._workdir, "/tmp/proj")
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


class DialogSizeTest(unittest.TestCase):
    """对话框尺寸：宽高均可调整，并在关闭后记住上次值

    回归背景：宽度曾被 setFixedWidth(420) 锁死（最大宽=最小宽），
    只能纵向拉伸、不能横向拉宽。这里锁死"宽度可自由调整 + 尺寸被记住"。
    """

    def setUp(self):
        AppConfig.save_card_dialog_size(None)   # 清掉上次记录，从默认值起测

    def tearDown(self):
        AppConfig.save_card_dialog_size(None)

    def test_width_is_not_locked(self):
        """宽度可自由调整：多个宽度都应生效（修复前恒定 420）"""
        dlg = CardDialog(None)
        for w in (600, 480, 900):
            dlg.resize(w, 500)
            self.assertEqual(dlg.width(), w,
                             f"请求宽 {w} 被夹成 {dlg.width()}，宽度仍被写死")
        dlg.deleteLater()

    def test_max_width_not_constrained(self):
        """最大宽度未被约束（此前 maximumWidth == 420）"""
        dlg = CardDialog(None)
        self.assertGreater(dlg.maximumWidth(), AppConfig.CARD_DIALOG_WIDTH)
        dlg.deleteLater()

    def test_default_size_when_no_record(self):
        """无历史记录时用默认宽高，且不低于最小尺寸"""
        dlg = CardDialog(None)
        self.assertEqual(dlg.size(),
                         QSize(AppConfig.CARD_DIALOG_WIDTH,
                               AppConfig.CARD_DIALOG_HEIGHT))
        self.assertGreaterEqual(dlg.width(), AppConfig.CARD_DIALOG_MIN_WIDTH)
        self.assertGreaterEqual(dlg.height(), AppConfig.CARD_DIALOG_MIN_HEIGHT)
        dlg.deleteLater()

    def test_min_size_enforced(self):
        """过小的尺寸请求被最小尺寸兜住（布局不被压垮）"""
        dlg = CardDialog(None)
        dlg.resize(50, 50)
        self.assertGreaterEqual(dlg.width(), AppConfig.CARD_DIALOG_MIN_WIDTH)
        self.assertGreaterEqual(dlg.height(), AppConfig.CARD_DIALOG_MIN_HEIGHT)
        dlg.deleteLater()

    def test_size_saved_on_accept_and_reused(self):
        """确定关闭：记住尺寸；下次打开沿用"""
        dlg = CardDialog(None)
        dlg.resize(760, 640)
        dlg.accept()
        self.assertEqual(AppConfig.get_card_dialog_size(), QSize(760, 640))
        again = CardDialog(None)
        self.assertEqual(again.size(), QSize(760, 640))
        again.deleteLater()

    def test_size_saved_on_cancel(self):
        """取消关闭：同样记住尺寸"""
        dlg = CardDialog(None)
        dlg.resize(820, 560)
        dlg.reject()
        self.assertEqual(AppConfig.get_card_dialog_size(), QSize(820, 560))

    def test_size_saved_on_window_close(self):
        """X 按钮关闭（closeEvent → reject）：同样记住尺寸"""
        dlg = CardDialog(None)
        dlg.show()
        _qapp.processEvents()
        dlg.resize(880, 620)
        _qapp.processEvents()
        dlg.close()
        _qapp.processEvents()
        self.assertEqual(AppConfig.get_card_dialog_size(), QSize(880, 620))

    def test_saved_size_clamped_to_screen(self):
        """历史尺寸大于当前屏幕可用区时夹进屏内（换小屏/拔外接屏后不越界）"""
        huge = QApplication.primaryScreen().availableGeometry()
        AppConfig.save_card_dialog_size(
            QSize(huge.width() + 500, huge.height() + 500))
        dlg = CardDialog(None)
        self.assertLessEqual(dlg.width(), huge.width())
        self.assertLessEqual(dlg.height(), huge.height())
        dlg.deleteLater()


if __name__ == "__main__":
    unittest.main(verbosity=2)

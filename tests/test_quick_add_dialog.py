# -*- coding: utf-8 -*-
"""快速添加对话框测试：速记实时预览、批量对话框行数与列表选择

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

from app.views.quick_add_dialog import BulkAddDialog, QuickAddDialog, _fmt_fields


class FmtFieldsTest(unittest.TestCase):
    def test_full_fields(self):
        s = _fmt_fields({"due_date": "2026-09-13", "priority": 1,
                         "labels": ["red"]})
        self.assertEqual(s, "截止 9/13 · P1 · 标签 红色")

    def test_empty(self):
        self.assertEqual(_fmt_fields({}), "")


class QuickAddDialogTest(unittest.TestCase):
    def test_syntax_preview_hit_and_miss(self):
        dlg = QuickAddDialog("t", "l", "p", syntax_preview=True)
        dlg._update_preview("明天 买菜")
        self.assertFalse(dlg._preview.isHidden())
        self.assertIn("截止", dlg._preview.text())
        dlg._update_preview("买菜")
        self.assertTrue(dlg._preview.isHidden())

    def test_no_preview_mode(self):
        dlg = QuickAddDialog("t", "l", "p", syntax_preview=False)
        dlg._update_preview("明天 买菜")
        self.assertTrue(dlg._preview.isHidden())

    def test_text(self):
        dlg = QuickAddDialog("t", "l", "p")
        dlg._edit.setText("  带空格  ")
        self.assertEqual(dlg.text(), "  带空格  ")


class BulkAddDialogTest(unittest.TestCase):
    def test_prefill_and_count(self):
        dlg = BulkAddDialog(["待办", "进行中"], prefill="第一行\n第二行\n\n")
        self.assertIn("2 行", dlg._count_label.text())

    def test_list_index(self):
        dlg = BulkAddDialog(["待办", "进行中"])
        self.assertEqual(dlg.list_index(), 0)
        dlg._combo.setCurrentIndex(1)
        self.assertEqual(dlg.list_index(), 1)

    def test_text(self):
        dlg = BulkAddDialog(["待办"])
        dlg._edit.setPlainText("a\nb")
        self.assertEqual(dlg.text(), "a\nb")


if __name__ == "__main__":
    unittest.main()

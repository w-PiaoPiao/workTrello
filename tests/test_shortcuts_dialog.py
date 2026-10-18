# -*- coding: utf-8 -*-
"""快捷键速查面板测试：双分区、手势条目、`?` 提示

面板是"可发现性的总闸门"：鼠标手势此前只散落在各处 tooltip 里，面板一行
都没有。这里锁住结构——两个分区都在、手势条目不少于 11 条、`?` 提示在位、
键盘那 15 行一条不少且按作用域分成三小节。

用法（离屏环境）：
    QT_QPA_PLATFORM=offscreen python tests/test_shortcuts_dialog.py
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PET_BOARD_DATA_DIR"] = tempfile.mkdtemp()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QFrame, QLabel, QScrollArea

_qapp = QApplication.instance() or QApplication([])

from app import i18n                                     # noqa: E402
from app.views.shortcuts_dialog import (                 # noqa: E402
    ShortcutsDialog,
    _gesture_rows,
    _keyboard_sections,
)

# 键盘区原有 15 行：面板补手势时不能把它们弄丢
_KEYBOARD_ROWS = 15
# 审计列出的纯鼠标手势条数下限
_MIN_GESTURES = 11


class ShortcutsDialogTest(unittest.TestCase):
    def setUp(self):
        i18n.set_lang("zh")
        self.dlg = ShortcutsDialog()
        self.dlg.show()
        _qapp.processEvents()

    def tearDown(self):
        self.dlg.hide()
        self.dlg.deleteLater()
        i18n.set_lang("zh")

    # ── 结构 ──────────────────────────────────────────────

    def test_two_sections_exist(self):
        """键盘 / 鼠标与手势 两个分区都要在"""
        sections = [lab.text() for lab in self.dlg.findChildren(QLabel)
                    if lab.objectName() == "scSection"]
        self.assertEqual(sections, [i18n.tr("键盘"), i18n.tr("鼠标与手势")])

    def test_keyboard_rows_kept_and_grouped_by_scope(self):
        """原有 15 行键盘快捷键一条不少，且按作用域分三小节"""
        groups = _keyboard_sections()
        self.assertEqual([title for title, _rows in groups],
                         [i18n.tr("全局"), i18n.tr("卡片上（先点一下卡片）"),
                          i18n.tr("卡片对话框")])
        self.assertEqual(sum(len(rows) for _title, rows in groups),
                         _KEYBOARD_ROWS)

    def test_gesture_rows_cover_the_audit_list(self):
        """鼠标手势至少 11 条，且每条的"怎么做"都写清楚了"""
        rows = _gesture_rows()
        self.assertGreaterEqual(len(rows), _MIN_GESTURES)
        for action, gesture in rows:
            self.assertTrue(action.strip(), "手势条目缺'会发生什么'")
            self.assertTrue(gesture.strip(), "手势条目缺'怎么做'")

    def test_gesture_rows_spot_check(self):
        """抽查几条最容易被漏掉的手势"""
        text = " ".join(f"{a} {g}" for a, g in _gesture_rows())
        for needle in (i18n.tr("点卡片左缘色条"),      # 标签过滤
                       i18n.tr("拖卡片到工具栏按钮"),   # 拖到归档/今日
                       i18n.tr("从系统拖文件进列表"),   # 拖文件建卡
                       i18n.tr("拖列头 / 列内空白"),    # 搬整列
                       i18n.tr("拖看板空白处"),         # 搬窗口
                       i18n.tr("悬停卡片"),             # 悬停出 ✕
                       i18n.tr("悬停「有备注」徽章")):  # 备注预览
            self.assertIn(needle, text)

    def test_question_mark_hint_present(self):
        """底部必须有"按 ? 呼出本面板"——不知道 ? 就永远学不到这些"""
        self.assertIn("?", self.dlg._hint.text())

    def test_all_rows_rendered(self):
        rows = self.dlg.findChildren(QFrame, "scRow")
        expected = _KEYBOARD_ROWS + len(_gesture_rows())
        self.assertEqual(len(rows), expected)

    def test_body_is_scrollable(self):
        """内容 26+ 行：必须能滚，否则小屏上后半截直接不可见"""
        scroll = self.dlg.findChild(QScrollArea, "scScroll")
        self.assertIsNotNone(scroll)
        self.assertTrue(scroll.widgetResizable())
        # 全部条目都挂在滚动区的宿主里（不是在对话框顶层硬堆）
        host = scroll.widget()
        self.assertIsNotNone(host)
        self.assertEqual(len(host.findChildren(QFrame, "scRow")),
                         _KEYBOARD_ROWS + len(_gesture_rows()))

    def test_platform_modifier_is_shown(self):
        """修饰键按平台显示（macOS ⌘ / 其它 Ctrl），不能写死一个"""
        from app.config import AppConfig
        from app.views.shortcuts_dialog import _mod
        expected = "⌘" if AppConfig.IS_MACOS else "Ctrl"
        self.assertEqual(_mod(), expected)
        keys = " ".join(k for _t, rows in _keyboard_sections()
                        for _a, k in rows)
        self.assertIn(expected, keys)


if __name__ == "__main__":
    unittest.main()

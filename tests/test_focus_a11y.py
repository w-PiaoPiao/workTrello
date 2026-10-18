# -*- coding: utf-8 -*-
"""键盘可达性与命中区护栏

审计实测出的三件事，都只能靠测试守住（肉眼不会发现）：

1. **焦点完全不可见**：全项目原来只有 4 条 `:focus` 规则（都是文本框）。
   样式表一旦给按钮设了背景/边框，Qt 就不再画原生焦点矩形，于是 Tab 过
   工具栏、对话框按钮行、标签色块时看不出焦点在哪——"键盘可操作"形同虚设。
2. **命中区普遍低于 24×24**：卡片勾选框 20×20（全板最高频操作）、
   标签色块 34×22、对话框 ✕ 22×22、浮窗关闭钮 22×22。
3. **无障碍名几乎为零**：全项目只有 4 处 `setAccessibleName`。自绘图标按钮
   没有文字，读屏只会念 "button"。

运行：python -m unittest tests.test_focus_a11y
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PET_BOARD_DATA_DIR"] = tempfile.mkdtemp()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton

_qapp = QApplication.instance() or QApplication([])

from app.config import AppConfig            # noqa: E402
from app.models.board import BoardList, Card   # noqa: E402
from app.views.board_view import BoardView     # noqa: E402
from app.views.theme import AppTheme           # noqa: E402

# 可点击目标的下限（WCAG 2.5.8 Target Size (Minimum) 取 24×24）
MIN_TARGET = 24


class FocusVisibilityTest(unittest.TestCase):
    """焦点环与禁用态必须在全局样式表里显式声明"""

    def _qss(self) -> str:
        AppTheme.set_mode("light")
        return AppTheme.global_qss()

    def test_buttons_have_focus_ring(self):
        qss = self._qss()
        self.assertIn("QPushButton:focus", qss,
                      "按钮没有焦点环：Tab 过去看不出焦点在哪")
        self.assertIn("QCheckBox:focus", qss)

    def test_focus_ring_uses_dedicated_token(self):
        """焦点环取 focus_ring 而不是 accent

        accent 直接当焦点环时，深色下压在 glass_hover 上只有 2.57:1，
        低于非文本组件 3:1 的门槛。
        """
        for mode in ("light", "dark"):
            AppTheme.set_mode(mode)
            c = AppTheme.colors()
            self.assertIn(c["focus_ring"], AppTheme.global_qss())

    def test_disabled_buttons_look_disabled(self):
        """禁用态必须显式写：样式表会吃掉调色板的变暗

        批量编辑里"不修改"状态的日期框此前看起来完全可用，点了没反应
        也没有解释；同一个对话框里 QLineEdit 却会变灰。
        """
        qss = self._qss()
        self.assertIn("QPushButton:disabled", qss)
        self.assertIn("QDateEdit:disabled", qss)
        self.assertIn("QComboBox:disabled", qss)


class BoardHitTargetTest(unittest.TestCase):
    """看板上高频控件的命中区"""

    def setUp(self):
        self.view = BoardView()
        lst = BoardList(title="待办")
        lst.cards.append(Card(title="A"))
        self.view.refresh([lst])

    def tearDown(self):
        self.view.deleteLater()

    def _card(self):
        return self.view._iter_card_widgets().__next__()

    def test_card_checkbox_meets_min_target(self):
        cw = self._card()
        btn = cw._check_btn
        self.assertGreaterEqual(btn.width(), MIN_TARGET)
        self.assertGreaterEqual(btn.height(), MIN_TARGET)

    def test_card_checkbox_is_checkable(self):
        """必须 setCheckable：否则读屏只报 "button"，永远不报勾选状态"""
        btn = self._card()._check_btn
        self.assertTrue(btn.isCheckable(),
                        "勾选框不可 check，无障碍层拿不到完成状态")
        self.assertTrue(btn.accessibleName(),
                        "勾选框没有 accessibleName")

    def test_checkbox_reflects_done_state(self):
        cw = self._card()
        cw._check_btn.set_done(True)
        self.assertTrue(cw._check_btn.isChecked())
        cw._check_btn.set_done(False)
        self.assertFalse(cw._check_btn.isChecked())


class BoardKeyboardTest(unittest.TestCase):
    """卡片的键盘操作路径"""

    def setUp(self):
        self.view = BoardView()
        lst = BoardList(title="待办")
        lst.cards.append(Card(title="A"))
        self.view.refresh([lst])
        self.deleted: list = []
        self.view.signal_card_delete.connect(
            lambda _lid, cid: self.deleted.append(cid))

    def tearDown(self):
        self.view.deleteLater()

    def test_delete_key_removes_card(self):
        """删除此前完全没有键盘路径

        ✕ 只在悬停时创建并显示，隐藏控件连 Tab 都跳过；右键菜单里也只有
        "归档"没有"删除"。
        """
        cw = self.view._iter_card_widgets().__next__()
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtCore import QEvent
        cw.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Delete,
                                   Qt.NoModifier))
        self.assertEqual(self.deleted, [cw.card().id])


class BoardAccessibleNameTest(unittest.TestCase):
    """自绘图标按钮必须有可被念出来的名字"""

    def test_icon_buttons_have_accessible_names(self):
        view = BoardView()
        try:
            for name in ("_calendar_btn", "_settings_btn", "_theme_btn",
                         "_collapse_btn", "_overflow_btn"):
                btn = getattr(view, name)
                self.assertTrue(
                    btn.accessibleName(),
                    f"{name} 没有 accessibleName（自绘图标按钮无文字，"
                    f"读屏只会念 button）")
        finally:
            view.deleteLater()

    def test_search_box_has_accessible_name(self):
        view = BoardView()
        try:
            self.assertTrue(view._search_edit.accessibleName())
        finally:
            view.deleteLater()


class CheckboxPaintTest(unittest.TestCase):
    """勾选框绘制不得抛异常（paintEvent 内异常会损坏 backing store）"""

    def test_paint_both_states_and_themes(self):
        from PySide6.QtGui import QPixmap
        from app.views.board_view import _CardCheckButton
        for mode in ("light", "dark"):
            AppTheme.set_mode(mode)
            btn = _CardCheckButton()
            try:
                for done in (False, True):
                    btn.set_done(done)
                    pm = QPixmap(btn.size())
                    pm.fill(Qt.transparent)
                    btn.render(pm)      # 真的跑一遍 paintEvent
            finally:
                btn.deleteLater()
        AppTheme.set_mode("light")


if __name__ == "__main__":
    unittest.main(verbosity=2)

# -*- coding: utf-8 -*-
"""可达性与命中区测试（阶段 5 · 对话框/浮窗侧）

覆盖三件事：
1. **分段控件键盘可达**——审计里"主题/语言/默认形态三组设置纯鼠标可达"
   的那条 P0：容器可聚焦、方向键改选中值并广播、未知取值不再呈现"零选中"；
2. **命中区 ≥24**——移除钮 / 标签色块 / 浮窗关闭钮 / 日历折叠入口；
3. **日历键盘可达与行高推导**——方向键在日期间移动焦点、Tab 跳出网格、
   整月各行在默认窗口高度内完整可见（不再一打开就带纵向滚动条）。

用法（离屏环境）：
    QT_QPA_PLATFORM=offscreen python tests/test_controls_a11y.py
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PET_BOARD_DATA_DIR"] = tempfile.mkdtemp()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

_qapp = QApplication.instance() or QApplication([])

from app.models.board import Card                      # noqa: E402
from app.views.calendar_view import CalendarDialog     # noqa: E402
from app.views.card_dialog import CardDialog, LabelChip  # noqa: E402
from app.views.controls import SegmentedControl, ToggleSwitch  # noqa: E402
from app.views.today_popover import TodayPopover       # noqa: E402


def _key(widget, key) -> QKeyEvent:
    """派发一次按键（返回事件，便于断言是否被消费）"""
    event = QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier)
    QApplication.sendEvent(widget, event)
    return event


class SegmentedControlKeyboardTest(unittest.TestCase):
    """分段控件：容器一个 tab 站 + 方向键切换（三组设置曾纯鼠标可达）"""

    def setUp(self):
        self.seg = SegmentedControl([("a", "甲"), ("b", "乙"), ("c", "丙")])
        self.seg.resize(180, 32)
        self.seg.show()
        self.got = []
        self.seg.changed.connect(self.got.append)
        _qapp.processEvents()

    def tearDown(self):
        self.seg.hide()
        self.seg.deleteLater()

    def test_container_is_a_single_tab_stop(self):
        """容器 StrongFocus（Tab 进得来）；按钮 ClickFocus（点击可聚焦但不各占一站）"""
        self.assertEqual(self.seg.focusPolicy(), Qt.StrongFocus)
        for btn in self.seg._buttons.values():
            self.assertEqual(btn.focusPolicy(), Qt.ClickFocus)
            self.assertTrue(btn.accessibleName(), "分段按钮缺读屏名")

    def test_arrow_keys_change_value_and_emit(self):
        self.seg.set_value("a")
        self.assertEqual(self.seg.value(), "a")
        for key, expect in ((Qt.Key_Right, "b"), (Qt.Key_Right, "c"),
                            (Qt.Key_Left, "b"), (Qt.Key_Down, "c"),
                            (Qt.Key_Up, "b")):
            event = _key(self.seg, key)
            self.assertTrue(event.isAccepted(), f"{key} 未被消费")
            self.assertEqual(self.seg.value(), expect)
        self.assertEqual(self.got, ["b", "c", "b", "c", "b"])

    def test_arrows_wrap_around(self):
        self.seg.set_value("c")
        _key(self.seg, Qt.Key_Right)
        self.assertEqual(self.seg.value(), "a")
        _key(self.seg, Qt.Key_Left)
        self.assertEqual(self.seg.value(), "c")

    def test_home_end_jump_to_ends(self):
        self.seg.set_value("b")
        _key(self.seg, Qt.Key_End)
        self.assertEqual(self.seg.value(), "c")
        _key(self.seg, Qt.Key_Home)
        self.assertEqual(self.seg.value(), "a")

    def test_unknown_value_falls_back_to_first(self):
        """未知取值回退首项：此前静默 return，控件停在"零选中"的假状态"""
        self.seg.set_value("a")
        self.seg.set_value("不存在的取值")
        self.assertEqual(self.seg.value(), "a")
        self.assertTrue(self.seg._buttons["a"].isChecked())

    def test_set_value_is_silent(self):
        """同步真实偏好不走 changed（否则打开设置就会触发一次主题切换）"""
        self.seg.set_value("b")
        self.assertEqual(self.got, [])

    def test_focus_ring_paints_without_error(self):
        self.seg.setFocus()
        _qapp.processEvents()
        self.assertTrue(self.seg._focused())
        self.assertFalse(self.seg.grab().isNull())

    def test_retexts_refreshes_accessible_names(self):
        self.seg.retexts([("a", "A"), ("b", "B"), ("c", "C")])
        self.assertEqual(self.seg._buttons["a"].accessibleName(), "A")


class ToggleSwitchA11yTest(unittest.TestCase):
    def test_accessible_text_and_focus_ring(self):
        toggle = ToggleSwitch()
        toggle.resize(44, 26)
        toggle.show()
        try:
            toggle.set_accessible_text("显示桌宠", "关闭后不再出现桌宠")
            self.assertEqual(toggle.accessibleName(), "显示桌宠")
            self.assertEqual(toggle.accessibleDescription(), "关闭后不再出现桌宠")
            toggle.setFocus()
            _qapp.processEvents()
            self.assertTrue(toggle.hasFocus())
            self.assertFalse(toggle.grab().isNull())   # 焦点环绘制不抛异常
        finally:
            toggle.hide()
            toggle.deleteLater()


class HitAreaTest(unittest.TestCase):
    """命中区 ≥24：视觉可以不变，但点得到"""

    def test_label_chip_is_24_high(self):
        chip = LabelChip("red")
        try:
            self.assertEqual((chip.width(), chip.height()), (34, 24))
        finally:
            chip.deleteLater()

    def test_remove_buttons_are_24(self):
        dlg = CardDialog(Card(
            title="x",
            checklist=[{"text": "项", "done": False}],
            attachments=[{"id": "a1", "name": "a.png", "path": "/tmp/a.png",
                          "is_image": False}]))
        try:
            _check, edit = dlg._check_rows[0]
            check_btn = edit.parentWidget().layout().itemAt(2).widget()
            att_btn = dlg.findChild(type(check_btn), "attachRemove")
            for btn in (check_btn, att_btn):
                self.assertIsNotNone(btn)
                self.assertGreaterEqual(btn.width(), 24)
                self.assertGreaterEqual(btn.height(), 24)
                self.assertFalse(btn.icon().isNull(), "移除钮缺矢量图标")
                self.assertTrue(btn.accessibleName(), "移除钮缺读屏名")
        finally:
            dlg.deleteLater()

    def test_popover_close_button_is_24(self):
        pop = TodayPopover()
        try:
            # 外壳是懒构建的：先灌一次数据（生产路径同款）
            pop.set_items([])
            self.assertGreaterEqual(pop._close_btn.width(), 24)
            self.assertGreaterEqual(pop._close_btn.height(), 24)
            self.assertTrue(pop._close_btn.accessibleName())
        finally:
            pop.deleteLater()

    def test_day_more_button_has_hit_area(self):
        """「还有 N 项…」是触达当天第 4 张以后卡片的唯一入口"""
        dlg = CalendarDialog()
        dlg.resize(760, 540)
        dlg.show()
        try:
            dlg.set_month(2026, 8, {"2026-08-10": [
                Card(title=f"卡{i}") for i in range(5)]})
            _qapp.processEvents()
            cell = next(c for c in dlg._cells if c.iso() == "2026-08-10")
            more = cell._more_btn
            self.assertIsNotNone(more)
            # #dayMore 的 padding / min-height 由格子的样式表下发
            self.assertIn("padding", cell.styleSheet())
            self.assertIn("min-height", cell.styleSheet())
            self.assertGreaterEqual(more.sizeHint().height(), 20)
        finally:
            dlg.hide()
            dlg.deleteLater()


class CalendarA11yTest(unittest.TestCase):
    """日历：键盘可达 + 行高按视口推导（整月不再一打开就带滚动条）"""

    def setUp(self):
        self.dlg = CalendarDialog()
        self.dlg.resize(760, 540)
        self.dlg.show()

    def tearDown(self):
        self.dlg.hide()
        self.dlg.deleteLater()

    def _set_month_with_cards(self, year, month, iso):
        self.dlg.set_month(year, month, {iso: [
            Card(title=f"卡{i}") for i in range(5)]})
        for _ in range(3):
            _qapp.processEvents()
        return next(c for c in self.dlg._cells if c.iso() == iso)

    def test_no_scrollbar_for_any_month_shape(self):
        """5 行与 6 行月份在默认窗口高度下都完整可见（此前 6 行只见 3.6 行）"""
        for year, month, iso in ((2026, 9, "2026-09-15"),
                                 (2026, 8, "2026-08-10")):
            self._set_month_with_cards(year, month, iso)
            rows = len(self.dlg._cells) // 7
            self.assertFalse(
                self.dlg._scroll.verticalScrollBar().isVisible(),
                f"{year}-{month}（{rows} 行）仍带滚动条")
            self.assertLessEqual(self.dlg._grid_host.height(),
                                 self.dlg._scroll.viewport().height())

    def test_more_button_stays_inside_cell(self):
        """行高被压缩时折叠入口不能被挤出格子（裁掉它就等于卡片不可达）"""
        cell = self._set_month_with_cards(2026, 8, "2026-08-10")
        more = cell._more_btn
        self.assertIsNotNone(more)
        top = more.mapTo(cell, QPoint(0, 0)).y()
        self.assertLessEqual(top + more.height(), cell.height())

    def _focus_widget(self):
        """窗口内的焦点控件

        断言用 focusWidget() 而不是 hasFocus()：全量跑测试时别的用例可能
        留着已显示的窗口，本对话框不一定是**活动窗口**，hasFocus() 会假红；
        focusWidget() 只看窗口内部的焦点归属，正是键盘事件的实际去向。
        """
        _qapp.processEvents()
        return self.dlg.focusWidget()

    def test_arrow_keys_move_focus_between_days(self):
        self._set_month_with_cards(2026, 9, "2026-09-15")
        cells = self.dlg._cells
        cells[0].setFocus()
        self.assertIs(self._focus_widget(), cells[0])
        _key(cells[0], Qt.Key_Right)
        self.assertIs(self._focus_widget(), cells[1])
        _key(cells[1], Qt.Key_Down)
        self.assertIs(self._focus_widget(), cells[8])

    def test_tab_leaves_the_grid(self):
        """35 个格子都在 tab 链里：Tab 必须直接跳出，而不是逐格跳"""
        self._set_month_with_cards(2026, 9, "2026-09-15")
        cell = self.dlg._cells[8]
        cell.setFocus()
        self.assertIs(self._focus_widget(), cell)
        _key(cell, Qt.Key_Tab)
        self.assertIs(self._focus_widget(), self.dlg._close_btn)

    def test_chip_enter_opens_edit(self):
        cell = self._set_month_with_cards(2026, 9, "2026-09-15")
        chip = cell._chip_widgets[0]
        got = []
        self.dlg.signal_edit_requested.connect(got.append)
        chip.setFocus()
        _qapp.processEvents()
        self.assertIs(self.dlg.focusWidget(), chip)
        _key(chip, Qt.Key_Return)
        self.assertEqual(got, [chip.card_id()])


if __name__ == "__main__":
    unittest.main()

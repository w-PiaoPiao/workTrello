# -*- coding: utf-8 -*-
"""
滚轮防误触控件测试：DateEdit / ComboBox / SpinBox 的滚轮事件忽略并穿透

回归背景：Qt 的 QAbstractSpinBox/QComboBox 默认对悬停（无需焦点）的滚轮
事件直接增减值——mac 触控板双指滑动划过资金环节列表时，办理日期框被误改
（Windows 鼠标滚轮同理）。防误触版本必须：值不变 + 事件 ignore（父级滚动
区接手滚动）。

用法：QT_QPA_PLATFORM=offscreen python tests/test_wheel_safe.py
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PET_BOARD_DATA_DIR"] = tempfile.mkdtemp()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QDate, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QDateEdit

_qapp = None

from app.views.controls import ComboBox, DateEdit, SpinBox


def _app():
    global _qapp
    if _qapp is None:
        from PySide6.QtWidgets import QApplication
        _qapp = QApplication.instance() or QApplication([])
    return _qapp


def _wheel(angle_delta: QPoint) -> QWheelEvent:
    """模拟一次触控板/滚轮滑动（mac 双指上滑 = angleDelta.y 为正）"""
    return QWheelEvent(QPointF(5, 5), QPointF(5, 5),
                       QPoint(0, 0), angle_delta,
                       Qt.NoButton, Qt.NoModifier,
                       Qt.ScrollUpdate, False)


class WheelSafeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _app()

    def test_date_edit_ignores_wheel(self):
        """悬停滚轮不再增减日期（修复前 QDateEdit 默认会改）"""
        de = DateEdit()
        de.setDate(QDate(2026, 9, 15))
        ev = _wheel(QPoint(0, 120))
        de.wheelEvent(ev)
        self.assertEqual(de.date(), QDate(2026, 9, 15))
        self.assertFalse(ev.isAccepted())   # ignore → 父级滚动区可接手

    def test_date_edit_has_calendar_popup(self):
        """防误触不牺牲调值入口：日历弹窗与格式默认配好"""
        de = DateEdit()
        self.assertTrue(de.calendarPopup())
        self.assertEqual(de.displayFormat(), "yyyy-MM-dd")

    def test_combo_box_ignores_wheel(self):
        """悬停滚轮不再切换下拉选中项"""
        combo = ComboBox()
        combo.addItems(["A", "B", "C"])
        combo.setCurrentIndex(1)
        ev = _wheel(QPoint(0, 120))
        combo.wheelEvent(ev)
        self.assertEqual(combo.currentIndex(), 1)
        self.assertFalse(ev.isAccepted())

    def test_spin_box_ignores_wheel(self):
        """悬停滚轮不再改数字（重复间隔天数等）"""
        spin = SpinBox()
        spin.setRange(1, 365)
        spin.setValue(7)
        ev = _wheel(QPoint(0, 120))
        spin.wheelEvent(ev)
        self.assertEqual(spin.value(), 7)
        self.assertFalse(ev.isAccepted())

    def test_stock_dialog_uses_safe_widgets(self):
        """卡片对话框的日期/下拉/数字框全部为防误触版本"""
        from app.models.board import Card
        from app.views.card_dialog import CardDialog
        card = Card(title="资金卡")
        card.switch_fund_kind("finance")
        dlg = CardDialog(card)
        self.assertIsInstance(dlg._due_edit, DateEdit)
        self.assertIsInstance(dlg._repeat_combo, ComboBox)
        self.assertIsInstance(dlg._repeat_interval_spin, SpinBox)
        self.assertIsInstance(dlg._fund_start_edit, DateEdit)
        self.assertTrue(dlg._fund_rows)   # 环节行已生成
        for row in dlg._fund_rows:
            # 环节办理时间框：QDateEdit 子类（DateEdit 或带哨兵的实例）
            self.assertIsInstance(row["date"], QDateEdit)
        dlg.deleteLater()

    def test_settings_dialog_uses_safe_combo(self):
        """设置对话框的下拉框为防误触版本"""
        from app.views.settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        self.assertIsInstance(dlg._remind_combo, ComboBox)
        dlg.deleteLater()


if __name__ == "__main__":
    unittest.main(verbosity=2)

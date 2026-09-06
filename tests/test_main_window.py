# -*- coding: utf-8 -*-
"""
窗口置顶切换测试：Qt.WindowStaysOnTopHint 的设置/恢复、可见性保持、
勾选态同步（blockSignals 防回环）

用法：QT_QPA_PLATFORM=offscreen python tests/test_main_window.py
"""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

_qapp = QApplication.instance() or QApplication([])

from app.views.main_window import MainWindow
from app.views.pet_view import PetView


class AlwaysOnTopTest(unittest.TestCase):
    def setUp(self):
        self.w = MainWindow()

    def tearDown(self):
        self.w.hide()
        self.w.deleteLater()

    def test_default_is_on_top(self):
        self.assertTrue(self.w.is_always_on_top())
        self.assertTrue(self.w.windowFlags() & Qt.WindowStaysOnTopHint)

    def test_toggle_off_and_on(self):
        self.w.set_always_on_top(False)
        self.assertFalse(self.w.is_always_on_top())
        self.assertFalse(self.w.windowFlags() & Qt.WindowStaysOnTopHint)
        self.w.set_always_on_top(True)
        self.assertTrue(self.w.is_always_on_top())

    def test_toggle_preserves_visibility(self):
        self.w.show()
        self.assertTrue(self.w.isVisible())
        self.w.set_always_on_top(False)
        self.assertTrue(self.w.isVisible())
        self.w.set_always_on_top(True)
        self.assertTrue(self.w.isVisible())

    def test_toggle_while_hidden_stays_hidden(self):
        self.assertFalse(self.w.isVisible())
        self.w.set_always_on_top(False)
        self.assertFalse(self.w.isVisible())
        self.w.set_always_on_top(True)
        self.assertFalse(self.w.isVisible())

    def test_idempotent_toggle_is_noop(self):
        self.w.show()
        self.assertTrue(self.w.isVisible())
        self.w.set_always_on_top(True)  # 已是置顶：不应触发隐藏/重建
        self.assertTrue(self.w.isVisible())


class PetAlwaysTopMenuTest(unittest.TestCase):
    def test_checked_sync_does_not_recurse(self):
        pet = PetView()
        emitted = []
        pet.signal_always_top_toggled.connect(emitted.append)

        pet.set_always_top_checked(False)
        self.assertFalse(pet._act_always_top.isChecked())
        self.assertEqual(emitted, [])  # 同步不触发信号回环

        pet.set_always_top_checked(True)
        self.assertTrue(pet._act_always_top.isChecked())
        self.assertEqual(emitted, [])

        pet.deleteLater()


if __name__ == "__main__":
    unittest.main(verbosity=2)

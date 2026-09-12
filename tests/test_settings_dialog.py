# -*- coding: utf-8 -*-
"""设置界面测试：分区构建、偏好同步、分段/开关控件、语言文案刷新"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PET_BOARD_DATA_DIR"] = tempfile.mkdtemp()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

_qapp = QApplication.instance() or QApplication([])

from app import i18n
from app.views.controls import SegmentedControl, ToggleSwitch
from app.views.settings_dialog import SettingsDialog


class ControlsTest(unittest.TestCase):
    def test_segmented_value_roundtrip(self):
        seg = SegmentedControl([("a", "A"), ("b", "B")])
        self.assertIsNone(seg.value())
        seg.set_value("b")
        self.assertEqual(seg.value(), "b")
        hits = []
        seg.changed.connect(hits.append)
        seg._buttons["a"].click()
        self.assertEqual(hits, ["a"])
        self.assertEqual(seg.value(), "a")

    def test_segmented_retexts_keeps_value(self):
        seg = SegmentedControl([("a", "甲"), ("b", "乙")])
        seg.set_value("b")
        seg.retexts([("a", "A"), ("b", "B")])
        self.assertEqual(seg._buttons["a"].text(), "A")
        self.assertEqual(seg.value(), "b")

    def test_toggle_switch_checked_emits_once(self):
        t = ToggleSwitch()
        hits = []
        t.toggled.connect(hits.append)
        t.setChecked(True)
        self.assertEqual(hits, [True])
        self.assertTrue(t.isChecked())
        t.setChecked(True)      # 同值不重复发
        self.assertEqual(hits, [True])
        t.click()               # 点击翻转（经 nextCheckState）
        self.assertEqual(hits, [True, False])


class SettingsDialogTest(unittest.TestCase):
    def setUp(self):
        self.dlg = SettingsDialog()

    def tearDown(self):
        i18n.set_lang("zh")

    def test_sync_from_prefs(self):
        self.dlg.sync_from_prefs("dark", "en", "choco", False, True)
        self.assertEqual(self.dlg._theme_seg.value(), "dark")
        self.assertEqual(self.dlg._lang_seg.value(), "en")
        self.assertTrue(self.dlg._skin_buttons["choco"].isChecked())
        self.assertFalse(self.dlg._skin_buttons["milk"].isChecked())
        self.assertFalse(self.dlg._anim_toggle.isChecked())
        self.assertTrue(self.dlg._top_toggle.isChecked())

    def test_theme_signal(self):
        hits = []
        self.dlg.signal_theme_selected.connect(hits.append)
        self.dlg._theme_seg._buttons["system"].click()
        self.assertEqual(hits, ["system"])

    def test_language_signal(self):
        hits = []
        self.dlg.signal_language_selected.connect(hits.append)
        self.dlg._lang_seg._buttons["en"].click()
        self.assertEqual(hits, ["en"])

    def test_retexts_in_english(self):
        i18n.set_lang("en")
        self.dlg.retexts()
        self.assertEqual(self.dlg.windowTitle(), "Settings")
        self.assertEqual(self.dlg._t_theme.text(), "Theme")
        self.assertEqual(self.dlg._dir_open_btn.text(), "Open Folder")

    def test_skin_buttons_cover_all_skins(self):
        from app.config import AppConfig
        self.assertEqual(set(self.dlg._skin_buttons), set(AppConfig.PET_SKINS))


if __name__ == "__main__":
    import unittest
    unittest.main()

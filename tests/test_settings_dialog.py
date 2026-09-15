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

    def test_autostart_toggle_syncs_and_emits(self):
        """开机自启动开关：随偏好回填，点击发信号（持久化由控制器负责）"""
        self.assertFalse(self.dlg._autostart_toggle.isChecked())
        self.dlg.sync_from_prefs("system", "zh", "milk", True, True,
                                 autostart=True)
        self.assertTrue(self.dlg._autostart_toggle.isChecked())
        hits = []
        self.dlg.signal_autostart_toggled.connect(hits.append)
        self.dlg.set_autostart(False)        # 回滚路径：只改状态不发信号
        self.assertEqual(hits, [])
        self.assertFalse(self.dlg._autostart_toggle.isChecked())
        self.dlg._autostart_toggle.click()
        self.assertEqual(hits, [True])

    def test_retexts_in_english(self):
        i18n.set_lang("en")
        self.dlg.retexts()
        self.assertEqual(self.dlg.windowTitle(), "Settings")
        self.assertEqual(self.dlg._t_theme.text(), "Theme")
        self.assertEqual(self.dlg._dir_open_btn.text(), "Open Folder")
        self.assertEqual(self.dlg._t_autostart.text(), "Launch at login")

    def test_skin_buttons_cover_all_skins(self):
        from app.config import AppConfig
        self.assertEqual(set(self.dlg._skin_buttons), set(AppConfig.PET_SKINS))

    def test_skin_buttons_are_exclusive(self):
        """皮肤单选：点新的自动取消旧的，且不能把唯一的选中项点掉

        回归背景：四个按钮各自 checkable、没挂互斥组，点新皮肤时旧的仍
        显示选中——皮肤实际换了（偏好已存），界面却像没换。
        """
        self.dlg.sync_from_prefs("system", "zh", "milk", True, True)
        self.assertEqual(self._checked_skins(), {"milk"})
        self.dlg._skin_buttons["choco"].click()
        self.assertEqual(self._checked_skins(), {"choco"})
        self.dlg._skin_buttons["snow"].click()
        self.assertEqual(self._checked_skins(), {"snow"})
        self.dlg._skin_buttons["snow"].click()      # 再点已选中的：不变
        self.assertEqual(self._checked_skins(), {"snow"})

    def test_set_skin_realigns_to_pref(self):
        """偏好在别处被改（桌宠右键菜单）时，设置页回写为唯一选中项"""
        self.dlg.sync_from_prefs("system", "zh", "milk", True, True)
        self.dlg._skin_buttons["choco"].click()
        self.dlg.set_skin("midnight")
        self.assertEqual(self._checked_skins(), {"midnight"})

    def test_sync_from_prefs_is_silent(self):
        """同步偏好**不发信号**：它只是把真实状态刷进界面

        此前 setChecked 照常 emit toggled，"打开设置"这个动作本身就会触发
        置顶/动画等副作用（切置顶会重建主窗口原生句柄，连带隐藏子对话框，
        表现即"设置打不开"）。
        """
        hits: dict[str, list] = {"theme": [], "lang": [], "anim": [],
                                 "top": [], "auto": [], "remind": []}
        self.dlg.signal_theme_selected.connect(hits["theme"].append)
        self.dlg.signal_language_selected.connect(hits["lang"].append)
        self.dlg.signal_animation_toggled.connect(hits["anim"].append)
        self.dlg.signal_always_top_toggled.connect(hits["top"].append)
        self.dlg.signal_autostart_toggled.connect(hits["auto"].append)
        self.dlg.signal_remind_advance_changed.connect(hits["remind"].append)
        # 一次性把所有控件都刷成与当前不同的值
        self.dlg.sync_from_prefs("dark", "en", "snow", False, False,
                                 remind_advance=3, autostart=True)
        self.assertEqual({k: v for k, v in hits.items() if v}, {})

    def test_sync_from_prefs_then_user_click_still_emits(self):
        """静音只覆盖同步本身：同步之后再点，信号照常发出"""
        self.dlg.sync_from_prefs("system", "zh", "milk", True, True,
                                 autostart=False)
        hits = []
        self.dlg.signal_always_top_toggled.connect(hits.append)
        self.dlg._top_toggle.click()
        self.assertEqual(hits, [False])

    def _checked_skins(self) -> set[str]:
        return {k for k, b in self.dlg._skin_buttons.items() if b.isChecked()}


if __name__ == "__main__":
    import unittest
    unittest.main()

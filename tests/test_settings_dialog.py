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

from PySide6.QtCore import QRectF, QVariantAnimation
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

_qapp = QApplication.instance() or QApplication([])

from app import i18n
from app.config import AppConfig
from app.views import motion
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

    def _seg(self, values=("a", "A", "b", "B")) -> SegmentedControl:
        seg = SegmentedControl([(values[0], values[1]), (values[2], values[3])])
        seg.show()                 # 触发布局：高亮块要贴按钮真实几何
        _qapp.processEvents()
        return seg

    @staticmethod
    def _pill_running(seg) -> bool:
        """动画是否运行中（动画对象自复用改造后常驻，不能以 None 判断）"""
        return seg._pill_anim.state() == QVariantAnimation.Running

    def test_segmented_pill_sits_on_selection_without_animating(self):
        """set_value 是"同步真实偏好"，高亮块必须瞬时落位

        回归背景：选中态原先只是按钮自己的 :checked 边框，点另一侧时没有
        任何东西在动，看不出选中项换到了哪边（"点了没反应"）。改成容器自绘
        高亮块后，同步路径仍不许播动画——打开设置这个动作不该在动。
        """
        seg = self._seg()
        seg.set_value("b")
        self.assertEqual(seg._selected, "b")
        self.assertFalse(self._pill_running(seg))     # 不播动画
        self.assertEqual(seg._paint_rect(),
                         QRectF(seg._buttons["b"].geometry()))

    def test_segmented_pill_slides_on_click(self):
        """用户点击：高亮块滑向被点中的按钮，而不是原地消失再出现在另一侧"""
        seg = self._seg()
        seg.set_value("a")
        pet_rect = QRectF(seg._buttons["a"].geometry())
        board_rect = QRectF(seg._buttons["b"].geometry())
        self.assertEqual(seg._paint_rect(), pet_rect)
        seg._buttons["b"].click()
        self.assertTrue(self._pill_running(seg))      # 播动画，不是瞬时跳
        QTest.qWait(AppConfig.SEGMENT_PILL_MS // 4)
        mid = QRectF(seg._pill)
        self.assertTrue(self._pill_running(seg))      # 仍在滑动途中
        self.assertLessEqual(pet_rect.x(), mid.x())
        self.assertLessEqual(mid.x(), board_rect.x())
        QTest.qWait(AppConfig.SEGMENT_PILL_MS + 150)
        self.assertFalse(self._pill_running(seg))
        self.assertEqual(seg._paint_rect(), board_rect)

    def test_segmented_pill_tracks_button_geometry_without_cache(self):
        """高亮块几何不缓存：按钮被挪动后立刻跟上，不依赖容器 resize

        回归背景：几何原先是缓存值、只在容器的 resizeEvent 里更新。但 Qt 在
        macOS 上先发容器 resizeEvent、之后才 activate 布局，那一刻读到的还是
        中间态（CI 实测偏 2px）；而布局挪动按钮自身不会再触发容器 resize，
        没有任何时机能补上。改为绘制时现取后，直接挪按钮即可验证。
        """
        seg = self._seg()
        seg.set_value("b")
        btn = seg._buttons["b"]
        btn.move(btn.x() + 7, btn.y())
        btn.resize(btn.width() + 5, btn.height())
        self.assertEqual(seg._paint_rect(), QRectF(btn.geometry()))

    def test_segmented_pill_follows_retexts_relayout(self):
        """切语言后按钮文字变宽 → 高亮块要跟着新几何落位，不能停在旧位置"""
        seg = self._seg(("zh", "中文", "en", "English"))
        seg.set_value("en")
        seg.retexts([("zh", "Chinese"), ("en", "English")])
        _qapp.processEvents()          # 让重排请求跑完
        seg.retexts([("zh", "中"), ("en", "E")])   # 大幅缩窄，几何必然变
        _qapp.processEvents()
        self.assertEqual(seg._paint_rect(),
                         QRectF(seg._buttons["en"].geometry()))

    def test_segmented_pill_snaps_when_animation_paused(self):
        """「暂停动画」总开关一关：高亮块瞬时落位，不建动画对象"""
        seg = self._seg()
        seg.set_value("a")
        original = motion.enabled()
        try:
            motion.set_enabled(False)
            seg._buttons["b"].click()
            self.assertFalse(self._pill_running(seg))
            self.assertEqual(seg._paint_rect(),
                             QRectF(seg._buttons["b"].geometry()))
        finally:
            motion.set_enabled(original)

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

    def test_pet_enabled_toggle_syncs_and_emits(self):
        """显示桌宠开关：随偏好回填，点击发信号（持久化由控制器负责）"""
        self.assertFalse(self.dlg._pet_toggle.isChecked())
        self.dlg.sync_from_prefs("system", "zh", "milk", True, True,
                                 pet_enabled=True)
        self.assertTrue(self.dlg._pet_toggle.isChecked())
        hits = []
        self.dlg.signal_pet_enabled_toggled.connect(hits.append)
        self.dlg._pet_toggle.click()
        self.assertEqual(hits, [False])       # 默认开 → 点击即关
        self.dlg._pet_toggle.click()
        self.assertEqual(hits, [False, True])

    def test_retexts_in_english(self):
        i18n.set_lang("en")
        self.dlg.retexts()
        self.assertEqual(self.dlg.windowTitle(), "Settings")
        self.assertEqual(self.dlg._t_theme.text(), "Theme")
        self.assertEqual(self.dlg._dir_open_btn.text(), "Open Folder")
        self.assertEqual(self.dlg._t_autostart.text(), "Launch at login")
        self.assertEqual(self.dlg._t_view.text(), "Default view on open")
        self.assertEqual(self.dlg._t_pet.text(), "Show desktop pet")

    # ── 默认打开形态 ──────────────────────────────────────

    def test_default_view_options_and_signal(self):
        """默认打开形态是二选一段控：点选发信号，值只有 pet/board"""
        self.assertEqual(set(self.dlg._view_seg._buttons), {"pet", "board"})
        hits = []
        self.dlg.signal_default_view_selected.connect(hits.append)
        self.dlg._view_seg._buttons["board"].click()
        self.assertEqual(hits, ["board"])
        self.assertEqual(self.dlg._view_seg.value(), "board")

    def test_default_view_synced_from_pref(self):
        """打开设置时回填真实偏好（段控与主题/语言一样由同步填入选中项）"""
        self.assertIsNone(self.dlg._view_seg.value())    # 构造时无选中
        self.dlg.sync_from_prefs("system", "zh", "milk", True, True,
                                 default_view="board")
        self.assertEqual(self.dlg._view_seg.value(), "board")
        self.dlg.sync_from_prefs("system", "zh", "milk", True, True,
                                 default_view="pet")
        self.assertEqual(self.dlg._view_seg.value(), "pet")

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
                                 "top": [], "auto": [], "remind": [],
                                 "view": [], "pet_on": []}
        self.dlg.signal_theme_selected.connect(hits["theme"].append)
        self.dlg.signal_language_selected.connect(hits["lang"].append)
        self.dlg.signal_animation_toggled.connect(hits["anim"].append)
        self.dlg.signal_always_top_toggled.connect(hits["top"].append)
        self.dlg.signal_autostart_toggled.connect(hits["auto"].append)
        self.dlg.signal_remind_advance_changed.connect(hits["remind"].append)
        self.dlg.signal_default_view_selected.connect(hits["view"].append)
        self.dlg.signal_pet_enabled_toggled.connect(hits["pet_on"].append)
        # 一次性把所有控件都刷成与当前不同的值
        self.dlg.sync_from_prefs("dark", "en", "snow", False, False,
                                 remind_advance=3, autostart=True,
                                 default_view="board", pet_enabled=True)
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

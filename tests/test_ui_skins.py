# -*- coding: utf-8 -*-
"""视觉皮肤护栏：5 套皮肤必须「结构完整、对比度达标、可持久化、能即时切换」

为什么单独一个文件
------------------
皮肤把「配色」从两份固定色表变成了「5 皮肤 × 2 深浅 = 10 份色表」，
每份都要满足和主表同一条 WCAG 门槛；而造型（圆角/字重/徽章形态）是
另一种失败模式——它不会让任何断言变红，只会在界面上出现"配色换了、
圆角没换"的半套皮肤。所以两类都写成测试：

1. 结构：每套皮肤都有 light/dark 两份，且**键集合与 COLORS 完全一致**
   （QSS 生成按固定键名插值，缺键是运行期 KeyError 或空值）；
2. 对比度：10 份色表全部按 tests/test_contrast.py 同一套组合与门槛遍历；
3. 持久化：默认皮肤是 editorial，写坏的值回退默认，开关调一遍能读回；
4. 切换：AppTheme.set_skin 落盘 + 重下样式表 + 广播，且看板 QSS 真的
   随皮肤变化（缓存键漏了 skin 是最容易犯的错）。

运行：python -m unittest tests.test_ui_skins
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

from PySide6.QtWidgets import QApplication                    # noqa: E402

_qapp = QApplication.instance() or QApplication([])

from app.config import AppConfig                              # noqa: E402
from app.views import tokens                                  # noqa: E402
from app.views.board_view import _board_qss, badge_content     # noqa: E402
from app.views.theme import AppTheme                          # noqa: E402
from tests.test_contrast import (                             # noqa: E402
    AA_NON_TEXT,
    AA_SMALL_TEXT,
    FILL_ON_KEYS,
    TONE_SOFT_KEYS,
    composite,
    contrast_ratio,
)

EXPECTED_SKINS = ("editorial", "warm", "terminal", "brutal", "frosted")

# 用户点名的 5 套皮肤里只有 editorial 是默认；其余 4 套也必须存在
DEFAULT_SKIN = "editorial"


def _skin_themes() -> list[tuple[str, str, dict]]:
    """[(皮肤, 深浅, 色表)]，覆盖全部皮肤 × 全部主题"""
    out = []
    for skin in AppConfig.UI_SKINS:
        spec = AppConfig.UI_SKINS[skin]
        for mode in ("light", "dark"):
            out.append((skin, mode, spec[mode]))
    return out


class SkinTableStructureTest(unittest.TestCase):
    """皮肤表结构：键集合、造型缺省、标签色"""

    def test_expected_skins_exist(self):
        missing = [k for k in EXPECTED_SKINS if k not in AppConfig.UI_SKINS]
        self.assertFalse(missing, f"缺少皮肤: {missing}")

    def test_default_skin_is_editorial(self):
        self.assertEqual(AppConfig.UI_SKIN_DEFAULT, DEFAULT_SKIN)
        self.assertIn(DEFAULT_SKIN, AppConfig.UI_SKINS)

    def test_every_skin_has_light_and_dark_with_identical_keys(self):
        """每条色表键集合必须与 COLORS 一致

        缺键：`_board_qss` 里 `c['bg_card']` 直接 KeyError，界面起不来；
        多键：说明写错名字（比如把 danger_soft 写成 danger_bg），那份颜色
        永远不会被用到，等于少配了一个色。
        """
        base = set(AppConfig.COLORS)
        for skin, mode, colors in _skin_themes():
            self.assertEqual(
                set(colors) - base, set(),
                f"{skin}/{mode} 有多余的键: {sorted(set(colors) - base)}")
            self.assertEqual(
                base - set(colors), set(),
                f"{skin}/{mode} 缺少这些键: {sorted(base - set(colors))}")

    def test_skin_names_are_unique(self):
        names = [spec["name"] for spec in AppConfig.UI_SKINS.values()]
        self.assertEqual(len(names), len(set(names)), f"皮肤名重复: {names}")

    def test_shape_falls_back_to_baseline_values(self):
        """造型缺省：没写任何 shape 键时，圆角必须等于皮肤引入前的基线"""
        sh = tokens.shape("")
        for name, value in (("shell", AppConfig.UI_RADIUS_SHELL),
                            ("panel", AppConfig.UI_RADIUS_PANEL),
                            ("card", AppConfig.UI_RADIUS_CARD),
                            ("control", AppConfig.UI_RADIUS_CONTROL),
                            ("chip", AppConfig.UI_RADIUS_CHIP)):
            self.assertEqual(sh["radii"][name], value,
                             f"基线圆角 {name} 被皮肤缺省值改掉了")
        self.assertEqual(sh["badge_style"], "pill")
        self.assertEqual(sh["hard_shadow"], 0)

    def test_every_skin_defines_radii_for_all_names(self):
        """皮肤写了 radii 就必须写全：只写一半会出现"卡片方角、输入框圆角" """
        names = {"shell", "panel", "card", "control", "chip", "pill"}
        for skin, spec in AppConfig.UI_SKINS.items():
            radii = (spec.get("shape") or {}).get("radii") or {}
            if radii:
                self.assertEqual(set(radii), names,
                                 f"{skin} 的 radii 不完整: {sorted(radii)}")

    def test_every_skin_has_label_palette(self):
        for skin in AppConfig.UI_SKINS:
            table = AppConfig.SKIN_LABEL_COLORS.get(skin) or {}
            for mode in ("light", "dark"):
                self.assertIn(mode, table, f"{skin}/{mode} 缺少标签色")
                self.assertEqual(set(table[mode]), set(AppConfig.LABEL_COLORS),
                                 f"{skin}/{mode} 标签色键与主表不一致")


class SkinContrastTest(unittest.TestCase):
    """10 份色表全部按主表同一套门槛遍历

    门槛与 tests/test_contrast.py 完全一致（同一组工具函数），所以"换皮肤
    换出一屏读不清的字"这件事会在 CI 里直接变红，而不是等用户发现。
    """

    def test_every_skin_badge_tone_meets_aa(self):
        for skin, mode, c in _skin_themes():
            for tone, (bg_key, fg_key) in TONE_SOFT_KEYS.items():
                bg = composite(c[bg_key], c["bg_card"])
                ratio = contrast_ratio(c[fg_key], bg)
                self.assertGreaterEqual(
                    ratio, AA_SMALL_TEXT,
                    f"{skin}/{mode}/{tone}: {c[fg_key]} on {bg} = "
                    f"{ratio:.2f}:1，低于 {AA_SMALL_TEXT}:1")

    def test_every_skin_fill_text_meets_threshold(self):
        for skin, mode, c in _skin_themes():
            for name, (fill_key, on_key, need) in FILL_ON_KEYS.items():
                ratio = contrast_ratio(c[on_key], c[fill_key])
                self.assertGreaterEqual(
                    ratio, need,
                    f"{skin}/{mode}/{name}: {c[on_key]} on {c[fill_key]} = "
                    f"{ratio:.2f}:1（需 {need}）")

    def test_text_readable_on_card_and_board(self):
        for skin, mode, c in _skin_themes():
            for surface in ("bg_card", "bg_primary"):
                for key in ("text_primary", "text_secondary", "text_muted"):
                    ratio = contrast_ratio(c[key], c[surface])
                    self.assertGreaterEqual(
                        ratio, AA_SMALL_TEXT,
                        f"{skin}/{mode}: {key} on {surface} = {ratio:.2f}:1")

    def test_hint_and_placeholder_legible(self):
        for skin, mode, c in _skin_themes():
            worst_hint = min(
                contrast_ratio(c["hint_on_board"], c[stop])
                for stop in ("board_bg_start", "board_bg_mid", "board_bg_end"))
            self.assertGreaterEqual(
                worst_hint, AA_SMALL_TEXT,
                f"{skin}/{mode}: 空看板提示最差 {worst_hint:.2f}:1")
            glass = composite(c["glass"], c["board_bg_mid"])
            self.assertGreaterEqual(
                contrast_ratio(c["text_muted"], glass), AA_SMALL_TEXT,
                f"{skin}/{mode}: 搜索占位在玻璃面上不达标")

    def test_focus_ring_visible_on_board(self):
        for skin, mode, c in _skin_themes():
            base = composite(c["glass_hover"], c["board_bg_mid"])
            ratio = contrast_ratio(c["focus_ring"], base)
            self.assertGreaterEqual(
                ratio, AA_NON_TEXT,
                f"{skin}/{mode}: 焦点环 {c['focus_ring']} on {base} = "
                f"{ratio:.2f}:1（需 {AA_NON_TEXT}）")

    def test_skin_label_palettes_meet_aa(self):
        for skin, table in AppConfig.SKIN_LABEL_COLORS.items():
            for mode, colors in table.items():
                for key, (bg, fg) in colors.items():
                    self.assertGreaterEqual(
                        contrast_ratio(fg, bg), AA_SMALL_TEXT,
                        f"{skin}/{mode} 标签 {key}: {fg} on {bg} = "
                        f"{contrast_ratio(fg, bg):.2f}:1")


class SkinPreferenceTest(unittest.TestCase):
    """偏好读写：默认值、白名单、持久化"""

    def setUp(self):
        self._prev = AppConfig.get_ui_skin()

    def tearDown(self):
        AppConfig.save_ui_skin(self._prev)

    def test_default_is_editorial(self):
        AppConfig.settings().remove(AppConfig.KEY_UI_SKIN)
        self.assertEqual(AppConfig.get_ui_skin(), "editorial")

    def test_unknown_skin_falls_back_to_default(self):
        AppConfig.settings().setValue(AppConfig.KEY_UI_SKIN, "neon-unicorn")
        self.assertEqual(AppConfig.get_ui_skin(), "editorial")

    def test_save_then_read_roundtrip(self):
        for key in EXPECTED_SKINS:
            AppConfig.save_ui_skin(key)
            self.assertEqual(AppConfig.get_ui_skin(), key)

    def test_save_ignores_unknown_key(self):
        AppConfig.save_ui_skin("warm")
        AppConfig.save_ui_skin("not-a-skin")
        self.assertEqual(AppConfig.get_ui_skin(), "warm",
                         "保存非法皮肤时必须忽略，而不是把偏好写坏")


class SkinSwitchTest(unittest.TestCase):
    """切换链路：落盘 + 重下样式表 + 广播 + QSS 真的随皮肤变"""

    def setUp(self):
        self._prev_skin = AppTheme.skin()
        self._prev_mode = AppTheme.mode()

    def tearDown(self):
        AppTheme.set_skin(self._prev_skin, touch_pref=False)
        AppTheme.set_mode(self._prev_mode)
        AppConfig.save_ui_skin(self._prev_skin)

    def test_set_skin_persists_and_applies(self):
        AppTheme.set_skin("brutal")
        self.assertEqual(AppTheme.skin(), "brutal")
        self.assertEqual(AppConfig.get_ui_skin(), "brutal")
        self.assertEqual(_qapp.styleSheet(), AppTheme.global_qss())

    def test_set_skin_notifies_listeners(self):
        hits = []

        def on_skin():
            hits.append("cb")
        AppTheme.register_skin_listener(on_skin)
        AppTheme.set_skin("frosted")
        self.assertEqual(hits, ["cb"])
        # 摘掉本用例注册的回调：全局单例会被后续用例复用，留着会互相污染
        AppTheme._skin_listeners = [
            ref for ref in AppTheme._skin_listeners
            if ref() is not on_skin]

    def test_set_skin_ignores_unknown(self):
        AppTheme.set_skin("terminal", touch_pref=False)
        AppTheme.set_skin("nope", touch_pref=False)
        self.assertEqual(AppTheme.skin(), "terminal")

    def test_board_qss_differs_per_skin(self):
        """缓存键漏了 skin 时，切皮肤会拿到上一套的样式表（界面不变）"""
        qss = {skin: _board_qss("light", "normal", skin)
               for skin in AppConfig.UI_SKINS}
        self.assertEqual(len(set(qss.values())), len(qss),
                         "看板 QSS 没有随皮肤变化——缓存键多半漏了 skin")

    def test_board_qss_reflects_skin_colors(self):
        for skin in AppConfig.UI_SKINS:
            colors = AppConfig.UI_SKINS[skin]["light"]
            qss = _board_qss("light", "normal", skin)
            self.assertIn(colors["bg_card"], qss,
                          f"{skin}: 看板 QSS 里找不到该皮肤卡片底色")

    def test_radius_tokens_are_skin_aware(self):
        # 直角皮肤：圆角可缩到 0（密度下限只挡"退化的负值"，不挡直角）
        self.assertEqual(tokens.radius("card", "terminal"), 0,
                         "终端皮肤应为直角卡片")
        self.assertGreater(tokens.radius("card", "warm"),
                           tokens.radius("card", "editorial"),
                           "暖纸皮肤的圆角应大于瑞士排版")

    def test_badge_prefix_only_in_text_skin(self):
        sh_text = tokens.shape("terminal")
        sh_pill = tokens.shape("frosted")
        self.assertEqual(badge_content("P1", "warning", sh_text), "~P1")
        self.assertEqual(badge_content("P1", "warning", sh_pill), "P1")
        self.assertEqual(badge_content("已逾期", "danger", sh_text),
                         "!已逾期")


if __name__ == "__main__":
    unittest.main(verbosity=2)

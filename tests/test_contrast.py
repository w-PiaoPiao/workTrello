# -*- coding: utf-8 -*-
"""对比度护栏：把「文字必须读得清」变成可执行断言

为什么需要这个测试
------------------
审计实测出的失败面比预想大得多，而且**光靠肉眼改不动**（人眼对小字对比度
的判断极不可靠）：

- 卡片徽章：所有 tone 共用 `accent_soft` 作底、只用 `c[tone]` 换文字色 →
  红字压蓝底，warning 1.73:1 / success 2.57:1 / danger 3.34:1，11px 小字全线失守；
- 占位与提示文字借用了 `text_disabled`（一个**禁用态**的色）→ 搜索框占位
  1.58:1、空列提示 1.87:1；
- 饱和填充上写死白字 → 深色主按钮 3.23:1、勾选对勾 1.99:1、Windows ✕ 悬停 2.77:1；
- 空看板引导（新手唯一能看到的一句）2.98:1。

所以门槛写成测试：**颜色表怎么改都行，达标是硬条件**。

棘轮约定
--------
- 已达标的组合直接断言，不设名单。
- 尚未达标的组合登记在 `KNOWN_*_FAILURES` 里，断言"失败集合 == 名单"：
  修好了会变红（逼你删条目），新坏了也会变红（逼你修）。名单只进不退。

运行：python -m unittest tests.test_contrast
"""

from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import AppConfig   # noqa: E402

# ── 颜色解析与对比度 ──────────────────────────────────────────

_CSS_RE = re.compile(
    r"^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*"
    r"(?:,\s*([0-9.]+)\s*)?\)$")


def parse_color(spec) -> tuple[int, int, int, float]:
    """颜色 → (r, g, b, a)；支持 #hex、QSS 的 rgb()/rgba() 与 (r,g,b[,a]) 元组

    色表里的半透明色用的是 css rgba() 语法（accent_soft / glass / bg_panel），
    QColor 构造它只会得到无效色，必须自己解。
    """
    if isinstance(spec, (tuple, list)):
        vals = list(spec) + ([1.0] if len(spec) == 3 else [])
        return int(vals[0]), int(vals[1]), int(vals[2]), float(vals[3])
    text = str(spec).strip()
    m = _CSS_RE.match(text)
    if m:
        r, g, b = (int(m.group(i)) for i in (1, 2, 3))
        a = float(m.group(4)) if m.group(4) else 1.0
        return r, g, b, a
    if text.startswith("#"):
        h = text.lstrip("#")
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0
    raise ValueError(f"无法解析颜色: {spec!r}")


def composite(fg, bg) -> tuple[int, int, int]:
    """把带 alpha 的 fg 叠加在不透明 bg 上，返回实际呈现的 rgb"""
    fr, fg_, fb, fa = parse_color(fg)
    br, bg_, bb, _ = parse_color(bg)
    return (round(fr * fa + br * (1 - fa)),
            round(fg_ * fa + bg_ * (1 - fa)),
            round(fb * fa + bb * (1 - fa)))


def _linear(channel: int) -> float:
    c = channel / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb) -> float:
    r, g, b = (_linear(x) for x in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg, bg) -> float:
    """WCAG 2.1 对比度；fg/bg 可以是 hex / rgba() 字符串，也可以是 rgb 元组"""
    f = parse_color(fg)[:3] if isinstance(fg, str) else tuple(fg)[:3]
    b = parse_color(bg)[:3] if isinstance(bg, str) else tuple(bg)[:3]
    l1, l2 = luminance(f), luminance(b)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


# ── 门槛 ──────────────────────────────────────────────────────

AA_SMALL_TEXT = 4.5      # 小字（< 18.66px 常规 / < 14px 粗体）
AA_NON_TEXT = 3.0        # 非文本 UI 组件：焦点环、图标、对勾

# 卡片徽章：tone → (软底色键, 前景色键)
TONE_SOFT_KEYS = {
    "danger": ("danger_soft", "danger_text"),
    "warning": ("warning_soft", "warning_text"),
    "success": ("success_soft", "success_text"),
    "accent": ("accent_soft", "accent_text"),
    # 中性徽章（备注 / 附件）：前景单独取 neutral_text，不复用 text_secondary
    # ——后者在深色中性软底上只有 4.30:1。
    "neutral": ("neutral_soft", "neutral_text"),
}

# 饱和填充：填充色键 → (压在其上的色键, 门槛)
# 门槛按"压在上面的是什么"分：accent 上是**按钮文字**（4.5），
# success/danger/warning 上是**字形**（勾选对勾、✕ 符号 → 非文本，3.0）。
FILL_ON_KEYS = {
    "accent": ("accent", "accent_on", AA_SMALL_TEXT),
    "success": ("success", "success_on", AA_NON_TEXT),
    "danger": ("danger", "danger_on", AA_NON_TEXT),
    "warning": ("warning", "warning_on", AA_NON_TEXT),
}


def _themes() -> dict:
    return {"light": AppConfig.COLORS, "dark": AppConfig.DARK_COLORS}


# ── 仍未达标项（棘轮：只能变少）───────────────────────────────
#
# 每一项都必须能在计划文档里找到对应的阶段；修好后测试会变红提醒删除。
KNOWN_SURFACE_FAILURES = {
    # 空列提示：QLabel#columnHint 目前仍取 text_disabled（board_view.py，阶段 2）
    ("light", "column_hint_on_panel"),
    ("dark", "column_hint_on_panel"),
    # 红绿灯悬停符号写死 QColor(66,44,20,175)（traffic_lights.py，阶段 5）
    ("light", "traffic_glyph_on_fill"),
    ("dark", "traffic_glyph_on_fill"),
}


def _measure_surface_pairs(colors: dict) -> dict:
    """返回 {描述: 比值}，键与 KNOWN_SURFACE_FAILURES 的描述一致"""
    out = {}
    # 空看板引导：hint_on_board 直接画在板底上（没有玻璃面），取最差采样点
    out["empty_board_hint_on_board"] = min(
        contrast_ratio(colors["hint_on_board"], colors[stop])
        for stop in ("board_bg_start", "board_bg_mid", "board_bg_end"))

    # 搜索框占位文字：调色板 PlaceholderText 取 text_muted，底是玻璃面
    glass = composite(colors["glass"], colors["board_bg_mid"])
    out["placeholder_on_search_glass"] = contrast_ratio(
        colors["text_muted"], glass)

    # 空列提示：`QLabel#columnHint` **当前实际**取的是 text_disabled
    # （board_view.py 尚未改）。这里按现状度量，改完后会变成"已达标"，
    # 届时把它换成 text_muted 并把条目从 KNOWN 里删掉。
    panel = composite(colors["bg_panel"], colors["board_bg_mid"])
    out["column_hint_on_panel"] = contrast_ratio(colors["text_disabled"], panel)

    # 搜索框焦点环：focus_ring 压在 glass_hover 上
    out["focus_ring_on_glass_hover"] = contrast_ratio(
        colors["focus_ring"],
        composite(colors["glass_hover"], colors["board_bg_mid"]))

    # 红绿灯悬停符号：QColor(66,44,20,175) 半透明压在红/黄/绿填充上，
    # 必须按合成后的实际颜色算，取最差的一枚
    out["traffic_glyph_on_fill"] = min(
        contrast_ratio(composite((66, 44, 20, 175 / 255), f), f)
        for f in (colors["danger"], colors["warning"], colors["success"]))
    return out


class BadgeToneAudit(unittest.TestCase):
    """卡片徽章：每个 tone 的软底 + 前景必须达标（11px 小字，门槛 4.5:1）"""

    def test_every_tone_pair_meets_aa(self):
        for theme_name, colors in _themes().items():
            for tone, (bg_key, fg_key) in TONE_SOFT_KEYS.items():
                bg = composite(colors[bg_key], colors["bg_card"])
                ratio = contrast_ratio(colors[fg_key], bg)
                self.assertGreaterEqual(
                    ratio, AA_SMALL_TEXT,
                    f"{theme_name}/{tone}: {colors[fg_key]} on {bg} = "
                    f"{ratio:.2f}:1，低于 {AA_SMALL_TEXT}:1")

    def test_every_tone_has_its_own_soft_background(self):
        """不允许再出现"所有 tone 共用一个底"的写法

        `_board_qss` 的徽章规则按 tone 取底色；如果某个 tone 的软底色键
        缺失或与另一个 tone 相同，说明又退回了共用底。
        """
        for theme_name, colors in _themes().items():
            missing = [k for pair in TONE_SOFT_KEYS.values()
                       for k in pair if k not in colors]
            self.assertFalse(
                missing, f"{theme_name} 主题缺少软底/前景 token: {missing}")
            used = [TONE_SOFT_KEYS[t][0] for t in TONE_SOFT_KEYS]
            self.assertEqual(
                len(set(used)), len(used),
                f"{theme_name}: tone 之间复用了同一个软底色 {used}")


class FillTextAudit(unittest.TestCase):
    """饱和填充上的文字：必须单独取色，不能写死白色"""

    def test_text_on_saturated_fills(self):
        for theme_name, colors in _themes().items():
            for name, (fill_key, on_key, need) in FILL_ON_KEYS.items():
                ratio = contrast_ratio(colors[on_key], colors[fill_key])
                self.assertGreaterEqual(
                    ratio, need,
                    f"{theme_name}/{name}: {colors[on_key]} on "
                    f"{colors[fill_key]} = {ratio:.2f}:1（需 {need}）")

    def test_dark_theme_inverts_text_on_bright_fills(self):
        """深色主题的填充是亮的，压在上面的文字必须是深色

        白字压深色 accent 只有 3.23:1、压 success 只有 1.99:1——这是"写死
        white"在深色主题下的系统性失败，不是个别位置的问题。
        """
        dark = AppConfig.DARK_COLORS
        for name in ("accent", "success", "danger"):
            on = parse_color(dark[f"{name}_on"])
            self.assertLess(
                luminance(on), 0.5,
                f"深色主题 {name}_on 应为深色文字，实测 {dark[f'{name}_on']}")


class TextOnSurfaceAudit(unittest.TestCase):
    """正文/次要/提示文字在主要表面上的对比度"""

    def test_text_on_surfaces(self):
        for theme_name, colors in _themes().items():
            for surface in ("bg_card", "bg_primary"):
                for key in ("text_primary", "text_secondary", "text_muted"):
                    ratio = contrast_ratio(colors[key], colors[surface])
                    self.assertGreaterEqual(
                        ratio, AA_SMALL_TEXT,
                        f"{theme_name}: {key} on {surface} = {ratio:.2f}:1")

    def test_toolbar_text_on_glass_over_board(self):
        """工具条 chip 是玻璃面浮在板底上：板底两端都要读得清"""
        for theme_name, colors in _themes().items():
            for stop in ("board_bg_start", "board_bg_mid", "board_bg_end"):
                glass = composite(colors["glass"], colors[stop])
                ratio = contrast_ratio(colors["text_primary"], glass)
                self.assertGreaterEqual(
                    ratio, AA_SMALL_TEXT,
                    f"{theme_name}: text_primary on glass over {stop} "
                    f"({colors[stop]}) = {ratio:.2f}:1")

    def test_empty_board_hint_is_legible(self):
        """空看板引导是新手唯一能看到的一句话，必须达标"""
        for theme_name, colors in _themes().items():
            worst = min(
                contrast_ratio(colors["hint_on_board"], colors[stop])
                for stop in ("board_bg_start", "board_bg_mid", "board_bg_end"))
            self.assertGreaterEqual(
                worst, AA_SMALL_TEXT,
                f"{theme_name}: hint_on_board 在板底上最差 {worst:.2f}:1")


class LabelColorAudit(unittest.TestCase):
    """标签色块：文字 on 色块底（卡片对话框 / 过滤 chip）"""

    def _check(self, table: dict, label: str):
        for key, (bg, fg) in table.items():
            ratio = contrast_ratio(fg, bg)
            self.assertGreaterEqual(
                ratio, AA_SMALL_TEXT,
                f"{label}标签 {key}: {fg} on {bg} = {ratio:.2f}:1")

    def test_light_label_colors_meet_aa(self):
        self._check(AppConfig.LABEL_COLORS, "浅色")

    def test_dark_label_colors_meet_aa(self):
        self._check(AppConfig.DARK_LABEL_COLORS, "深色")

    def test_dark_labels_do_not_reuse_light_palette(self):
        """深色必须有自己的标签表

        复用浅色 pastel 底 + 把文字统一压成 #10131A 的写法，会让六种色的
        选中边框全变成同一个近黑色，且亮色块在深色界面上层级反了。
        """
        for key in AppConfig.LABEL_COLORS:
            self.assertIn(key, AppConfig.DARK_LABEL_COLORS,
                          f"深色标签表缺少 {key}")
            self.assertNotEqual(
                AppConfig.LABEL_COLORS[key][0],
                AppConfig.DARK_LABEL_COLORS[key][0],
                f"{key} 的深色底仍在复用浅色 pastel")


class SurfacePairRatchet(unittest.TestCase):
    """表面/填充对比度棘轮：仍未达标项登记在册"""

    def test_surface_pair_failures_match_known_set(self):
        failing, detail = set(), []
        for theme_name, colors in _themes().items():
            for desc, ratio in sorted(_measure_surface_pairs(colors).items()):
                need = AA_NON_TEXT if "focus_ring" in desc or \
                    "traffic_glyph" in desc else AA_SMALL_TEXT
                detail.append(f"  {theme_name:5s} {desc:32s} "
                              f"{ratio:5.2f}:1  (需 {need})")
                if ratio < need:
                    failing.add((theme_name, desc))

        msg = ("\n表面/填充对比度审计:\n%s\n\n失败 = %s\n已知 = %s\n"
               % ("\n".join(detail), sorted(failing),
                  sorted(KNOWN_SURFACE_FAILURES)))

        fixed = KNOWN_SURFACE_FAILURES - failing
        self.assertFalse(
            fixed,
            "以下组合已达标，请从 KNOWN_SURFACE_FAILURES 删除:\n"
            + "\n".join(f"  {t}/{n}" for t, n in sorted(fixed)) + "\n" + msg)

        new = failing - KNOWN_SURFACE_FAILURES
        self.assertFalse(
            new,
            "出现新的对比度不达标组合:\n"
            + "\n".join(f"  {t}/{n}" for t, n in sorted(new)) + "\n" + msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)

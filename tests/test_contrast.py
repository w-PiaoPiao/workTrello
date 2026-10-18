# -*- coding: utf-8 -*-
"""对比度护栏：把「语义色必须读得清」变成可执行断言

为什么需要这个测试
------------------
卡片信息行徽章的底色此前是**所有 tone 共用一个 `accent_soft`**（蓝色调），
文字却按 tone 取红/橙/绿——两个后果：

1. 对比度不达标。徽章字号 11px 属小字，WCAG AA 要求 4.5:1；实测浅色下
   warning 1.73:1、success 2.57:1、danger 3.34:1、accent 3.84:1，全线失守。
2. 语义被底色污染。红字压蓝底，色相互相干扰，"已逾期"和"有备注"看起来
   是同一枚灰蓝 chip。

光靠肉眼调色改不动这件事（人眼对小字对比度的判断极不可靠），所以把门槛
写成测试：**颜色表怎么改都行，达标是硬条件**。

两段式设计（棘轮）
------------------
- `test_current_rendering_failures_are_exactly_the_known_set`：按**当前**
  实际渲染方式（统一 accent_soft 底）审计，断言失败集合与 `KNOWN_FAILING`
  完全相等。今天绿；阶段 2 修好后它会变红，提醒你把条目从 KNOWN_FAILING
  里删掉——不允许"修好了但名单没清"。
- `test_per_tone_soft_backgrounds_meet_aa`：目标态断言。每个 tone 有独立
  软底色时，逐对校验 ≥4.5:1。阶段 2 之前自动跳过。

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
    """主题色字符串 → (r, g, b, a)；支持 #hex 与 QSS 的 rgb()/rgba()

    色表里半透明色用的是 css rgba() 语法（accent_soft / glass / bg_panel），
    QColor 构造它只会得到无效色，必须自己解。也接受 (r,g,b) / (r,g,b,a)
    元组，方便直接表达自绘代码里的 QColor 字面量。
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
    """WCAG 2.1 对比度；fg/bg 可以是 hex 或 rgba() 字符串，也可以是 rgb 元组"""
    f = parse_color(fg)[:3] if isinstance(fg, str) else fg
    b = parse_color(bg)[:3] if isinstance(bg, str) else bg
    l1, l2 = luminance(f), luminance(b)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


# ── 审计范围 ──────────────────────────────────────────────────

# 卡片信息行徽章用到的 tone（与 board_view._BADGE_TONES 一致）
BADGE_TONES = ("danger", "warning", "accent", "text_secondary", "success")

# 徽章字号 11px → 小字，门槛 4.5:1
AA_SMALL_TEXT = 4.5

# 阶段 0 基线：按「统一 accent_soft 底」渲染时实测不达标的 (主题, tone)。
# 阶段 2 给每个 tone 配独立软底色后，这些条目会转绿，届时必须从这里删除
# （测试会因此变红，这就是棘轮的用力点）。
KNOWN_FAILING = {
    ("light", "danger"),          # 3.34:1
    ("light", "warning"),         # 1.73:1
    ("light", "accent"),          # 3.84:1
    ("light", "success"),         # 2.57:1
    ("dark", "danger"),           # 3.75:1
    ("dark", "accent"),           # 3.21:1
    ("dark", "text_secondary"),   # 4.16:1
}

# 目标态：tone → (软底色键, 前景色键)。阶段 2 落这些键。
TONE_SOFT_KEYS = {
    "danger": ("danger_soft", "danger_text"),
    "warning": ("warning_soft", "warning_text"),
    "success": ("success_soft", "success_text"),
    "accent": ("accent_soft", "accent_text"),
    "text_secondary": ("neutral_soft", "text_secondary"),
}


def _themes() -> dict:
    return {"light": AppConfig.COLORS, "dark": AppConfig.DARK_COLORS}


class BadgeContrastAudit(unittest.TestCase):
    """按当前渲染方式审计徽章对比度（棘轮）"""

    def test_current_rendering_failures_are_exactly_the_known_set(self):
        failing = set()
        detail = []
        for theme_name, colors in _themes().items():
            # 徽章底：统一的 accent_soft 叠在卡面上（_board_qss 的旧规则）
            badge_bg = composite(colors["accent_soft"], colors["bg_card"])
            for tone in BADGE_TONES:
                if tone not in colors:
                    continue
                ratio = contrast_ratio(colors[tone], badge_bg)
                detail.append(f"  {theme_name:5s} {tone:15s} "
                              f"{colors[tone]:>22s} on {badge_bg} = "
                              f"{ratio:5.2f}:1")
                if ratio < AA_SMALL_TEXT:
                    failing.add((theme_name, tone))

        msg = ("\n徽章对比度审计（门槛 %.1f:1）:\n%s\n\n"
               "失败集合 = %s\n已知集合 = %s\n"
               % (AA_SMALL_TEXT, "\n".join(detail),
                  sorted(failing), sorted(KNOWN_FAILING)))

        # 转绿（修好了）→ 变红，要求从 KNOWN_FAILING 删条目
        fixed = KNOWN_FAILING - failing
        self.assertFalse(
            fixed,
            "以下组合已达标，请从 KNOWN_FAILING 中删除（棘轮只进不退）:\n"
            + "\n".join(f"  {t}/{n}" for t, n in sorted(fixed)) + "\n" + msg)

        # 新出现的不达标 → 变红
        new = failing - KNOWN_FAILING
        self.assertFalse(
            new,
            "出现新的对比度不达标组合:\n"
            + "\n".join(f"  {t}/{n}" for t, n in sorted(new)) + "\n" + msg)

    def test_per_tone_soft_backgrounds_meet_aa(self):
        """目标态：每个 tone 配独立软底色 + 前景色，逐对 ≥4.5:1

        阶段 2 之前色表里还没有这些键，自动跳过（不算失败）。
        """
        for theme_name, colors in _themes().items():
            missing = [k for pair in TONE_SOFT_KEYS.values()
                       for k in pair if k not in colors]
            if missing:
                self.skipTest(
                    f"{theme_name} 主题尚未引入逐 tone 软底色（阶段 2）: "
                    f"{sorted(set(missing))}")

        for theme_name, colors in _themes().items():
            for tone, (bg_key, fg_key) in TONE_SOFT_KEYS.items():
                bg = composite(colors[bg_key], colors["bg_card"])
                ratio = contrast_ratio(colors[fg_key], bg)
                self.assertGreaterEqual(
                    ratio, AA_SMALL_TEXT,
                    f"{theme_name}/{tone}: {colors[fg_key]} on {bg} = "
                    f"{ratio:.2f}:1，低于 {AA_SMALL_TEXT}:1")


class TextOnSurfaceAudit(unittest.TestCase):
    """正文/次要文字在主要表面上的对比度（含板底与玻璃工具条）"""

    def test_primary_and_secondary_text_on_surfaces(self):
        for theme_name, colors in _themes().items():
            for surface in ("bg_card", "bg_primary"):
                base = colors[surface]
                for key in ("text_primary", "text_secondary"):
                    ratio = contrast_ratio(colors[key], base)
                    self.assertGreaterEqual(
                        ratio, AA_SMALL_TEXT,
                        f"{theme_name}: {key} on {surface} = {ratio:.2f}:1")

    def test_toolbar_text_on_glass_over_board_gradient(self):
        """工具条 chip 是玻璃面浮在板底渐变上：渐变两端都要读得清

        板底是线性渐变，同一排按钮落在不同位置会得到不同的合成底色，
        因此取三个采样点（起点/中点/终点）分别校验。
        """
        stops = {
            "light": ("board_bg_start", "board_bg_mid", "board_bg_end"),
            "dark": ("board_bg_start", "board_bg_mid", "board_bg_end"),
        }
        for theme_name, colors in _themes().items():
            for stop in stops[theme_name]:
                glass = composite(colors["glass"], colors[stop])
                ratio = contrast_ratio(colors["text_primary"], glass)
                self.assertGreaterEqual(
                    ratio, AA_SMALL_TEXT,
                    f"{theme_name}: text_primary on glass over {stop} "
                    f"({colors[stop]}) = {ratio:.2f}:1")


class LabelColorAudit(unittest.TestCase):
    """标签色块：文字 on 色块底（卡片对话框 / 过滤 chip）"""

    # 现状：浅色 green 4.31:1、orange 3.79:1 不达标。阶段 2 修好后本测试会
    # 变成 "unexpected success"（unittest 记为失败），届时摘掉装饰器。
    @unittest.expectedFailure   # 阶段 2 摘除
    def test_light_label_colors_meet_aa(self):
        for key, (bg, fg) in AppConfig.LABEL_COLORS.items():
            ratio = contrast_ratio(fg, bg)
            self.assertGreaterEqual(
                ratio, AA_SMALL_TEXT,
                f"浅色标签 {key}: {fg} on {bg} = {ratio:.2f}:1")


# ── 第二轮审计确认的表面/填充失败项（棘轮）───────────────────────
#
# 与徽章那批不同：这些要么需要新 token（text_muted / focus_ring），
# 要么需要在饱和填充上反相取文字色（深色主题的主按钮、勾选对勾、✕ 悬停）。
# 阶段 2 修好后测试会变红，逼你把条目删掉。
KNOWN_SURFACE_FAILURES = {
    ("light", "empty_board_hint_on_gradient"),   # 2.98–3.70:1，唯一的新手引导文字
    ("light", "placeholder_on_search_glass"),    # 1.58:1
    ("dark", "placeholder_on_search_glass"),     # 1.90:1
    ("light", "column_hint_on_panel"),           # 1.87:1，空列里唯一的文字
    ("dark", "column_hint_on_panel"),            # 2.60:1
    ("dark", "white_on_accent_primary_button"),  # 3.23:1，每个对话框的主按钮
    ("light", "white_on_accent_primary_button"),  # 4.50 卡在门槛上，实测略低于 4.5
    ("dark", "tick_on_success_fill"),            # 1.99:1，勾选框对勾
    ("dark", "white_on_danger_hover"),           # 2.77:1，Windows ✕ 悬停
    ("dark", "focus_ring_on_glass_hover"),       # 2.57:1，搜索框焦点环
    ("light", "traffic_glyph_on_fill"),          # 2.75:1，红绿灯符号
    ("dark", "traffic_glyph_on_fill"),           # 同左（符号色不随主题变）
}

# 门槛按"是不是文本"区分：非文本 UI 组件（焦点环、符号、对勾）按 3:1
_NON_TEXT_KEYS = ("focus_ring", "traffic_glyph", "tick_on", "danger_hover")


def _measure_surface_pairs(colors: dict) -> dict:
    """返回 {描述: 比值}，键与 KNOWN_SURFACE_FAILURES 的描述一致"""
    out = {}
    # 空看板引导：hint_on_board 直接画在渐变上（没有玻璃面），取最差采样点
    out["empty_board_hint_on_gradient"] = min(
        contrast_ratio(colors["hint_on_board"], colors[stop])
        for stop in ("board_bg_start", "board_bg_mid", "board_bg_end"))

    # 搜索框占位文字：调色板 PlaceholderText 取 text_disabled，底是玻璃面
    glass = composite(colors["glass"], colors["board_bg_mid"])
    out["placeholder_on_search_glass"] = contrast_ratio(
        colors["text_disabled"], glass)

    # 空列提示：text_disabled 画在列面板上
    panel = composite(colors["bg_panel"], colors["board_bg_mid"])
    out["column_hint_on_panel"] = contrast_ratio(colors["text_disabled"], panel)

    # 主按钮：primary_button_style 硬编码 color: white on accent
    out["white_on_accent_primary_button"] = contrast_ratio("#FFFFFF",
                                                           colors["accent"])

    # 勾选框对勾：_CHECK_PEN 硬编码 #FFFFFF，画在 success 填充上
    out["tick_on_success_fill"] = contrast_ratio("#FFFFFF", colors["success"])

    # Windows ✕ 悬停：danger 填充 + 白色符号
    out["white_on_danger_hover"] = contrast_ratio("#FFFFFF", colors["danger"])

    # 搜索框焦点环：accent 边框压在 glass_hover 上
    out["focus_ring_on_glass_hover"] = contrast_ratio(
        colors["accent"], composite(colors["glass_hover"],
                                    colors["board_bg_mid"]))

    # 红绿灯悬停符号：QColor(66,44,20,175) 半透明压在红/黄/绿填充上，
    # 必须按合成后的实际颜色算（不合成会低估失败程度），取最差的一枚
    out["traffic_glyph_on_fill"] = min(
        contrast_ratio(composite((66, 44, 20, 175 / 255), f), f)
        for f in (colors["danger"], colors["warning"], colors["success"]))
    return out


class SurfacePairRatchet(unittest.TestCase):
    """第二轮审计确认的表面/填充对比度棘轮"""

    def test_surface_pair_failures_match_known_set(self):
        failing, detail = set(), []
        for theme_name, colors in _themes().items():
            for desc, ratio in sorted(_measure_surface_pairs(colors).items()):
                need = 3.0 if any(k in desc for k in _NON_TEXT_KEYS) \
                    else AA_SMALL_TEXT
                detail.append(f"  {theme_name:5s} {desc:34s} "
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

# -*- coding: utf-8 -*-
"""设计 token 护栏：字号 / 圆角 / 内边距不允许再出现字面量

为什么需要这个测试
------------------
`config.py` 里已经有 `UI_RADIUS_*` / `UI_PAD_*` token，但只覆盖了一部分：
实测全项目还有 **65 处 `font-size:`（7 个不同档位：10/11/12/13/14/15/17）**、
59 处字面量 `border-radius`（11 种取值）、46 处字面量 `padding`（24 种取值）。
同一个 `_board_qss` 里就同时存在 8px（token）、10px（卡片）、12px（选择栏）、
14px（列表列）、17px（圆形按钮）——靠人工记忆维持一致不可持续，而且**字号
根本没有 token**，这是"界面密度"这类设置无从下手的原因。

棘轮设计
--------
`LITERAL_BASELINE` 记录**当前**每个文件的字面量数量。测试断言实测值与之
完全相等：

- 改多了 → 变红（不允许新的字面量进来）
- 改少了 → 也变红，并提示你把基线调低

阶段 1 把这些数字全部清零后，基线表会变成空字典，测试就退化成
"任何字面量都不允许"的硬约束。棘轮只进不退。

运行：python -m unittest tests.test_design_tokens
"""

from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from app.config import AppConfig   # noqa: E402

# ── 扫描规则 ──────────────────────────────────────────────────
# 只认 QSS 字符串里的声明；注释里的说明文字不算（`padding: 0 不是装饰` 那种）

_PATTERNS = {
    "font-size": re.compile(r"font-size:\s*[^;{}\n]+"),
    "border-radius": re.compile(r"border-radius:\s*[^;{}\n]+"),
    "padding": re.compile(r"\bpadding:\s*[^;{}\n]+"),
}

# 阶段 0 基线（实测值）。阶段 1 清零后此表应为 {}。
LITERAL_BASELINE: dict[str, tuple[int, int, int]] = {
    # path: (font-size, border-radius, padding)
    "app/views/archive_dialog.py": (3, 4, 4),
    "app/views/board_view.py": (15, 18, 11),
    "app/views/calendar_view.py": (6, 3, 3),
    "app/views/card_dialog.py": (13, 4, 4),
    "app/views/notes_popover.py": (3, 1, 0),
    "app/views/pet_view.py": (1, 1, 1),
    "app/views/quick_add_dialog.py": (4, 2, 2),
    "app/views/settings_dialog.py": (8, 6, 5),
    "app/views/shortcuts_dialog.py": (2, 2, 1),
    "app/views/theme.py": (0, 11, 8),
    "app/views/toast.py": (1, 1, 1),
    "app/views/today_popover.py": (9, 6, 6),
}


def _scan() -> dict[str, tuple[int, int, int]]:
    """逐文件统计字面量数量（顺序与 _PATTERNS 一致）"""
    out: dict[str, tuple[int, int, int]] = {}
    for path in sorted(_ROOT.glob("app/**/*.py")):
        text = path.read_text(encoding="utf-8")
        counts = tuple(len(rx.findall(text)) for rx in _PATTERNS.values())
        if any(counts):
            out[str(path.relative_to(_ROOT))] = counts
    return out


class LiteralSweepRatchet(unittest.TestCase):
    """QSS 字面量棘轮：只能减少，不能增加"""

    def test_literal_counts_match_baseline(self):
        actual = _scan()

        added = {k: v for k, v in actual.items()
                 if k not in LITERAL_BASELINE}
        grew = {k: (LITERAL_BASELINE[k], v) for k, v in actual.items()
                if k in LITERAL_BASELINE and v != LITERAL_BASELINE[k]}
        shrunk = {k: (LITERAL_BASELINE[k], v) for k, v in actual.items()
                  if k in LITERAL_BASELINE
                  and sum(v) < sum(LITERAL_BASELINE[k])}
        gone = sorted(set(LITERAL_BASELINE) - set(actual))

        hint = ("\n请把 LITERAL_BASELINE 更新为下面的实测值"
                "（棘轮只进不退：数字只能调低）:\n"
                + "\n".join(f'    "{k}": {v},' for k, v in sorted(actual.items())))

        self.assertFalse(
            added, "出现新的字面量文件（请改用 token）:\n"
            + "\n".join(f"  {k}: {v}" for k, v in sorted(added.items())) + hint)

        self.assertFalse(
            grew, "字面量数量与基线不符（新增字面量必须改用 token）:\n"
            + "\n".join(f"  {k}: 基线 {b} → 实测 {a}"
                        for k, (b, a) in sorted(grew.items())) + hint)

        # 改少了是好事，但基线必须同步调低，否则棘轮失去意义
        self.assertFalse(
            shrunk, "字面量已减少，请把基线调低（棘轮只进不退）:\n"
            + "\n".join(f"  {k}: 基线 {b} → 实测 {a}"
                        for k, (b, a) in sorted(shrunk.items())) + hint)

        self.assertFalse(
            gone, "以下文件已无字面量，请从基线中删除:\n  "
            + "\n  ".join(gone) + hint)


class TokenTableIntegrity(unittest.TestCase):
    """token 表本身的自洽性"""

    def test_light_and_dark_have_identical_keys(self):
        """两主题键集合必须一致：缺键会让 QSS 插值抛 KeyError 或静默取到空串"""
        light = set(AppConfig.COLORS)
        dark = set(AppConfig.DARK_COLORS)
        self.assertEqual(
            light - dark, set(), f"深色主题缺少这些键: {sorted(light - dark)}")
        self.assertEqual(
            dark - light, set(), f"浅色主题缺少这些键: {sorted(dark - light)}")

    # expectedFailure = 棘轮的"待办"标记：现在必然失败（token 还没建），
    # 阶段 1 建好后它会变成 "unexpected success"（unittest 记为失败），
    # 逼你把装饰器摘掉。摘掉即代表这项约束正式生效。
    @unittest.expectedFailure   # 阶段 1 摘除
    def test_radius_tokens_exist_and_are_ordered(self):
        """圆角阶梯存在且单调：外壳 ≥ 分区 ≥ 卡片 ≥ 控件 ≥ 徽章"""
        names = ("UI_RADIUS_SHELL", "UI_RADIUS_PANEL", "UI_RADIUS_CARD",
                 "UI_RADIUS_CONTROL", "UI_RADIUS_CHIP")
        missing = [n for n in names if not hasattr(AppConfig, n)]
        self.assertFalse(missing, f"缺少圆角 token: {missing}（阶段 1 补齐）")
        values = [getattr(AppConfig, n) for n in names]
        self.assertEqual(
            values, sorted(values, reverse=True),
            f"圆角阶梯必须单调递减，实测 {dict(zip(names, values))}")

    @unittest.expectedFailure   # 阶段 1 摘除
    def test_font_scale_exists_and_is_ordered(self):
        """字号阶梯存在且单调递减，且最小档不低于 11px（10px 可读性不足）"""
        names = ("UI_FONT_DISPLAY", "UI_FONT_TITLE", "UI_FONT_BODY",
                 "UI_FONT_LABEL", "UI_FONT_CAPTION")
        missing = [n for n in names if not hasattr(AppConfig, n)]
        self.assertFalse(missing, f"缺少字号 token: {missing}（阶段 1 补齐）")
        values = [getattr(AppConfig, n) for n in names]
        self.assertEqual(
            values, sorted(values, reverse=True),
            f"字号阶梯必须单调递减，实测 {dict(zip(names, values))}")
        self.assertGreaterEqual(
            min(values), 11, "最小字号不得低于 11px")

    @unittest.expectedFailure   # 阶段 1 摘除
    def test_space_scale_exists(self):
        """间距阶梯存在、升序、且覆盖 4~24px"""
        self.assertTrue(hasattr(AppConfig, "UI_SPACE"),
                        "缺少 UI_SPACE 间距阶梯（阶段 1 补齐）")
        space = tuple(AppConfig.UI_SPACE)
        self.assertEqual(space, tuple(sorted(space)), "间距阶梯必须升序")
        self.assertLessEqual(space[0], 4)
        self.assertGreaterEqual(space[-1], 24)


if __name__ == "__main__":
    unittest.main(verbosity=2)

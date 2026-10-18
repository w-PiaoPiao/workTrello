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
import ast
import io
import re
import sys
import tokenize
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from app.config import AppConfig   # noqa: E402

# ── 扫描规则 ──────────────────────────────────────────────────
# 只认**真正会下发给 Qt 的 QSS 文本**。注释与文档字符串里为了说明问题
# 引用的 `font-size: 13px` 不算字面量——否则解释性文字写得越多、棘轮越松，
# 而本模块自己的文档字符串正是重灾区。

_PATTERNS = {
    "font-size": re.compile(r"font-size:\s*([^;{}\n]+)"),
    "border-radius": re.compile(r"border-radius:\s*([^;{}\n]+)"),
    "padding": re.compile(r"\bpadding:\s*([^;{}\n]+)"),
}

# 纯零值不算"字面量"：`padding: 0` 是"不要内边距"的表达，没有对应的
# token 可言，硬要它走 token 只会让规则变噪音。
_ZERO_VALUES = {"0", "0px", "0 0", "0px 0px"}


def _is_literal(value: str) -> bool:
    """捕获到的值是不是一个**硬编码数值**

    - 以 `{` 开头 = 走 token 插值（`{tokens.radius('chip')}px`）→ 不是字面量
    - 纯零 → 不是字面量
    - 其余（`8px`、`6px 10px`、`1.5px` …）→ 是字面量

    此前用 `[^;{}\n]+` 直接当值，会把 `border-radius: {tokens…}` 里
    冒号后的那个空格也算成一次命中——token 化越彻底、假阳性越多。
    """
    v = value.strip()
    if not v or v.startswith("{"):
        return False
    return v not in _ZERO_VALUES

# token 模块本身必须排除：它的职责就是**产出** `font-size: …px` /
# `border-radius: …px` / `padding: …` 这三种声明，把它算作"字面量"
# 等于禁止定义 token。
_TOKEN_MODULES = {"app/views/tokens.py"}

# 阶段 1 已全部清零。**这张表要一直保持为空**：它一旦非空，就说明有新的
# QSS 字面量混进来了。棘轮机制仍然生效——加回字面量会让测试报"出现新的
# 字面量文件"，并给出需要填入的实测值。
LITERAL_BASELINE: dict[str, tuple[int, int, int]] = {}


def _code_only(path: Path) -> str:
    """去掉注释与文档字符串后的源码

    - 注释：用 tokenize 精确剔除。不能简单"从 # 截断"——QSS 里的 `#FFFFFF`
      会被当成注释开头。
    - 文档字符串：AST 定位后按行清空。
    """
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines()

    doc_lines: set[int] = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        tree = None
    if tree is not None:
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.ClassDef,
                                     ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = getattr(node, "body", None) or []
            if not body:
                continue
            first = body[0]
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                doc_lines.update(range(first.lineno,
                                       (first.end_lineno or first.lineno) + 1))

    comment_cols: dict[int, int] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                comment_cols[tok.start[0]] = tok.start[1]
    except tokenize.TokenError:
        pass

    out = []
    for idx, line in enumerate(lines, start=1):
        if idx in doc_lines:
            continue
        col = comment_cols.get(idx)
        if col is not None:
            line = line[:col]
        out.append(line)
    return "\n".join(out)


def _count_literals(text: str) -> tuple[int, int, int]:
    """统计文本里的硬编码字号 / 圆角 / 内边距数量"""
    return tuple(
        sum(1 for m in rx.finditer(text) if _is_literal(m.group(1)))
        for rx in _PATTERNS.values()
    )


def _scan() -> dict[str, tuple[int, int, int]]:
    """逐文件统计字面量数量（顺序与 _PATTERNS 一致）"""
    out: dict[str, tuple[int, int, int]] = {}
    for path in sorted(_ROOT.glob("app/**/*.py")):
        rel = str(path.relative_to(_ROOT))
        if rel in _TOKEN_MODULES:
            continue
        counts = _count_literals(_code_only(path))
        if any(counts):
            out[rel] = counts
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

    def test_space_scale_exists(self):
        """间距阶梯存在、升序、且覆盖 4~24px"""
        self.assertTrue(hasattr(AppConfig, "UI_SPACE"),
                        "缺少 UI_SPACE 间距阶梯（阶段 1 补齐）")
        space = tuple(AppConfig.UI_SPACE)
        self.assertEqual(space, tuple(sorted(space)), "间距阶梯必须升序")
        self.assertLessEqual(space[0], 4)
        self.assertGreaterEqual(space[-1], 24)


class GeneratedQssSanity(unittest.TestCase):
    """生成的 QSS 必须是合法的

    为什么单独测这个：`tokens.pad()` / `pad_preset()` 返回的是**整条声明**
    （`"padding: 6px 10px"`），调用点若再写一遍 `padding:` 就会生成
    `padding: padding: 6px 10px`。这种错误**不会让任何断言失败**——测试全绿，
    只有 Qt 在运行期往 stderr 打一行 "Could not parse application stylesheet"，
    然后**整份样式表静默失效**（界面直接退回 Fusion 原生外观）。
    阶段 1 的 token 替换里就真的踩了一次，所以补上这道自检。
    """

    # 典型的手写错误：声明名重复、值里混进声明名、空值
    _BAD_SNIPPETS = (
        "padding: padding", "margin: padding", "padding: margin",
        "border-radius: border-radius", "font-size: font-size",
        ": ;", ":;", "px px;",
    )

    def _qss_sources(self) -> dict:
        from app.views.board_view import _board_qss
        from app.views.theme import AppTheme, primary_button_style
        from app.config import AppConfig
        out = {}
        for mode in ("light", "dark"):
            AppTheme.set_mode(mode)
            out[f"theme:{mode}"] = AppTheme.global_qss()
            out[f"board:{mode}"] = _board_qss(mode)
            out[f"primary:{mode}"] = primary_button_style(
                AppConfig.DARK_COLORS if mode == "dark" else AppConfig.COLORS)
        return out

    def test_no_doubled_declarations(self):
        for name, qss in self._qss_sources().items():
            for bad in self._BAD_SNIPPETS:
                self.assertNotIn(
                    bad, qss,
                    f"{name} 的 QSS 里出现 {bad!r}——多半是 token 辅助函数"
                    f"（pad/pad_preset/radius_css/font_css）已经带了声明名，"
                    f"调用点又写了一遍")

    def test_no_unresolved_interpolation(self):
        """f-string 插值必须全部落实：不该残留 Python 表达式

        注意不能简单地找 `{...}`——QSS 的规则体本身就是花括号包起来的。
        未解析的插值长这样：`{tokens.radius('chip')}` / `{c['accent']}`，
        特征是花括号里出现 Python 的取值语法。
        """
        import re
        leftover = re.compile(r"\{[^{}]*\b(?:tokens\.|AppConfig\.|c\[)[^{}]*\}")
        for name, qss in self._qss_sources().items():
            found = leftover.findall(qss)
            self.assertFalse(
                found,
                f"{name} 的 QSS 里有未解析的 Python 插值: {found[:5]}")

    def test_qt_accepts_the_stylesheet(self):
        """交给 Qt 实际解析一遍：解析失败时它会打印告警并**丢弃整份样式表**"""
        from PySide6.QtWidgets import QApplication
        from app.views.theme import AppTheme
        app = QApplication.instance() or QApplication([])
        for mode in ("light", "dark"):
            AppTheme.set_mode(mode)
            qss = AppTheme.global_qss()
            app.setStyleSheet(qss)
            self.assertEqual(app.styleSheet(), qss,
                             f"{mode}: Qt 未接受该样式表（被丢弃）")


if __name__ == "__main__":
    unittest.main(verbosity=2)

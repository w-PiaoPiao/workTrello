# -*- coding: utf-8 -*-
"""i18n 测试：tr 中英切换、广播回调、显示名函数、系统语言探测

语言是模块级全局态：每个用例结束后必须恢复 zh，避免污染其他测试。
"""

import ast
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PET_BOARD_DATA_DIR"] = tempfile.mkdtemp()

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from PySide6.QtWidgets import QApplication

_qapp = QApplication.instance() or QApplication([])

from app import i18n
from app.config import AppConfig


def _tearDownModule():
    i18n.set_lang("zh")


# ── tr() 文案覆盖扫描 ─────────────────────────────────────────
# tr() 的 key 就是中文原文；命中失败时**静默回退中文**，英文界面下表现为
# "这一条没翻"，没有任何运行时错误可供断言。审计正是靠人工比对才发现两条
# 对不上：一条文案漂移（调用点改了措辞、词典键没跟上）、一条全角 ＋/半角 +
# 不一致。这里把"逐字命中"变成机械检查。

_APP_DIR = _ROOT / "app"
_CJK = re.compile(r"[\u4e00-\u9fff]")


def _tr_literals(path: Path):
    """产出 (行号, 文案)：文件里所有 `tr("中文…")` 的字面量

    - 只认字符串常量：`tr(f"…{x}")` 这类动态插值不是 key，跳过；
    - 隐式拼接的多行字面量由 AST 合并成一个常量后比对——文案漂移最容易
      发生在这种跨行长句上（词典键也必须写成同样的拼接）；
    - `i18n.tr(...)` 的限定调用一并覆盖。
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_tr = ((isinstance(func, ast.Name) and func.id == "tr")
                 or (isinstance(func, ast.Attribute) and func.attr == "tr"))
        if not is_tr:
            continue
        for arg in node.args:
            if (isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)
                    and _CJK.search(arg.value)):
                yield node.lineno, arg.value


class TrTest(unittest.TestCase):
    def tearDown(self):
        i18n.set_lang("zh")

    def test_zh_returns_source(self):
        i18n.set_lang("zh")
        self.assertEqual(i18n.tr("删除卡片"), "删除卡片")

    def test_en_translates_and_falls_back(self):
        i18n.set_lang("en")
        self.assertEqual(i18n.tr("删除卡片"), "Delete card")
        # 未收录词条回退中文原文，不抛错
        self.assertEqual(i18n.tr("不存在的词条xyz"), "不存在的词条xyz")

    def test_set_lang_broadcasts_once_on_change(self):
        calls = []
        i18n.set_lang("zh")            # 归零
        cb = lambda: calls.append(i18n.lang())
        i18n.register(cb)
        try:
            i18n.set_lang("en")
            i18n.set_lang("en")        # 同值切换短路
            self.assertEqual(calls, ["en"])
        finally:
            i18n.set_lang("zh")
            i18n.unregister(cb)

    def test_unregister_stops_broadcast(self):
        """显式退订后不再收到语言切换回调"""
        calls = []
        i18n.set_lang("zh")
        cb = lambda: calls.append(i18n.lang())
        i18n.register(cb)
        i18n.unregister(cb)
        try:
            i18n.set_lang("en")
            self.assertEqual(calls, [])
        finally:
            i18n.set_lang("zh")

    def test_format_style_entries(self):
        i18n.set_lang("en")
        self.assertEqual(i18n.tr("匹配 {n} 张").format(n=3), "3 matched")
        self.assertEqual(i18n.tr("今日 {n}").format(n=0), "Today 0")

    def test_display_functions_follow_lang(self):
        self.assertEqual(i18n.label_display("red"), "红色")
        i18n.set_lang("en")
        self.assertEqual(i18n.label_display("red"), "Red")
        self.assertEqual(i18n.repeat_display("daily"), "Daily")
        self.assertEqual(i18n.skin_display("milk"), "Milk")
        self.assertEqual(i18n.priority_name(1), "High")
        i18n.set_lang("zh")
        self.assertEqual(i18n.skin_display("milk"), "奶糖")

    def test_app_display_name_keeps_data_identity(self):
        """数据身份 APP_NAME 恒为中文常量（数据目录定位），仅显示名随语言"""
        i18n.set_lang("en")
        self.assertEqual(i18n.app_display_name(), "Pet Board")
        self.assertEqual(AppConfig.APP_NAME, "桌宠看板")
        i18n.set_lang("zh")


class LanguagePrefTest(unittest.TestCase):
    def test_get_language_detects_without_record(self):
        """无记录时按系统语言探测（不写盘）"""
        import app.i18n as m
        with patch.object(m, "detect_language", return_value="en"):
            self.assertEqual(AppConfig.get_language(), "en")

    def test_save_and_get_language(self):
        AppConfig.save_language("en")
        self.assertEqual(AppConfig.get_language(), "en")
        AppConfig.save_language("zh")
        self.assertEqual(AppConfig.get_language(), "zh")

    def test_detect_language_by_locale(self):
        """按 QLocale 探测：zh_CN → zh，en_US → en"""
        from PySide6.QtCore import QLocale
        with patch.object(QLocale, "system", return_value=QLocale("zh_CN")):
            self.assertEqual(i18n.detect_language(), "zh")
        with patch.object(QLocale, "system", return_value=QLocale("en_US")):
            self.assertEqual(i18n.detect_language(), "en")


class TranslationCoverageTest(unittest.TestCase):
    """每条 tr("中文…") 字面量都必须在 _zh_en 里有逐字相同的词条

    这是本文件里唯一会遍历全项目源码的用例：词典漏条目不会报错，
    只会在英文界面悄悄显示中文，所以只能靠静态比对兜住。
    """

    def test_every_tr_literal_is_in_dictionary(self):
        missing: dict[str, list[tuple[int, str]]] = {}
        total = 0
        for path in sorted(_APP_DIR.rglob("*.py")):
            rel = path.relative_to(_ROOT)
            for lineno, text in _tr_literals(path):
                total += 1
                if text not in i18n._zh_en:
                    missing.setdefault(str(rel), []).append((lineno, text))

        # 扫描本身失效（比如 app/ 路径写错）时不能"零缺失"地通过
        self.assertGreater(
            total, 400, f"只扫到 {total} 条 tr() 中文字面量，扫描规则可能失效")

        if missing:
            detail = "\n".join(
                f"  {f}:{ln}  {text!r}"
                for f, items in sorted(missing.items())
                for ln, text in items)
            self.fail(
                f"{total} 条 tr() 文案里有 {sum(map(len, missing.values()))} 条"
                "在 _zh_en 里找不到词条，英文界面会静默回退中文：\n"
                + detail
                + "\n（词典键必须与调用点文案逐字相同：全角/半角标点、"
                  "空格、换行与隐式拼接都要一致）")

    def test_scanner_reads_concatenated_and_skips_interpolation(self):
        """扫描规则自检：跨行拼接按合并后整条比对，f-string 不算字面量"""
        src = (
            'tr("标题不能" "为空")\n'
            'tr(f"动态 {x}")\n'
            'tr("OK")\n'
        )
        tmp = Path(tempfile.mkdtemp()) / "sample.py"
        tmp.write_text(src, encoding="utf-8")
        self.assertEqual(list(_tr_literals(tmp)), [(1, "标题不能为空")])
        self.assertIn("标题不能为空", i18n._zh_en)


if __name__ == "__main__":
    import unittest
    unittest.main()

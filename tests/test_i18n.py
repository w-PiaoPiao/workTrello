# -*- coding: utf-8 -*-
"""i18n 测试：tr 中英切换、广播回调、显示名函数、系统语言探测

语言是模块级全局态：每个用例结束后必须恢复 zh，避免污染其他测试。
"""

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
from app.config import AppConfig


def _tearDownModule():
    i18n.set_lang("zh")


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
            i18n._listeners.remove(cb)

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


if __name__ == "__main__":
    import unittest
    unittest.main()

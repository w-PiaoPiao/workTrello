# -*- coding: utf-8 -*-
"""
控制器新特性测试：撤销快照栈、截止提醒统计、系统深浅色跟随

需要 Qt 离屏环境；数据目录隔离到临时目录（在导入 app 模块前设置）。
"""

import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PET_BOARD_DATA_DIR"] = tempfile.mkdtemp()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

_qapp = QApplication.instance() or QApplication([])

from app.config import AppConfig
from app.controllers.app_controller import AppController
from app.models.board import Card
import app.views.theme as theme_mod
from app.views.theme import AppTheme


class ControllerFeatureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = AppController()

    def _list(self):
        return self.c._store.load().lists[0]

    def _titles(self):
        return [x.title for x in self._list().cards]

    def _reset(self):
        board = self.c._store.load()
        for lst in board.lists:
            lst.cards.clear()
        self.c._after_data_change(None)
        self.c._undo_stack.clear()

    # ── 撤销 ──────────────────────────────────────────────

    def test_undo_card_add(self):
        self._reset()
        self.c._on_card_add(self._list().id, "临时卡片")
        self.assertEqual(self._titles(), ["临时卡片"])
        self.c._on_undo_requested(False)
        self.assertEqual(self._titles(), [])

    def test_undo_card_done(self):
        self._reset()
        self.c._on_card_add(self._list().id, "任务")
        card_id = self._list().cards[0].id
        self.c._on_card_done(self._list().id, card_id, True)
        self.assertTrue(self._list().cards[0].done)
        self.c._on_undo_requested(False)
        self.assertFalse(self._list().cards[0].done)

    def test_undo_card_move(self):
        self._reset()
        board = self.c._store.load()
        board.lists[0].cards.append(Card(title="M1"))
        board.lists[0].cards.append(Card(title="M2"))
        self.c._after_data_change(None)

        m1_id = self._list().cards[0].id
        self.c._on_card_move(m1_id, board.lists[1].id, 0)
        self.assertEqual([x.title for x in board.lists[1].cards], ["M1"])
        self.c._on_undo_requested(False)
        board = self.c._store.load()   # 撤销会整体替换模型对象，重新获取
        self.assertEqual([x.title for x in board.lists[0].cards], ["M1", "M2"])
        self.assertEqual([x.title for x in board.lists[1].cards], [])

    def test_undo_empty_stack_noop(self):
        self._reset()
        self.c._undo_stack.clear()
        self.c._on_undo_requested(True)   # 不应抛异常

    def test_undo_limit(self):
        self._reset()
        for i in range(AppConfig.UNDO_LIMIT + 5):
            self.c._on_card_add(self._list().id, f"卡{i}")
        self.assertLessEqual(len(self.c._undo_stack), AppConfig.UNDO_LIMIT)

    # ── 截止提醒 ──────────────────────────────────────────

    def test_due_check_signature(self):
        self._reset()
        board = self.c._store.load()
        for lst in board.lists:
            for x in lst.cards:
                x.due_date = None
        self.c._after_data_change(None)
        self.c._due_signature = None
        self.c._check_due_dates()
        self.assertEqual(self.c._due_signature, (0, 0))

        self.c._on_card_add(self._list().id, "今天到期")
        card = self._list().cards[0]
        card.due_date = date.today().isoformat()
        self.c._after_data_change(None)
        self.c._check_due_dates()
        self.assertEqual(self.c._due_signature, (0, 1))
        # 状态未变：签名不重复触发
        self.c._check_due_dates()
        self.assertEqual(self.c._due_signature, (0, 1))

    # ── 系统深浅色跟随 ────────────────────────────────────

    def test_system_scheme_follow_and_manual_priority(self):
        AppTheme.set_mode("light")
        fake_cfg_system = classmethod(lambda cls: "system")
        fake_cfg_light = classmethod(lambda cls: "light")
        with patch.object(AppConfig, "get_theme_mode", fake_cfg_system), \
                patch.object(theme_mod, "_default_dark", lambda: True):
            self.c._on_system_scheme_changed()
            self.assertEqual(AppTheme.mode(), "dark")    # 跟随系统
        with patch.object(AppConfig, "get_theme_mode", fake_cfg_light):
            self.c._on_system_scheme_changed()
            self.assertEqual(AppTheme.mode(), "dark")    # 手动固定优先，不跟随
        AppTheme.set_mode("light")   # 还原全局单例


if __name__ == "__main__":
    unittest.main(verbosity=2)

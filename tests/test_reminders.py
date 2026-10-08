# -*- coding: utf-8 -*-
"""
提醒交互与每日快照测试：稍后提醒（贪睡）、气泡点击跳转、跨天自动快照

用法（离屏环境）：
    QT_QPA_PLATFORM=offscreen python tests/test_reminders.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PET_BOARD_DATA_DIR"] = tempfile.mkdtemp()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

_qapp = QApplication.instance() or QApplication([])

from app.config import AppConfig
from app.models.board import BoardList, Card
from tests import capture_notifications, restore_notify


class ReminderInteractionTest(unittest.TestCase):
    """稍后提醒 / 气泡点击跳转"""

    @classmethod
    def setUpClass(cls):
        from tests import shared_controller
        cls.c = shared_controller()

    def setUp(self):
        board = self.c._store.load()
        for lst in board.lists:
            lst.cards.clear()
        if not board.lists:
            board.lists.append(BoardList(title="待办"))
        del board.lists[1:]
        self.c._after_data_change(None)
        self.c._undo_stack.clear()
        AppConfig.save_remind_log({})
        AppConfig.save_remind_advance(0)
        self.c._snooze_until = 0.0
        self.c._snooze_timer.stop()
        self.c._notify_card_id = None
        self.notices = capture_notifications(self.c)
        self.bubbles: list = []
        self.c._tray.show_notification = lambda text: self.bubbles.append(text)

    def tearDown(self):
        restore_notify(self.c)
        self.c._snooze_timer.stop()
        self.c._snooze_until = 0.0
        AppConfig.save_remind_log({})

    def _add_due_card(self, title: str, due: date) -> str:
        board = self.c._store.load()
        card = Card(title=title, due_date=due.isoformat())
        board.lists[0].cards.append(card)
        self.c._after_data_change(None)
        self.c._undo_stack.clear()
        return card.id

    # ── 稍后提醒（贪睡） ──────────────────────────────────

    def test_snooze_with_pending_card(self):
        self._add_due_card("今天交", date.today())
        self.c._on_snooze_requested()
        self.assertTrue(self.c._snooze_timer.isActive())
        self.assertGreater(self.c._snooze_until, 0.0)
        self.assertTrue(self.notices)

    def test_snooze_without_pending_card_is_informative(self):
        """没有待提醒的卡片时明确回执（菜单项常驻，空手点是常态）"""
        self._add_due_card("以后再说", date.today() + timedelta(days=30))
        self.c._on_snooze_requested()
        self.assertFalse(self.c._snooze_timer.isActive())
        self.assertEqual(self.c._snooze_until, 0.0)
        self.assertTrue(self.notices)

    def test_done_card_does_not_count_as_pending(self):
        card_id = self._add_due_card("已办完", date.today())
        _lst, card = self.c._store.load().find_card(card_id)
        card.done = True
        self.c._after_data_change(None)
        self.c._on_snooze_requested()
        self.assertFalse(self.c._snooze_timer.isActive())

    def test_check_skips_while_snoozing(self):
        """贪睡期间不再重复提醒"""
        self._add_due_card("今天交", date.today())
        self.c._snooze_until = 1e12     # 远未来
        self.c._check_due_dates()
        self.assertEqual(self.bubbles, [])

    def test_snooze_elapsed_clears_log_and_reminds_again(self):
        self._add_due_card("今天交", date.today())
        self.c._check_due_dates()
        self.assertEqual(len(self.bubbles), 1)     # 第一次提醒
        self.c._check_due_dates()
        self.assertEqual(len(self.bubbles), 1)     # 去重，同一天不再弹

        self.c._on_snooze_elapsed()
        self.assertEqual(self.c._snooze_until, 0.0)
        self.assertEqual(len(self.bubbles), 2)     # 贪睡结束重新提醒

    def test_snooze_elapsed_keeps_yesterday_log(self):
        """只清今天的去重键：昨天的不动（否则会连带重放昨天的提醒）"""
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        AppConfig.save_remind_log({yesterday: ["x:1:today"]})
        self.c._on_snooze_elapsed()
        log = AppConfig.get_remind_log()
        self.assertIn(yesterday, log)

    # ── 气泡点击跳转 ──────────────────────────────────────

    def test_reminder_records_click_target(self):
        card_id = self._add_due_card("今天交", date.today())
        self.c._check_due_dates()
        self.assertEqual(self.c._notify_card_id, card_id)

    def test_notification_click_opens_card(self):
        card_id = self._add_due_card("今天交", date.today())
        self.c._check_due_dates()
        opened: list = []
        self.c._on_card_edit = lambda lid, cid: opened.append((lid, cid))
        self.c._on_notification_clicked()
        self.assertEqual(opened, [(self.c._store.load().lists[0].id, card_id)])

    def test_notification_click_consumes_target(self):
        self._add_due_card("今天交", date.today())
        self.c._check_due_dates()
        self.c._on_card_edit = lambda lid, cid: None
        self.c._on_notification_clicked()
        self.assertIsNone(self.c._notify_card_id)
        self.c._on_notification_clicked()   # 再点一次不应有动作

    def test_click_without_target_is_noop(self):
        opened: list = []
        self.c._on_card_edit = lambda lid, cid: opened.append(cid)
        self.c._on_notification_clicked()
        self.assertEqual(opened, [])

    def test_generic_notify_clears_click_target(self):
        """普通操作反馈不该继承上一次提醒的跳转目标"""
        self._add_due_card("今天交", date.today())
        self.c._check_due_dates()
        self.assertIsNotNone(self.c._notify_card_id)
        restore_notify(self.c)          # 走真实 _notify（打桩的影子会挡掉它）
        self.c._notify("已归档")
        self.assertIsNone(self.c._notify_card_id)

    def test_click_on_deleted_card_is_noop(self):
        """提醒后卡片被删：点击气泡不该抛错"""
        card_id = self._add_due_card("今天交", date.today())
        self.c._check_due_dates()
        self.c._on_card_delete(self.c._store.load().lists[0].id, card_id)
        opened: list = []
        self.c._on_card_edit = lambda lid, cid: opened.append(cid)
        self.c._on_notification_clicked()
        self.assertEqual(opened, [])


class DailySnapshotTest(unittest.TestCase):
    """跨天刷新顺带留一份快照（常驻不重启也有版本历史）"""

    @classmethod
    def setUpClass(cls):
        from tests import shared_controller
        cls.c = shared_controller()

    def setUp(self):
        board = self.c._store.load()
        for lst in board.lists:
            lst.cards.clear()
        del board.lists[1:]
        board.lists[0].cards[:] = [Card(title="有卡")]
        self.c._after_data_change(None)
        self.c._flush_store()
        self.c._save_pool.waitForDone(3000)
        self.path = self.c._store.path

    def _snaps(self):
        return sorted(self.path.parent.glob(f"{self.path.name}.snap.*.bak"))

    def test_new_day_creates_snapshot(self):
        before = len(self._snaps())
        self.c._ui_day = date.today() - timedelta(days=1)   # 假装跨了天
        self.c._refresh_if_new_day()
        self.assertEqual(len(self._snaps()), before + 1)

    def test_no_snapshot_when_day_unchanged(self):
        self.c._ui_day = date.today()
        before = len(self._snaps())
        self.c._refresh_if_new_day()
        self.assertEqual(len(self._snaps()), before)

    def test_empty_board_creates_no_snapshot(self):
        board = self.c._store.load()
        for lst in board.lists:
            lst.cards.clear()
        self.c._after_data_change(None)
        self.c._flush_store()
        self.c._save_pool.waitForDone(3000)
        before = len(self._snaps())
        self.c._ui_day = date.today() - timedelta(days=1)
        self.c._refresh_if_new_day()
        self.assertEqual(len(self._snaps()), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)

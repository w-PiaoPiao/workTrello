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

from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

_qapp = QApplication.instance() or QApplication([])

from app.config import AppConfig
from app.controllers.app_controller import AppController
from app.models.board import BoardList, Card
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

    # ── 列表删除 ──────────────────────────────────────────

    def test_delete_empty_list_without_dialog(self):
        """空列表删除不弹确认框、可撤销（回归：reply 未定义曾致 UnboundLocalError）"""
        self._reset()
        board = self.c._store.load()
        target = board.lists[0]
        n_lists = len(board.lists)
        with patch.object(QMessageBox, "question") as q:
            self.c._on_list_delete(target.id)
            q.assert_not_called()
        board = self.c._store.load()
        self.assertEqual(len(board.lists), n_lists - 1)
        self.assertIsNone(board.find_list(target.id))
        self.c._on_undo_requested(False)
        board = self.c._store.load()
        self.assertIsNotNone(board.find_list(target.id))

    def test_delete_nonempty_list_cancel_keeps_list(self):
        self._reset()
        self.c._on_card_add(self._list().id, "占位卡")
        board = self.c._store.load()
        target = board.lists[0]
        with patch.object(QMessageBox, "question",
                          return_value=QMessageBox.No):
            self.c._on_list_delete(target.id)
        board = self.c._store.load()
        self.assertIsNotNone(board.find_list(target.id))

    def test_delete_nonempty_list_confirm_and_undo(self):
        self._reset()
        self.c._on_card_add(self._list().id, "占位卡")
        board = self.c._store.load()
        target = board.lists[0]
        n_lists = len(board.lists)
        with patch.object(QMessageBox, "question",
                          return_value=QMessageBox.Yes):
            self.c._on_list_delete(target.id)
        board = self.c._store.load()
        self.assertEqual(len(board.lists), n_lists - 1)
        self.assertIsNone(board.find_list(target.id))
        self.c._on_undo_requested(False)
        board = self.c._store.load()
        self.assertIsNotNone(board.find_list(target.id))
        self.assertEqual(
            [x.title for x in board.find_list(target.id).cards], ["占位卡"])

    # ── 列表拖拽重排 ──────────────────────────────────────

    def _ensure_lists(self, n=3):
        board = self.c._store.load()
        while len(board.lists) < n:
            board.lists.append(BoardList(title=f"列{len(board.lists)}"))
        return [lst.id for lst in board.lists]

    def test_list_move_reorders_and_undo(self):
        self._reset()
        ids = self._ensure_lists()
        self.c._on_list_move(ids[0], ids[2], True)   # A 移到 C 前面
        board = self.c._store.load()
        self.assertEqual([lst.id for lst in board.lists],
                         [ids[1], ids[0], ids[2]])
        self.c._on_undo_requested(False)             # 撤销 → 还原列序
        board = self.c._store.load()
        self.assertEqual([lst.id for lst in board.lists], ids)

    def test_list_move_after_puts_at_end(self):
        self._reset()
        ids = self._ensure_lists()
        self.c._on_list_move(ids[0], ids[2], False)  # A 移到 C 后面（末尾）
        board = self.c._store.load()
        self.assertEqual([lst.id for lst in board.lists],
                         [ids[1], ids[2], ids[0]])

    def test_list_move_noop_when_unchanged(self):
        """拖回原相邻位置不产生撤销快照、不刷新"""
        self._reset()
        ids = self._ensure_lists()
        self.c._on_list_move(ids[0], ids[1], True)   # A 本就在 B 前 → 无变化
        self.assertEqual([lst.id for lst in self.c._store.load().lists], ids)
        self.assertEqual(self.c._undo_stack, [])
        self.c._on_list_move(ids[1], ids[1], False)  # 拖到自己 → 直接返回
        self.assertEqual(self.c._undo_stack, [])

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

    # ── 归档 ──────────────────────────────────────────────

    def test_archive_and_restore_with_undo(self):
        self._reset()
        self.c._on_card_add(self._list().id, "要归档的卡")
        card_id = self._list().cards[0].id
        self.c._on_card_archive(card_id)
        board = self.c._store.load()
        self.assertTrue(board.find_card(card_id)[1].archived)
        # 归档卡片仍在模型里，但看板视图不再显示
        self.assertEqual(self.c._board_view._columns[0]._card_widgets, [])
        self.c._on_undo_requested(False)                             # 撤销归档
        board = self.c._store.load()
        self.assertFalse(board.find_card(card_id)[1].archived)

    def test_archive_dialog_refresh(self):
        self._reset()
        self.c._on_card_add(self._list().id, "归档对话框测试")
        card_id = self._list().cards[0].id
        self.c._on_card_archive(card_id)
        self.c._on_archive_open()
        dlg = self.c._archive_dialog
        self.assertIsNotNone(dlg)
        texts = "\n".join(w.text() for w in dlg.findChildren(QLabel))
        self.assertIn("归档对话框测试", texts)
        self.assertIn("本周完成", texts)
        dlg.close()
        self._reset()

    def test_archive_refresh_skipped_when_unchanged(self):
        """归档内容未变时数据变更不重建对话框（指纹跳过）"""
        self._reset()
        self.c._on_card_add(self._list().id, "指纹卡")
        card_id = self._list().cards[0].id
        self.c._on_card_archive(card_id)
        self.c._on_archive_open()
        dlg = self.c._archive_dialog
        self.assertIsNotNone(dlg)
        calls = []
        orig = dlg.set_items
        dlg.set_items = lambda *a, **k: (calls.append(1), orig(*a, **k))
        try:
            self.c._refresh_archive()          # 归档未变 → 跳过重建
            self.assertEqual(calls, [])
            self.c._on_card_restore(card_id)   # 恢复 → 内容变化 → 重建一次
            self.assertEqual(calls, [1])
        finally:
            dlg.close()
            self._reset()

    # ── 导出 ──────────────────────────────────────────────

    def test_export_markdown_and_csv(self):
        self._reset()
        board = self.c._store.load()
        lst0 = board.lists[0]
        lst0.cards.append(Card(title="写周报", notes="含项目进度",
                               due_date="2026-09-10", labels=["blue"]))
        lst0.cards.append(Card(title="已完成项", done=True, pomodoros=2))
        self.c._after_data_change(None)

        tmp = Path(tempfile.mkdtemp())
        md = tmp / "out.md"
        self.c._write_export_md(md, board)
        text = md.read_text(encoding="utf-8")
        self.assertIn("## " + lst0.title, text)
        self.assertIn("- [ ] 写周报 📅 2026-09-10", text)
        self.assertIn("- [x] 已完成项 🍅×2", text)
        self.assertIn("含项目进度", text)

        csv_path = tmp / "out.csv"
        self.c._write_export_csv(csv_path, board)
        content = csv_path.read_text(encoding="utf-8-sig")
        self.assertIn("写周报", content)
        self.assertIn("blue", content)

    # ── 番茄钟 ────────────────────────────────────────────

    def test_pomodoro_finish_increments_and_wakes(self):
        self._reset()
        self.c._on_card_add(self._list().id, "专注目标")
        card_id = self._list().cards[0].id
        with patch.object(AppConfig, "POMODORO_MINUTES", 0):
            self.c._on_card_pomo(card_id)   # 时长 0
            self.c._pomo_tick()             # 手动触发一跳 → 立即完成
        board = self.c._store.load()
        self.assertEqual(board.find_card(card_id)[1].pomodoros, 1)
        self.assertIsNone(self.c._pomo_card_id)
        self.assertIsNone(self.c._pet_view._badge_override)

    def test_pomodoro_stop_keeps_count(self):
        self._reset()
        self.c._on_card_add(self._list().id, "专注中断")
        card_id = self._list().cards[0].id
        self.c._on_card_pomo(card_id)
        self.assertEqual(self.c._pomo_card_id, card_id)
        self.assertIsNotNone(self.c._pet_view._badge_override)  # 倒计时覆盖角标
        self.c._on_card_pomo(card_id)      # 再次触发 = 停止
        self.assertIsNone(self.c._pomo_card_id)
        self.assertIsNone(self.c._pet_view._badge_override)
        board = self.c._store.load()
        self.assertEqual(board.find_card(card_id)[1].pomodoros, 0)

    def test_archive_focusing_card_stops_pomodoro(self):
        self._reset()
        self.c._on_card_add(self._list().id, "专注中被归档")
        card_id = self._list().cards[0].id
        self.c._on_card_pomo(card_id)
        self.c._on_card_archive(card_id)
        self.assertIsNone(self.c._pomo_card_id)

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


@unittest.skipIf(AppConfig.IS_MACOS, "键盘入口仅非 macOS 构建")
class KeyboardShortcutTest(unittest.TestCase):
    """Windows/Linux：Ctrl+Z 经窗口信号触发控制器撤销"""

    @classmethod
    def setUpClass(cls):
        cls.c = AppController()

    def setUp(self):
        board = self.c._store.load()
        for lst in board.lists:
            lst.cards.clear()
        self.c._after_data_change(None)
        self.c._undo_stack.clear()

    def _titles(self):
        return [x.title for x in self.c._store.load().lists[0].cards]

    def test_ctrl_z_signal_wired_to_undo(self):
        self.c._on_card_add(self.c._store.load().lists[0].id, "键盘撤销")
        self.assertEqual(self._titles(), ["键盘撤销"])
        self.c._window.undo_shortcut.emit()
        self.assertEqual(self._titles(), [])


@unittest.skipUnless(AppConfig.IS_MACOS, "全局菜单栏仅 macOS 构建")
class MenuBarTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = AppController()

    def test_menu_actions_exist(self):
        self.assertTrue(self.c._menu_act_today.isCheckable())
        self.assertTrue(self.c._menu_act_dark.isCheckable())
        self.assertEqual(self.c._menu_act_always_top.isChecked(),
                         self.c._window.is_always_on_top())
        self.assertEqual(self.c._menu_act_dark.isChecked(),
                         AppTheme.mode() == "dark")

    def test_menu_today_syncs_board(self):
        self.c._menu_act_today.setChecked(True)
        self.assertTrue(self.c._board_view.is_today_mode())
        self.c._menu_act_today.setChecked(False)
        self.assertFalse(self.c._board_view.is_today_mode())

    def test_board_today_syncs_menu(self):
        self.c._board_view.set_today_mode(True)
        self.assertTrue(self.c._menu_act_today.isChecked())
        self.c._board_view.set_today_mode(False)
        self.assertFalse(self.c._menu_act_today.isChecked())

    def test_menu_always_top_syncs_all_entries(self):
        self.c._menu_act_always_top.setChecked(False)
        self.assertFalse(self.c._window.is_always_on_top())
        self.assertFalse(self.c._pet_view._act_always_top.isChecked())
        self.assertFalse(self.c._tray._always_top_action.isChecked())
        self.c._menu_act_always_top.setChecked(True)
        self.assertTrue(self.c._window.is_always_on_top())
        self.assertTrue(self.c._pet_view._act_always_top.isChecked())
        self.assertTrue(self.c._tray._always_top_action.isChecked())

    def test_menu_dark_toggle_with_save_patched(self):
        """菜单切换主题走 save_theme_mode（测试中打桩避免污染用户设置）"""
        with patch.object(AppConfig, "save_theme_mode",
                          classmethod(lambda cls, m: None)):
            self.c._menu_act_dark.setChecked(True)
            self.assertEqual(AppTheme.mode(), "dark")
            self.c._menu_act_dark.setChecked(False)
            self.assertEqual(AppTheme.mode(), "light")

    def test_undo_menu_action_registered(self):
        act = next(a for m in self.c._menu_bar.actions()
                   for a in m.menu().actions() if a.text() == "撤销")
        self.assertEqual(act.shortcut(), QKeySequence.Undo)


if __name__ == "__main__":
    unittest.main(verbosity=2)

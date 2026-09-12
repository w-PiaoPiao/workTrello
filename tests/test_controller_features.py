# -*- coding: utf-8 -*-
"""
控制器新特性测试：撤销快照栈、截止提醒统计、系统深浅色跟随

需要 Qt 离屏环境；数据目录隔离到临时目录（在导入 app 模块前设置）。
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
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
        # 启动已改为折叠态延迟构建看板：测试直接断言列/卡控件，先模拟
        # 一次首次展开把看板 UI 构建出来
        cls.c._ensure_board_ui()

    def _list(self):
        return self.c._store.load().lists[0]

    def _titles(self):
        return [x.title for x in self._list().cards]

    def _flush_sync(self):
        """触发防抖落盘并等后台写线程收口

        落盘已移到后台单线程池（flush_async）；断言文件内容前必须等待
        队列完成，否则与 worker 竞态。
        """
        self.c._flush_store()
        self.assertTrue(self.c._save_pool.waitForDone(3000))

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

    # ── 空板恢复引导 / 落盘可靠性 ─────────────────────────

    def _seed_snapshot(self, title="快照里的卡"):
        """在数据目录预置一份含卡的启动快照（模拟历史数据）"""
        import json
        path = self.c._store.path
        path.parent.mkdir(parents=True, exist_ok=True)
        snap = path.with_name(path.name + ".snap.20260909_100000_000000.bak")
        doc = {"app": "桌宠看板", "lists": [
            {"id": "l1", "title": "待办",
             "cards": [Card(title=title).to_dict()]}]}
        snap.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        return snap

    def test_empty_board_restore_yes(self):
        """空板 + 历史快照：选"是"从快照恢复数据"""
        self._reset()
        snap = self._seed_snapshot()
        try:
            with patch.object(AppConfig, "get_empty_board_ack",
                              return_value=False), \
                 patch.object(QMessageBox, "question",
                              return_value=QMessageBox.Yes):
                self.c._maybe_offer_empty_restore()
            board = self.c._store.load()
            titles = [c.title for lst in board.lists for c in lst.cards]
            self.assertEqual(titles, ["快照里的卡"])
        finally:
            snap.unlink(missing_ok=True)
            self._reset()

    def test_empty_board_restore_no_remembers(self):
        """空板 + 快照：选"否"记住选择，后续启动不再询问"""
        self._reset()
        snap = self._seed_snapshot()
        try:
            with patch.object(AppConfig, "get_empty_board_ack",
                              return_value=False), \
                 patch.object(QMessageBox, "question",
                              return_value=QMessageBox.No), \
                 patch.object(AppConfig, "set_empty_board_ack") as set_ack:
                self.c._maybe_offer_empty_restore()
            set_ack.assert_called_once_with(True)
            # 已记住 → 不再弹窗
            with patch.object(AppConfig, "get_empty_board_ack",
                              return_value=True), \
                 patch.object(QMessageBox, "question") as q:
                self.c._maybe_offer_empty_restore()
            q.assert_not_called()
        finally:
            snap.unlink(missing_ok=True)
            self._reset()

    def test_flush_failure_notifies_user(self):
        """落盘失败经信号回主线程通知用户（不静默丢写）并安排重试"""
        self.c._last_save_error_notify = None
        with patch.object(self.c._tray, "show_notification") as notify:
            self.c._on_save_failed(
                "保存失败 (board.json): 磁盘满")
        notify.assert_called_once()

    def test_flush_with_cards_clears_empty_ack(self):
        """保存含卡数据后清除"已确认空板"标记（下次真空重新询问）

        清除带存在性守卫：ack 缺席（绝大多数时间）不触发 remove。
        """
        self._reset()
        self.c._on_card_add(self._list().id, "有卡")
        # ack 缺席 → 不做无谓的 remove
        with patch.object(AppConfig, "get_empty_board_ack",
                          return_value=False), \
             patch.object(AppConfig, "clear_empty_board_ack") as clear:
            self._flush_sync()
        clear.assert_not_called()
        # ack 存在 → 清除
        with patch.object(AppConfig, "get_empty_board_ack",
                          return_value=True), \
             patch.object(AppConfig, "clear_empty_board_ack") as clear:
            self._flush_sync()
        clear.assert_called_once()

    def test_bulk_add_lines(self):
        """批量添加：每行一张保序、速记语法生效、撤销一次回滚整批"""
        self._reset()
        lst = self._list()
        from app.views.quick_add_dialog import BulkAddDialog
        with patch("app.views.quick_add_dialog.BulkAddDialog") as dlg_cls:
            dlg = dlg_cls.return_value
            dlg.exec.return_value = BulkAddDialog.Accepted
            dlg.text.return_value = "第三张\n改bug !P1 #红\n9月20日交房租\n"
            dlg.list_index.return_value = 0   # 第一个列表
            self.c._on_bulk_add()
        cards = self._list().cards
        self.assertEqual([c.title for c in cards[:3]],
                         ["第三张", "改bug", "交房租"])
        self.assertEqual(cards[1].priority, 1)
        self.assertEqual(cards[1].labels, ["red"])
        self.assertEqual(cards[2].due_date,
                         date(date.today().year, 9, 20).isoformat())
        # 撤销一次回滚整批（单个撤销快照）
        self.c._on_undo_requested(False)
        self.assertEqual([c.title for c in self._list().cards][:2], [])

    def test_edit_marks_store_dirty(self):
        """数据变更必须标脏——flush() 只在脏时落盘，漏标则编辑只留内存"""
        self._reset()
        self.c._store._dirty = False
        self.c._on_card_add(self._list().id, "脏标记")
        self.assertTrue(self.c._store._dirty)

    def test_card_star_toggle_with_undo(self):
        """右键星标 toggle：加入/移出今日，撤销可回滚（星标一键回归护栏）"""
        self._reset()
        self.c._on_card_add(self._list().id, "星标卡")
        card = self._list().cards[0]
        self.assertFalse(card.starred)
        self.c._on_card_star(card.id)
        self.assertTrue(self._list().cards[0].starred)
        self.c._on_card_star(card.id)
        self.assertFalse(self._list().cards[0].starred)
        # 撤销"移出" → 恢复星标
        self.c._on_undo_requested(False)
        self.assertTrue(self._list().cards[0].starred)
        self.assertFalse(self._list().cards[0].done)   # 不误改完成态

    # ── 工作目录 ──────────────────────────────────────────

    def test_workdir_open_missing_dir_notifies(self):
        """目录不存在（外接盘未连接是常态）：走 _notify 轻提示，不崩不弹框"""
        self._reset()
        self.c._on_card_add(self._list().id, "离线盘任务")
        card = self._list().cards[0]
        card.workdir = "/Volumes/不存在的移动硬盘/proj"
        notes = []
        self.c._notify = lambda text: notes.append(text)
        self.c._on_card_workdir_open(card.id)
        self.assertEqual(notes, ["工作目录无法访问（设备可能未连接）"])

    def test_workdir_open_calls_desktop_services(self):
        """目录可达：经 QDesktopServices 打开"""
        self._reset()
        self.c._on_card_add(self._list().id, "有目录任务")
        card = self._list().cards[0]
        card.workdir = tempfile.mkdtemp()          # 测试中真实存在的目录
        opened = []
        with patch("app.controllers.app_controller.QDesktopServices") as ds, \
             patch("app.controllers.app_controller.QUrl") as qurl:
            ds.openUrl.side_effect = lambda url: opened.append(url)
            self.c._on_card_workdir_open(card.id)
        self.assertEqual(len(opened), 1)
        qurl.fromLocalFile.assert_called_once_with(card.workdir)

    def test_workdir_open_without_dir_short_circuit(self):
        """卡片无目录（不应出现的状态）也安全：走提示而非异常"""
        self._reset()
        self.c._on_card_add(self._list().id, "无目录任务")
        card_id = self._list().cards[0].id
        notes = []
        self.c._notify = lambda text: notes.append(text)
        self.c._on_card_workdir_open(card_id)     # workdir 为空串
        self.assertEqual(len(notes), 1)

    def test_workdir_set_persists_with_undo(self):
        """右键快捷设置目录：写卡 + 撤销可回滚"""
        self._reset()
        self.c._on_card_add(self._list().id, "要设目录的任务")
        card_id = self._list().cards[0].id
        target = tempfile.mkdtemp()
        with patch("app.controllers.app_controller.QFileDialog") as fdlg:
            fdlg.getExistingDirectory.return_value = target
            self.c._on_card_workdir_set(card_id)
        self.assertEqual(self._list().cards[0].workdir, target)
        # 撤销 → 目录清空（回到设置前快照）
        self.c._on_undo_requested(False)
        self.assertEqual(self._list().cards[0].workdir, "")
        self._flush_sync()
        doc = json.loads(AppConfig.board_path().read_text(encoding="utf-8"))
        card_doc = [c for lst in doc["lists"] for c in lst["cards"]
                    if c["title"] == "要设目录的任务"][0]
        self.assertEqual(card_doc["workdir"], "")

    def test_workdir_set_cancelled_keeps_state(self):
        """选择器取消（返回空串）：不改卡、不标脏"""
        self._reset()
        self.c._on_card_add(self._list().id, "取消设置")
        card = self._list().cards[0]
        self.c._store._dirty = False
        with patch("app.controllers.app_controller.QFileDialog") as fdlg:
            fdlg.getExistingDirectory.return_value = ""
            self.c._on_card_workdir_set(card.id)
        self.assertEqual(card.workdir, "")
        self.assertFalse(self.c._store._dirty)

    def test_edit_persists_to_disk(self):
        """编辑落到 board.json（回归护栏）

        曾漏掉 mark_dirty：_after_data_change 只启动防抖计时器不标脏，
        计时器到点后 flush() 因不脏直接返回，所有编辑停留在内存、退出即丢，
        磁盘上永远停在首次启动那份默认空板。
        """
        self._reset()
        self._flush_sync()                       # 先落下空板，模拟"数据文件已存在"
        self.c._on_card_add(self._list().id, "必须落盘的卡")
        self._flush_sync()                       # 防抖计时器到点 / 退出冲刷
        doc = json.loads(AppConfig.board_path().read_text(encoding="utf-8"))
        titles = [c["title"] for lst in doc["lists"] for c in lst["cards"]]
        self.assertEqual(titles, ["必须落盘的卡"])

    def test_card_mutations_persist(self):
        """完成 / 删除 / 拖拽等改卡操作同样落盘（不只新增）"""
        self._reset()
        self.c._on_card_add(self._list().id, "甲")
        self.c._on_card_add(self._list().id, "乙")
        self._flush_sync()
        lst = self._list()
        # 完成第一张（列表序为 [乙, 甲]）
        self.c._on_card_done(lst.id, lst.cards[0].id, True)
        self._flush_sync()
        doc = json.loads(AppConfig.board_path().read_text(encoding="utf-8"))
        done = {c["title"]: c["done"] for l in doc["lists"] for c in l["cards"]}
        self.assertTrue(done["乙"])
        self.assertFalse(done["甲"])

    def test_flush_async_writes_and_clears_dirty(self):
        """flush_async 提交即清脏并经单线程池落盘（后台落盘回归护栏）"""
        self._reset()
        self.c._on_card_add(self._list().id, "异步落盘")
        self.c._store.flush_async(self.c._save_pool)
        self.assertFalse(self.c._store._dirty)   # 快照取走即清脏
        self.assertTrue(self.c._save_pool.waitForDone(3000))
        doc = json.loads(AppConfig.board_path().read_text(encoding="utf-8"))
        titles = [c["title"] for lst in doc["lists"] for c in lst["cards"]]
        self.assertEqual(titles, ["异步落盘"])

    def test_quit_flushes_remaining_dirty_data(self):
        """退出兜底：等在途后台写完成后，同步 flush 剩余脏数据（含 fsync）"""
        self._reset()
        self.c._on_card_add(self._list().id, "退出兜底")
        self.c._store.mark_dirty()   # 模拟后台写提交后又发生编辑
        self.c._on_about_to_quit()
        doc = json.loads(AppConfig.board_path().read_text(encoding="utf-8"))
        titles = [c["title"] for lst in doc["lists"] for c in lst["cards"]]
        self.assertEqual(titles, ["退出兜底"])
        self.assertFalse(self.c._store._dirty)

    # ── 截止提醒（逐卡检查，每天每卡只提醒一次） ─────────────

    def test_due_check_reminds_each_card_once_per_day(self):
        self._reset()
        board = self.c._store.load()
        for lst in board.lists:
            for x in lst.cards:
                x.due_date = None
        self.c._after_data_change(None)
        # 一张今天到期、一张已逾期（标题避开日期词——速记会把"今天"消费为截止日）
        self.c._on_card_add(self._list().id, "当天到期")
        self._list().cards[0].due_date = date.today().isoformat()
        self.c._on_card_add(self._list().id, "已逾期")
        self._list().cards[0].due_date = (
            date.today() - timedelta(days=1)).isoformat()
        self.c._after_data_change(None)

        log_saved: dict = {}

        def fake_save(log):
            log_saved.clear()
            log_saved.update(log)

        with patch.object(self.c._tray, "show_notification") as notify, \
                patch.object(AppConfig, "get_remind_log",
                             return_value={}), \
                patch.object(AppConfig, "save_remind_log",
                             side_effect=fake_save):
            self.c._check_due_dates()
        notify.assert_called_once()
        self.assertIn("已逾期", notify.call_args.args[0])
        self.assertIn("当天到期", notify.call_args.args[0])

        # 当天签名已入库：重复检查不触发通知、不写日志
        with patch.object(self.c._tray, "show_notification") as notify2, \
                patch.object(AppConfig, "get_remind_log",
                             return_value=log_saved), \
                patch.object(AppConfig, "save_remind_log") as save2:
            self.c._check_due_dates()
        notify2.assert_not_called()
        save2.assert_not_called()

    def test_due_check_skips_done_archived_and_future(self):
        self._reset()
        self.c._on_card_add(self._list().id, "未来卡")
        self._list().cards[0].due_date = (
            date.today() + timedelta(days=3)).isoformat()
        self.c._on_card_add(self._list().id, "已完成")
        self._list().cards[0].due_date = date.today().isoformat()
        self._list().cards[0].done = True
        self.c._after_data_change(None)
        with patch.object(self.c._tray, "show_notification") as notify, \
                patch.object(AppConfig, "get_remind_log",
                             return_value={}), \
                patch.object(AppConfig, "save_remind_log"):
            self.c._check_due_dates()
        notify.assert_not_called()

    # ── 重复任务 ──────────────────────────────────────────

    def test_daily_repeat_cards_roll_on_done(self):
        self._reset()
        self.c._on_card_add(self._list().id, "每日晨会")
        card = self._list().cards[0]
        card.due_date = date.today().isoformat()
        card.repeat = "daily"
        self.c._after_data_change(None)
        self.c._on_card_done(self._list().id, card.id, True)
        c = self._list().cards[0]
        self.assertEqual(
            c.due_date, (date.today() + timedelta(days=1)).isoformat())
        self.assertFalse(c.done)
        self.assertIsNone(c.done_at)

    def test_card_done_maintains_done_at(self):
        """勾选框完成写 done_at（今日统计/彩蛋/周统计共用）；取消则清空"""
        self._reset()
        self.c._on_card_add(self._list().id, "勾选完成")
        card = self._list().cards[0]
        self.c._on_card_done(self._list().id, card.id, True)
        self.assertIsNotNone(card.done_at)
        self.assertEqual(self.c._store.load().today_done_count(), 1)
        self.c._on_card_done(self._list().id, card.id, False)
        self.assertIsNone(card.done_at)
        self.assertEqual(self.c._store.load().today_done_count(), 0)

    def test_pet_skin_selected_saves(self):
        with patch.object(AppConfig, "save_pet_skin") as save:
            self.c._on_pet_skin_selected("snow")
        save.assert_called_once_with("snow")

    # ── 备份导入导出 ──────────────────────────────────────

    def test_backup_export_import_roundtrip(self):
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        self._reset()
        self.c._on_card_add(self._list().id, "备份卡")
        card_id = self._list().cards[0].id
        tmp = Path(tempfile.mkdtemp()) / "backup.json"
        with patch.object(QFileDialog, "getSaveFileName",
                          return_value=(str(tmp), "JSON (*.json)")):
            self.c._on_export_backup()
        self.assertTrue(tmp.exists())

        # 改掉内容后再导入：应恢复备份里的标题
        for lst in self.c._store.load().lists:
            for x in lst.cards:
                x.title = "被改"
        self.c._after_data_change(None)
        with patch.object(QFileDialog, "getOpenFileName",
                          return_value=(str(tmp), "JSON (*.json)")), \
                patch.object(QMessageBox, "question",
                             return_value=QMessageBox.Yes):
            self.c._on_import_backup()
        titles = [c.title for lst in self.c._store.load().lists
                  for c in lst.cards if c.id == card_id]
        self.assertEqual(titles, ["备份卡"])
        self.assertEqual(self.c._undo_stack, [])   # 导入清空撤销栈

    def test_backup_import_bad_file_shows_error(self):
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        bad = Path(tempfile.mkdtemp()) / "bad.json"
        bad.write_text("not-json{{{", encoding="utf-8")
        with patch.object(QFileDialog, "getOpenFileName",
                          return_value=(str(bad), "JSON (*.json)")), \
                patch.object(self.c, "_show_error") as err:
            self.c._on_import_backup()
        err.assert_called_once()

    # ── 列表折叠持久化 ────────────────────────────────────

    def test_list_collapsed_persists(self):
        self._reset()
        lst_id = self._list().id
        with patch.object(AppConfig, "save_collapsed_lists") as save:
            self.c._on_list_collapsed(lst_id, True)
            saved = save.call_args.args[0]
            self.assertIn(lst_id, saved)
            self.c._on_list_collapsed(lst_id, False)
            saved2 = save.call_args.args[0]
            self.assertNotIn(lst_id, saved2)

    # ── 今日清单浮窗 ──────────────────────────────────────

    def test_today_list_open_and_row_removed_on_done(self):
        self._reset()
        self.c._on_card_add(self._list().id, "今日任务")
        self._list().cards[0].due_date = date.today().isoformat()
        self.c._on_card_add(self._list().id, "未来的任务")
        self._list().cards[0].due_date = (
            date.today() + timedelta(days=2)).isoformat()
        self.c._after_data_change(None)

        self.c._on_today_list_open()
        pop = self.c._today_popover
        self.assertIsNotNone(pop)
        self.assertEqual(len(pop._rows), 1)     # 只含今日聚焦卡
        card_id = pop._rows[0][1]
        self.c._on_card_done(self._list().id, card_id, True)
        self.assertEqual(len(pop._rows), 0)     # 完成后行移除
        pop.close()

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

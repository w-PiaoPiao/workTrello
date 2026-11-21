# -*- coding: utf-8 -*-
"""
多看板工作区单元测试：索引加载/迁移、新建/重命名/删除、当前看板切换

用法：python tests/test_workspace.py
"""

import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

os.environ["PET_BOARD_DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.board import Board, BoardList, Card
from app.models.workspace import (
    BOARDS_DIR_NAME,
    WORKSPACE_FILE,
    Workspace,
)


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _ws(self) -> Workspace:
        ws = Workspace(self.data_dir)
        ws.load()
        return ws

    def test_first_load_creates_default_board(self):
        ws = self._ws()
        self.assertEqual(len(ws.boards), 1)
        self.assertEqual(ws.current_id, ws.boards[0].id)
        self.assertTrue(ws.board_path(ws.current_id).exists())
        # 索引落盘
        doc = json.loads((self.data_dir / WORKSPACE_FILE).read_text("utf-8"))
        self.assertEqual(doc["current"], ws.current_id)

    def test_legacy_board_json_migrates(self):
        """旧版单看板 board.json 首次加载迁移到 boards/<id>.json"""
        legacy = self.data_dir / "board.json"
        legacy.write_text(json.dumps({
            "lists": [{"title": "旧数据列",
                       "cards": [Card(title="旧卡片").to_dict()]}],
        }, ensure_ascii=False), encoding="utf-8")
        ws = self._ws()
        self.assertEqual(len(ws.boards), 1)
        path = ws.board_path(ws.current_id)
        self.assertTrue(path.exists())
        doc = json.loads(path.read_text("utf-8"))
        self.assertEqual(doc["lists"][0]["cards"][0]["title"], "旧卡片")
        self.assertIn("name", doc)            # 新结构字段补齐
        self.assertIn("id", doc)
        # 旧文件保留（额外安全网，不删除）
        self.assertTrue(legacy.exists())

    def test_index_reload_preserves_boards_and_current(self):
        ws = self._ws()
        first_id = ws.current_id
        ws.create_board(name="第二块")
        second_id = ws.current_id
        # 重新加载（模拟下次启动）
        ws2 = self._ws()
        self.assertEqual(ws2.order(), [first_id, second_id])
        self.assertEqual(ws2.current_id, second_id)   # 记住上次打开的看板

    def test_create_switches_current_and_persists_name(self):
        ws = self._ws()
        meta = ws.create_board(name="工作项目")
        self.assertEqual(ws.current_id, meta.id)
        doc = json.loads(ws.board_path(meta.id).read_text("utf-8"))
        self.assertEqual(doc["name"], "工作项目")
        self.assertEqual(len(doc["lists"]), 3)   # 默认三列

    def test_create_with_board_data(self):
        board = Board(lists=[BoardList(title="导入列",
                                       cards=[Card(title="导入卡")])],
                      name="导入板")
        meta = self._ws().create_board(name="导入板", board=board)
        doc = json.loads(
            (self.data_dir / BOARDS_DIR_NAME / f"{meta.id}.json")
            .read_text("utf-8"))
        self.assertEqual(doc["lists"][0]["cards"][0]["title"], "导入卡")

    def test_rename_updates_meta_and_file(self):
        ws = self._ws()
        bid = ws.current_id
        self.assertTrue(ws.rename_board(bid, "新名字"))
        self.assertEqual(ws.meta(bid).name, "新名字")
        doc = json.loads(ws.board_path(bid).read_text("utf-8"))
        self.assertEqual(doc["name"], "新名字")
        self.assertFalse(ws.rename_board("不存在的id", "x"))

    def test_delete_refuses_last_board_and_cleans_files(self):
        ws = self._ws()
        bid = ws.current_id
        self.assertFalse(ws.delete_board(bid))   # 只有一块，拒绝
        meta = ws.create_board(name="临时的")
        path = ws.board_path(meta.id)
        self.assertTrue(path.exists())
        self.assertTrue(ws.delete_board(meta.id))
        self.assertFalse(path.exists())
        self.assertEqual(ws.current_id, bid)     # 删除后回到剩余看板
        self.assertNotIn(meta.id, ws.order())

    def test_delete_current_switches_to_remaining(self):
        ws = self._ws()
        first = ws.current_id
        second = ws.create_board(name="B").id
        self.assertTrue(ws.delete_board(second))
        self.assertEqual(ws.current_id, first)

    def test_set_current_rejects_unknown(self):
        ws = self._ws()
        self.assertFalse(ws.set_current("不存在"))
        self.assertEqual(ws.current_id, ws.boards[0].id)


class DeleteFailureTest(unittest.TestCase):
    """回归：删除看板文件失败（被占用/权限）时索引与磁盘必须保持一致"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.ws = Workspace(self.data_dir)
        self.ws.load()
        self.second = self.ws.create_board("第二块")

    def tearDown(self):
        self.tmp.cleanup()

    def test_delete_failure_keeps_index_and_disk(self):
        """unlink 失败 → 索引不摘除、current 不变、重启后看板仍在

        索引先摘除会让残留文件下次启动被孤儿收编"复活"，与确认框
        承诺矛盾；当前会话的 store 已作废，后续编辑还会静默丢失。
        """
        from unittest import mock

        target = self.second.id
        path = self.ws.board_path(target)
        self.assertEqual(self.ws.current_id, target)

        with mock.patch.object(Path, "unlink", side_effect=OSError("被占用")):
            ok = self.ws.delete_board(target)

        self.assertFalse(ok)
        self.assertIsNotNone(self.ws.meta(target))      # 索引项保留
        self.assertEqual(self.ws.current_id, target)    # current 未切换
        self.assertTrue(path.exists())                  # 文件仍在
        # 重启（重新加载索引）后看板依然在
        ws2 = Workspace(self.data_dir)
        ws2.load()
        self.assertIsNotNone(ws2.meta(target))

    def test_delete_success_removes_index_and_file(self):
        target = self.second.id
        path = self.ws.board_path(target)
        self.assertTrue(self.ws.delete_board(target))
        self.assertIsNone(self.ws.meta(target))
        self.assertFalse(path.exists())


class GhostEntryPurgeTest(unittest.TestCase):
    """E-02：delete 后索引写失败 → 重启不得出现幽灵空板；失败要通知"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_failure_notifies_callback(self):
        """索引落盘失败回调 save_failed_cb（与看板数据同一通知通道）"""
        from unittest import mock
        ws = Workspace(self.data_dir)
        ws.load()
        seen = []
        ws.save_failed_cb = lambda e: seen.append(e)
        with mock.patch("app.models.workspace.atomic_write_json",
                        side_effect=OSError("磁盘满")):
            ws._save()   # 直接驱动索引写入（create_board 还含板文件写）
        self.assertEqual(len(seen), 1)
        self.assertIsInstance(seen[0], OSError)

    def test_ghost_entry_purged_on_reload(self):
        """索引指向已删除的板文件：重启加载时剔除该条目，不复活空板"""
        ws = Workspace(self.data_dir)
        ws.load()
        second = ws.create_board("会被删的板")
        first = ws.boards[0].id
        self.assertTrue(ws.delete_board(second.id))
        # 模拟"delete 后 _save 失败"：手改索引文件把已删板加回去
        doc = json.loads((self.data_dir / WORKSPACE_FILE).read_text("utf-8"))
        doc["boards"].append(second.to_dict())
        (self.data_dir / WORKSPACE_FILE).write_text(
            json.dumps(doc, ensure_ascii=False), encoding="utf-8")

        ws2 = Workspace(self.data_dir)
        ws2.load()
        self.assertIsNone(ws2.meta(second.id),
                          "幽灵条目必须被剔除，否则空板复活")
        self.assertIsNotNone(ws2.meta(first))
        self.assertEqual(ws2.current_id, first)


class CorruptLegacyMigrationTest(unittest.TestCase):
    """回归：损坏的旧看板文件不得被静默迁移成空板"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_corrupt_legacy_not_migrated_as_empty_board(self):
        legacy = self.data_dir / "board.json"
        legacy.write_bytes(b"\x80\x81 not valid json \xff")
        ws = Workspace(self.data_dir)
        ws.load()
        # 中止迁移：legacy 原文件保留（+ .corrupt 隔离副本），
        # 索引里是全新默认板而非"迁移成功"的空板
        self.assertTrue(legacy.exists())
        self.assertTrue(list(self.data_dir.glob("board.json.corrupt.*.bak")))
        doc = json.loads(ws.board_path(ws.current_id).read_text("utf-8"))
        self.assertEqual(doc["lists"][0]["cards"], [])


class UnreadableStartupTest(unittest.TestCase):
    """E-07：数据文件暂时不可读（同步盘锁文件/权限抖动）的启动分流

    拒绝恢复后不能置脏落盘——否则一次瞬时锁读就以空板覆盖原文件。
    借用真实的 AppController._load_data（未绑定方法挂到最小骨架上），
    不起完整启动链（完整构造在离屏下有段错误风险，且与本分支无关）。
    """

    @staticmethod
    def _harness(problems):
        import types
        from app.controllers.app_controller import AppController
        calls = {"mark_dirty": 0, "schedule": 0, "errors": []}
        store = types.SimpleNamespace(
            load=lambda: Board.from_dict({}),
            problems=list(problems),
            mark_dirty=lambda: calls.__setitem__(
                "mark_dirty", calls["mark_dirty"] + 1),
        )

        class _Harness:
            _load_data = AppController._load_data
            _offer_restore = lambda self, kinds: False   # 无备份候选
            _preserve_good_copy = lambda self: None
            _show_error = lambda self, msg: calls["errors"].append(msg)
            _apply_board_to_ui = lambda self, board: None
            _schedule_save = lambda self: calls.__setitem__(
                "schedule", calls["schedule"] + 1)

        h = _Harness()
        h._store = store
        return h, calls

    def test_unreadable_only_skips_mark_dirty(self):
        """纯 unreadable：告知用户但不置脏不落盘（原文件原样存活）"""
        h, calls = self._harness([("unreadable", "pd")])
        h._load_data()
        self.assertEqual(calls["mark_dirty"], 0)
        self.assertEqual(calls["schedule"], 0)
        self.assertEqual(len(calls["errors"]), 1)   # 有告知

    def test_corrupted_still_overwrites(self):
        """损坏路径维持既有语义：默认看板置脏落盘，好副本在隔离副本里"""
        h, calls = self._harness([("corrupted", "pd")])
        h._load_data()
        self.assertEqual(calls["mark_dirty"], 1)
        self.assertEqual(calls["schedule"], 1)

    def test_mixed_still_overwrites(self):
        """混合（损坏 + 不可读）：按损坏处理覆盖——文件至少有一份确实坏了"""
        h, calls = self._harness(
            [("unreadable", "pd"), ("corrupted", "pd2")])
        h._load_data()
        self.assertEqual(calls["mark_dirty"], 1)


class QuarantineCountTest(unittest.TestCase):
    """E-03：损坏板文件被隔离出列时计数，启动后控制器据此提示；
    E-09：重命名的文件同步失败以返回值标记（调用方标脏自愈）"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_corrupt_orphan_counted_during_absorb(self):
        """孤儿收编遇损坏文件：不进索引、quarantined_boards 计数"""
        ws = Workspace(self.data_dir)
        ws.load()
        good = ws.create_board("好板")
        # 一块合法孤儿 + 一块损坏孤儿
        orphan = self.data_dir / BOARDS_DIR_NAME / (uuid.uuid4().hex + ".json")
        orphan.write_text(json.dumps(
            {"id": orphan.stem, "name": "孤儿", "lists": []}),
            encoding="utf-8")
        corrupt = self.data_dir / BOARDS_DIR_NAME / (uuid.uuid4().hex + ".json")
        corrupt.write_bytes(b"\x80\x81 broken \xff")

        ws2 = Workspace(self.data_dir)
        ws2.load()
        self.assertEqual(ws2.quarantined_boards, 1)
        self.assertIsNotNone(ws2.meta(good.id))
        self.assertIsNotNone(ws2.meta(orphan.stem))   # 合法孤儿照常收编
        self.assertIsNone(ws2.meta(corrupt.stem))
        self.assertTrue(list(self.data_dir.glob("*.json.corrupt.*.bak")) or
                        list((self.data_dir / BOARDS_DIR_NAME)
                             .glob("*.json.corrupt.*.bak")))

    def test_rename_file_sync_failure_returns_false(self):
        """E-09：重命名的文件同步失败返回 False（调用方标脏自愈）"""
        from unittest import mock
        ws = Workspace(self.data_dir)
        ws.load()
        ws.create_board("改名板")
        bid = ws.current_id
        with mock.patch("app.models.workspace.atomic_write_json",
                        side_effect=OSError("磁盘满")):
            ok = ws.rename_board(bid, "新名字")
        self.assertFalse(ok)
        self.assertEqual(ws.meta(bid).name, "新名字")   # 索引侧已改名


if __name__ == "__main__":
    unittest.main(verbosity=2)

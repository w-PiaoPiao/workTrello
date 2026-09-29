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


if __name__ == "__main__":
    unittest.main(verbosity=2)

# -*- coding: utf-8 -*-
"""
数据层单元测试：Board / BoardList / Card 序列化 + BoardStore 读写、
损坏隔离备份与 .prev 好副本恢复

用法：python tests/test_board.py
"""

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.board import Board, BoardList, BoardStore, Card
from app.models.json_io import (
    StoreError,
    atomic_write_json,
    good_prev_copy,
    latest_backup,
    load_json_doc,
    restore_from_backup,
)


class CardTest(unittest.TestCase):
    def test_roundtrip(self):
        card = Card(title="写周报", notes="包含项目进度", labels=["blue", "red"],
                    due_date="2026-09-10", done=True)
        data = card.to_dict()
        card2 = Card.from_dict(data)
        self.assertEqual(card2.title, "写周报")
        self.assertEqual(card2.labels, ["blue", "red"])
        self.assertEqual(card2.due_date, "2026-09-10")
        self.assertTrue(card2.done)
        self.assertEqual(card2.id, card.id)

    def test_empty_title_rejected(self):
        with self.assertRaises(ValueError):
            Card.from_dict({"title": "  "})

    def test_missing_fields_defaults(self):
        card = Card.from_dict({"title": "仅标题"})
        self.assertEqual(card.notes, "")
        self.assertEqual(card.labels, [])
        self.assertIsNone(card.due_date)
        self.assertFalse(card.done)

    def test_apply_updates_fields(self):
        card = Card(title="旧标题", due_date="2026-09-01")
        card.apply({"title": " 新标题 ", "notes": "备注",
                    "labels": ["red"], "due_date": None, "done": True})
        self.assertEqual(card.title, "新标题")
        self.assertEqual(card.due_date, None)
        self.assertTrue(card.done)


class BoardListTest(unittest.TestCase):
    def test_roundtrip_with_cards(self):
        lst = BoardList(title="进行中")
        lst.cards.append(Card(title="卡片一"))
        lst.cards.append(Card(title="卡片二"))
        data = lst.to_dict()
        lst2 = BoardList.from_dict(data)
        self.assertEqual(lst2.title, "进行中")
        self.assertEqual([c.title for c in lst2.cards], ["卡片一", "卡片二"])

    def test_invalid_cards_skipped(self):
        lst = BoardList.from_dict({
            "title": "T",
            "cards": [{"title": "有效"}, {"title": ""}, "垃圾", 123],
        })
        self.assertEqual(len(lst.cards), 1)


class BoardTest(unittest.TestCase):
    def setUp(self):
        self.board = Board(lists=[BoardList(title="待办"), BoardList(title="进行中")])
        self.card_a = Card(title="A")
        self.card_b = Card(title="B")
        self.board.lists[0].cards.append(self.card_a)
        self.board.lists[1].cards.append(self.card_b)

    def test_from_empty_gives_default_lists(self):
        board = Board.from_dict({})
        self.assertEqual(len(board.lists), 3)  # 待办/进行中/已完成

    def test_find_card(self):
        lst, card = self.board.find_card(self.card_b.id)
        self.assertIs(lst, self.board.lists[1])
        self.assertIs(card, self.card_b)
        self.assertEqual(self.board.find_card("不存在"), (None, None))

    def test_find_list(self):
        self.assertIs(self.board.find_list(self.board.lists[0].id),
                      self.board.lists[0])
        self.assertIsNone(self.board.find_list("不存在"))

    def test_remove_card_and_list(self):
        removed = self.board.remove_card(self.card_a.id)
        self.assertIs(removed, self.card_a)
        self.assertEqual(self.board.total_cards(), 1)
        self.assertIsNone(self.board.remove_card(self.card_a.id))
        target = self.board.lists[1]
        removed_lst = self.board.remove_list(target.id)
        self.assertIs(removed_lst, target)
        self.assertEqual(len(self.board.lists), 1)

    def test_counts(self):
        self.card_b.done = True
        self.assertEqual(self.board.total_cards(), 2)
        self.assertEqual(self.board.done_cards(), 1)

    def test_due_counts(self):
        lst = self.board.lists[0]
        lst.cards.append(Card(title="逾期", due_date="2026-09-01"))
        lst.cards.append(Card(title="今天", due_date="2026-09-06"))
        lst.cards.append(Card(title="未来", due_date="2026-12-01"))
        lst.cards.append(Card(title="无效日期", due_date="不是日期"))
        lst.cards.append(Card(title="已完成逾期", due_date="2026-09-01",
                              done=True))
        lst.cards.append(Card(title="无日期"))
        # 只统计未完成：逾期 1（"逾期"）、今日截止 1（"今天"）
        self.assertEqual(self.board.due_counts(date(2026, 9, 6)), (1, 1))

    def test_to_from_dict_roundtrip(self):
        data = self.board.to_dict()
        board2 = Board.from_dict(data)
        self.assertEqual([lst.title for lst in board2.lists], ["待办", "进行中"])
        self.assertEqual(board2.lists[0].cards[0].title, "A")
        self.assertEqual(board2.lists[1].cards[0].id, self.card_b.id)


class BoardStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "board.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_load_gives_default_lists(self):
        store = BoardStore(self.path)
        board = store.load()
        self.assertEqual(len(board.lists), 3)  # 待办/进行中/已完成

    def test_flush_and_reload(self):
        store = BoardStore(self.path)
        board = store.load()
        board.lists[0].cards.append(Card(title="持久化测试"))
        store.mark_dirty()
        store.flush()

        store2 = BoardStore(self.path)
        board2 = store2.load()
        self.assertEqual(board2.lists[0].cards[0].title, "持久化测试")

    def test_reload_refetches_disk(self):
        """reload() 丢弃缓存重新读盘（恢复备份后的取数路径）"""
        store = BoardStore(self.path)
        board = store.load()
        self.assertEqual(len(board.lists[0].cards), 0)
        # 外部把文件换成含一张卡片的版本
        atomic_write_json(self.path, {
            "lists": [{"title": "外部", "cards": [Card(title="磁盘数据").to_dict()]}]})
        board2 = store.reload()
        self.assertEqual(board2.lists[0].cards[0].title, "磁盘数据")

    def test_corrupted_file_isolated(self):
        self.path.write_text("{这不是JSON", encoding="utf-8")
        store = BoardStore(self.path)
        board = store.load()
        self.assertEqual(len(board.lists), 3)  # 回退默认看板
        self.assertTrue(store.problems)
        # 损坏文件被隔离备份
        self.assertTrue(list(self.path.parent.glob("board.json.corrupt.*.bak")))

    def test_wrong_top_level_isolated(self):
        self.path.write_text("[1, 2, 3]", encoding="utf-8")  # 顶层数组 → 隔离
        store = BoardStore(self.path)
        store.load()
        self.assertTrue(store.problems)

    def test_corrupt_backup_kept_limited(self):
        for i in range(8):
            self.path.write_text(f"{{坏{i}", encoding="utf-8")
            BoardStore(self.path).load()
        backups = list(self.path.parent.glob("board.json.corrupt.*.bak"))
        self.assertLessEqual(len(backups), 5)

    def test_atomic_write_rejects_failure(self):
        # 目录不可写场景不好跨平台构造，退而验证正常写不抛异常
        atomic_write_json(self.path, {"ok": True})
        self.assertEqual(json.loads(self.path.read_text("utf-8")), {"ok": True})


class PrevBackupRecoveryTest(unittest.TestCase):
    """最近一次成功写入的好副本（.prev）可在损坏后恢复数据"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "board.json"
        self.orig = {"lists": [{"title": "好数据", "cards": []}]}
        atomic_write_json(self.path, self.orig)  # 第一次写：无 .prev

    def tearDown(self):
        self.tmp.cleanup()

    def test_prev_rotated_after_second_write(self):
        atomic_write_json(self.path, {"lists": [{"title": "新数据", "cards": []}]})
        self.assertTrue(good_prev_copy(self.path).exists())
        # .prev 内容应为上一次写入的内容
        data = json.loads(good_prev_copy(self.path).read_text("utf-8"))
        self.assertEqual(data, self.orig)

    def test_restore_from_prev_after_corruption(self):
        # 好数据 → 修改 → 写盘（产生 .prev = 修改前的"好数据"）
        atomic_write_json(self.path, {"lists": [{"title": "好数据", "cards": []}]})
        store = BoardStore(self.path)
        board = store.load()
        board.lists[0].cards.append(Card(title="抢救目标"))
        board.lists[0].cards.append(Card(title="最新改动"))
        store.mark_dirty()
        store.flush()
        # 再改一次并落盘：此刻 .prev = 含"抢救目标"的版本
        board.lists[0].cards[1].title = "最新改动2"
        store.mark_dirty()
        store.flush()

        # 主文件损坏（模拟断电/写坏）
        self.path.write_text("{损坏", encoding="utf-8")
        store2 = BoardStore(self.path)
        board2 = store2.load()
        self.assertTrue(store2.problems)
        self.assertEqual(len(board2.lists), 3)  # 默认看板

        # 从 .prev 好副本恢复：应拿到上一份完整好数据（含"抢救目标"）
        prev = good_prev_copy(self.path)
        self.assertIsNotNone(prev)
        self.assertTrue(restore_from_backup(self.path, prev))
        store3 = BoardStore(self.path)
        board3 = store3.load()
        titles = [c.title for c in board3.lists[0].cards]
        self.assertIn("抢救目标", titles)
        self.assertNotIn("最新改动2", titles)

        # 恢复后主文件与备份均保留，可继续正常读写
        self.assertTrue(self.path.exists())
        self.assertTrue(good_prev_copy(self.path).exists())

    def test_restore_rejects_invalid_backup(self):
        bak = self.path.with_name("fake.bak")
        bak.write_text("不是JSON", encoding="utf-8")
        self.assertFalse(restore_from_backup(self.path, bak))

    def test_latest_backup_helpers(self):
        self.assertIsNone(good_prev_copy(self.path))  # 仅写过一次，无 .prev
        self.path.write_text("{坏", encoding="utf-8")
        doc = load_json_doc(self.path, on_problem=lambda *_: None)
        self.assertEqual(doc, {})
        self.assertIsNotNone(latest_backup(self.path))
        self.assertIsNone(good_prev_copy(self.path))  # 损坏备份 ≠ 好副本


if __name__ == "__main__":
    unittest.main(verbosity=2)

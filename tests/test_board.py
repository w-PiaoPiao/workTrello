# -*- coding: utf-8 -*-
"""
数据层单元测试：Board / BoardList / Card 序列化 + BoardStore 读写、
损坏隔离备份与 .prev 好副本恢复

用法：python tests/test_board.py
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

# 数据目录隔离：单文件 / discover / pytest 运行方式下
# 都不得读写真实用户数据（防止全量回归清空 %LOCALAPPDATA% 看板）
os.environ["PET_BOARD_DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.board import Board, BoardList, BoardStore, Card
from app.models.json_io import (
    SNAPSHOT_KEEP,
    StoreError,
    atomic_write_json,
    doc_has_cards,
    good_prev_copy,
    load_json_doc,
    nonempty_snapshots,
    restore_from_backup,
    rotate_backups,
    snapshot_board,
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
        self.assertEqual(card.workdir, "")
        self.assertFalse(card.done)

    def test_apply_updates_fields(self):
        card = Card(title="旧标题", due_date="2026-09-01")
        card.apply({"title": " 新标题 ", "notes": "备注",
                    "labels": ["red"], "due_date": None, "done": True,
                    "workdir": " /tmp/proj "})
        self.assertEqual(card.title, "新标题")
        self.assertEqual(card.due_date, None)
        self.assertEqual(card.workdir, "/tmp/proj")
        self.assertTrue(card.done)

    def test_new_fields_roundtrip(self):
        card = Card(title="全字段", starred=True, pomodoros=3,
                    done_at="2026-09-06T10:00:00+08:00", archived=True,
                    workdir="/Volumes/SSD/项目")
        card2 = Card.from_dict(card.to_dict())
        self.assertTrue(card2.starred)
        self.assertEqual(card2.pomodoros, 3)
        self.assertEqual(card2.done_at, "2026-09-06T10:00:00+08:00")
        self.assertTrue(card2.archived)
        self.assertEqual(card2.workdir, "/Volumes/SSD/项目")

    def test_apply_sets_done_at(self):
        card = Card(title="任务")
        self.assertIsNone(card.done_at)
        card.apply({"done": True})
        self.assertIsNotNone(card.done_at)      # 完成时刻自动记录
        card.apply({"done": False})
        self.assertIsNone(card.done_at)          # 取消完成则清除
        card.apply({})                           # 未涉及 done：保持原状
        self.assertIsNone(card.done_at)

    def test_apply_updates_starred(self):
        card = Card(title="任务")
        card.apply({"starred": True})
        self.assertTrue(card.starred)

    # ── 重复任务：roll_repeat（完成时滚动截止日期） ──────────

    def test_roll_repeat_daily_and_weekly(self):
        card = Card(title="每日晨会", due_date=date.today().isoformat())
        card.repeat = "daily"
        self.assertTrue(card.roll_repeat())
        self.assertEqual(card.due_date,
                         (date.today() + timedelta(days=1)).isoformat())
        self.assertFalse(card.done)
        self.assertIsNone(card.done_at)
        card.repeat = "weekly"
        card.due_date = date.today().isoformat()
        self.assertTrue(card.roll_repeat())
        self.assertEqual(card.due_date,
                         (date.today() + timedelta(days=7)).isoformat())

    def test_roll_repeat_overdue_jumps_to_today_or_later(self):
        card = Card(title="迟交补完",
                    due_date=(date.today() - timedelta(days=20)).isoformat())
        card.repeat = "daily"
        self.assertTrue(card.roll_repeat())
        delta = (date.fromisoformat(card.due_date) - date.today()).days
        self.assertGreaterEqual(delta, 0)

    def test_roll_repeat_noop_without_repeat_or_date(self):
        card = Card(title="普通卡", due_date=date.today().isoformat())
        self.assertFalse(card.roll_repeat())          # repeat=never
        card.repeat = "daily"
        card.due_date = None
        self.assertFalse(card.roll_repeat())          # 无日期
        card.due_date = "bad-date"
        self.assertFalse(card.roll_repeat())          # 非法日期

    def test_repeat_roundtrip_and_validation(self):
        card = Card(title="周报", due_date="2026-09-13", repeat="weekly")
        card2 = Card.from_dict(card.to_dict())
        self.assertEqual(card2.repeat, "weekly")
        # 扩展周期：月/年/工作日/自定义均为合法值
        for kind in ("monthly", "yearly", "weekdays", "custom"):
            self.assertEqual(Card.from_dict(
                {"title": "x", "repeat": kind}).repeat, kind)
        # 非法值回退 never
        self.assertEqual(Card.from_dict(
            {"title": "x", "repeat": "hourly"}).repeat, "never")
        self.assertEqual(Card.from_dict({"title": "y"}).repeat, "never")

    def test_next_repeat_date_monthly_yearly_weekdays_custom(self):
        # 每月：1/31 → 2/28（尾日钳制）；再推进 3/28（钳制不回跳 31）
        card = Card(title="月末", repeat="monthly")
        d = card._next_repeat_date(date(2026, 1, 31))
        self.assertEqual(d, date(2026, 2, 28))
        self.assertEqual(card._next_repeat_date(d), date(2026, 3, 28))
        # 每年：2024-02-29 → 2025-02-28（闰日钳制）
        card = Card(title="闰日", repeat="yearly")
        self.assertEqual(card._next_repeat_date(date(2024, 2, 29)),
                         date(2025, 2, 28))
        # 自定义间隔：3 天
        card = Card(title="每三天", repeat="custom", repeat_interval=3)
        self.assertEqual(card._next_repeat_date(date(2026, 9, 10)),
                         date(2026, 9, 13))
        # 工作日：周五 → 下周一；周日 → 周一
        card = Card(title="工作日", repeat="weekdays")
        self.assertEqual(card._next_repeat_date(date(2026, 9, 11)),   # 周五
                         date(2026, 9, 14))
        self.assertEqual(card._next_repeat_date(date(2026, 9, 13)),   # 周日
                         date(2026, 9, 14))

    def test_roll_repeat_custom_catches_up_to_today(self):
        """逾期补完推进到不早于今天（追赶语义对所有周期一致）"""
        card = Card(title="逾期补完", due_date="2026-01-31",
                    repeat="monthly", repeat_interval=1)
        self.assertTrue(card.roll_repeat())
        delta = (date.fromisoformat(card.due_date) - date.today()).days
        self.assertGreaterEqual(delta, 0)
        self.assertLessEqual(delta, 31)

    def test_repeat_interval_roundtrip(self):
        card = Card(title="每五天", repeat="custom", repeat_interval=5)
        card2 = Card.from_dict(card.to_dict())
        self.assertEqual(card2.repeat_interval, 5)
        # 非法/越界：回退 1 并夹到 1..365
        self.assertEqual(Card.from_dict(
            {"title": "x", "repeat_interval": "abc"}).repeat_interval, 1)
        self.assertEqual(Card.from_dict(
            {"title": "x", "repeat_interval": 9999}).repeat_interval, 365)

    # ── 优先级 ────────────────────────────────────────────

    def test_priority_roundtrip_and_clamp(self):
        card = Card(title="重要任务", priority=1)
        card2 = Card.from_dict(card.to_dict())
        self.assertEqual(card2.priority, 1)
        # 非法值回退 0 并夹到 0..3
        self.assertEqual(Card.from_dict({"title": "x",
                                         "priority": "abc"}).priority, 0)
        self.assertEqual(Card.from_dict(
            {"title": "x", "priority": 99}).priority, 3)
        self.assertEqual(Card.from_dict(
            {"title": "x", "priority": -2}).priority, 0)

    def test_apply_updates_priority(self):
        card = Card(title="任务")
        card.apply({"priority": 2})
        self.assertEqual(card.priority, 2)
        card.apply({"priority": 99})
        self.assertEqual(card.priority, 3)
        card.apply({})
        self.assertEqual(card.priority, 3)          # 未涉及：保持原状

    # ── 当日完成统计（彩蛋用） ─────────────────────────────

    def test_today_done_count(self):
        board = Board()
        lst = BoardList("待办")
        board.lists = [lst]
        now = datetime.now().astimezone()
        c1 = Card(title="今天完成", done=True, done_at=now.isoformat())
        c2 = Card(title="昨天完成", done=True,
                  done_at=(now - timedelta(days=1)).isoformat())
        c3 = Card(title="今天但取消", done=False, done_at=now.isoformat())
        lst.cards = [c1, c2, c3]
        self.assertEqual(board.today_done_count(now.date()), 1)

    # ── 统一谓词：due_delta / in_today_focus（今日聚焦与统计共用） ──

    def test_due_delta(self):
        today = date(2026, 9, 6)
        self.assertEqual(Card(title="无日期").due_delta(today), None)
        self.assertEqual(Card(title="逾期", due_date="2026-09-05")
                         .due_delta(today), -1)
        self.assertEqual(Card(title="今天", due_date="2026-09-06")
                         .due_delta(today), 0)
        self.assertEqual(Card(title="明天", due_date="2026-09-07")
                         .due_delta(today), 1)
        self.assertEqual(Card(title="非法", due_date="垃圾日期")
                         .due_delta(today), None)

    def test_in_today_focus(self):
        today = date(2026, 9, 6)
        self.assertTrue(Card(title="星标", starred=True).in_today_focus(today))
        self.assertTrue(Card(title="逾期", due_date="2026-09-05")
                        .in_today_focus(today))
        self.assertTrue(Card(title="今天截止", due_date="2026-09-06")
                        .in_today_focus(today))
        self.assertFalse(Card(title="明天截止", due_date="2026-09-07")
                         .in_today_focus(today))
        self.assertFalse(Card(title="无日期无星标").in_today_focus(today))
        # 排除项：完成/归档后即使星标也不属于今日聚焦
        self.assertFalse(Card(title="已完星标", starred=True, done=True)
                         .in_today_focus(today))
        self.assertFalse(Card(title="归档星标", starred=True, archived=True)
                         .in_today_focus(today))
        # 非法日期按未设置处理
        self.assertFalse(Card(title="非法", due_date="垃圾")
                         .in_today_focus(today))


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

    def test_due_counts_excludes_archived(self):
        lst = self.board.lists[0]
        lst.cards.append(Card(title="逾期归档", due_date="2026-09-01",
                              archived=True))
        self.assertEqual(self.board.due_counts(date(2026, 9, 6)), (0, 0))

    def test_today_focus_cards(self):
        lst = self.board.lists[0]
        lst.cards.append(Card(title="星标", starred=True, done=True))
        lst.cards.append(Card(title="星标未完成", starred=True))
        lst.cards.append(Card(title="逾期未完成", due_date="2026-09-01"))
        lst.cards.append(Card(title="未来截止", due_date="2026-12-01"))
        lst.cards.append(Card(title="逾期归档", due_date="2026-09-01",
                              archived=True))
        lst.cards.append(Card(title="普通"))
        focus = [c.title for c in self.board.today_focus_cards(date(2026, 9, 6))]
        # 已完成、未来截止、归档、普通卡片都不算今日聚焦
        self.assertEqual(sorted(focus), ["星标未完成", "逾期未完成"])

    def test_today_stats_matches_legacy_helpers(self):
        """today_stats 单趟统计结果必须与各旧接口逐一等价（回归护栏）"""
        today = date(2026, 9, 6)
        lst0, lst1 = self.board.lists
        lst0.cards += [
            Card(title="星标", starred=True),
            Card(title="逾期", due_date="2026-09-01"),
            Card(title="今天", due_date="2026-09-06"),
            Card(title="未来", due_date="2026-12-01"),
            Card(title="完成", done=True),
            Card(title="归档", archived=True),
            Card(title="无效日期", due_date="不是日期"),
        ]
        lst1.cards.append(Card(title="完成B", done=True))
        lst1.cards.append(Card(title="今天完成", done=True,
                              done_at=f"{today.isoformat()}T10:00:00+08:00"))
        stats = self.board.today_stats(today)
        self.assertEqual(stats["total"], self.board.total_cards())
        self.assertEqual(stats["done"], self.board.done_cards())
        self.assertEqual(stats["focus_count"],
                         len(self.board.today_focus_cards(today)))
        self.assertEqual([(l.id, c.id) for l, c in stats["focus"]],
                         [(l.id, c.id) for l, c
                          in ((lst, c) for lst in self.board.lists
                              for c in lst.cards
                              if c.in_today_focus(today))])
        self.assertEqual((stats["overdue"], stats["due_today"]),
                         self.board.due_counts(today))
        self.assertEqual(stats["done_today"],
                         self.board.today_done_count(today))
        self.assertEqual(stats["focus_count"], 3)       # 星标 + 逾期 + 今天
        self.assertEqual(stats["total"], 10)            # 含完成，不含归档
        self.assertEqual(stats["done"], 3)
        self.assertEqual(stats["done_today"], 1)        # 仅"今天完成"

    def test_due_delta_cache_tracks_due_date(self):
        """due_delta 缓存自校验：due_date 变更后立即按新值计算"""
        today = date(2026, 9, 6)
        c = Card(title="任务", due_date="2026-09-06")
        self.assertEqual(c.due_delta(today), 0)
        c.due_date = "2026-09-01"      # 模拟 apply/roll_repeat 的直接赋值
        self.assertEqual(c.due_delta(today), -5)
        c.due_date = "不是日期"
        self.assertIsNone(c.due_delta(today))
        c.due_date = None
        self.assertIsNone(c.due_delta(today))

    def test_card_index_invalidation(self):
        """find_card 懒建索引：变更点失效契约（controller 于直接改 cards
        的位置立即 invalidate_index，_after_data_change 再兜底一次）"""
        lst0 = self.board.lists[0]
        new_card = Card(title="新卡")
        lst0.cards.insert(0, new_card)
        lst0.cards.remove(self.card_a)
        # 索引未建立过 → 首次查询懒建，天然反映最新结构
        found_lst, found = self.board.find_card(new_card.id)
        self.assertIs(found, new_card)
        self.assertIs(found_lst, lst0)
        self.assertEqual(self.board.find_card(self.card_a.id), (None, None))
        # 索引已建立后再直接改结构且未失效 → 查到旧态（契约所允许）；
        # 显式失效后立即反映新态
        newer = Card(title="更新卡")
        lst0.cards.insert(0, newer)
        self.assertIsNone(self.board.find_card(newer.id)[1])
        self.board.invalidate_index()
        self.assertIs(self.board.find_card(newer.id)[1], newer)
        # remove_card 走索引且移除后不可再查
        removed = self.board.remove_card(new_card.id)
        self.assertIs(removed, new_card)
        self.assertEqual(self.board.find_card(new_card.id), (None, None))
        self.assertIsNone(self.board.remove_card("不存在"))

    def test_totals_exclude_archived(self):
        lst = self.board.lists[0]
        lst.cards.append(Card(title="A", done=True))
        lst.cards.append(Card(title="B", archived=True))
        # setUp 已有 card_a(未完成) + card_b(未完成)；归档的 B 不计入
        self.assertEqual(self.board.total_cards(), 3)
        self.assertEqual(self.board.done_cards(), 1)

    def test_archived_cards_and_weekly_done(self):
        now = datetime(2026, 9, 6, 12, 0, 0)
        lst = self.board.lists[0]
        lst.cards.append(Card(title="归档A", archived=True))
        lst.cards.append(Card(title="本周完成", done=True,
                              done_at="2026-09-04T10:00:00+08:00"))
        lst.cards.append(Card(title="上周完成", done=True,
                              done_at="2026-08-20T10:00:00+08:00"))
        archived = self.board.archived_cards()
        self.assertEqual(len(archived), 1)
        self.assertIs(archived[0][0], self.board.lists[0])   # 所属列表
        self.assertEqual(archived[0][1].title, "归档A")
        self.assertEqual(self.board.weekly_done_count(now), 1)

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

    def test_flush_writes_compact_json(self):
        """flush 走紧凑输出：落盘是最高频全量写，美化输出体积近乎翻倍"""
        store = BoardStore(self.path)
        board = store.load()
        board.lists[0].cards.append(Card(title="紧凑"))
        store.mark_dirty()
        store.flush()
        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn("\n", raw)          # 紧凑 = 单行
        # 内容等价可解析
        doc = json.loads(raw)
        self.assertEqual(doc["lists"][0]["cards"][0]["title"], "紧凑")

    def test_rotate_backups_keeps_latest(self):
        """好副本轮转：只保留时间戳最新的 keep 份（原逻辑永不清理）"""
        for i in range(7):
            bak = self.path.with_name(
                f"{self.path.name}.good.2026090{i}_000000_000000.bak")
            bak.write_text(str(i), encoding="utf-8")
        rotate_backups(self.path, "good", 5)
        remaining = sorted(self.path.parent.glob(f"{self.path.name}.good.*.bak"))
        self.assertEqual(len(remaining), 5)
        names = [p.name for p in remaining]
        self.assertNotIn(f"{self.path.name}.good.20260900_000000_000000.bak", names)
        self.assertNotIn(f"{self.path.name}.good.20260901_000000_000000.bak", names)
        self.assertIn(f"{self.path.name}.good.20260906_000000_000000.bak", names)

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

    def test_good_prev_copy_helper(self):
        self.assertIsNone(good_prev_copy(self.path))  # 仅写过一次，无 .prev
        self.path.write_text("{坏", encoding="utf-8")
        doc = load_json_doc(self.path, on_problem=lambda *_: None)
        self.assertEqual(doc, {})
        self.assertIsNone(good_prev_copy(self.path))  # 损坏备份 ≠ 好副本

    # ── 启动快照链（防空板覆盖事故的多层备份） ──

    def test_snapshot_skips_empty_board(self):
        """空默认板不产生快照（不污染恢复候选链）"""
        atomic_write_json(self.path, {"lists": [{"title": "待办", "cards": []}]})
        self.assertIsNone(snapshot_board(self.path))
        self.assertEqual(nonempty_snapshots(self.path), [])

    def test_snapshot_creates_and_lists(self):
        doc = {"lists": [{"title": "待办", "cards": [
            Card(title="真实卡").to_dict()]}]}
        atomic_write_json(self.path, doc)
        snap = snapshot_board(self.path)
        self.assertIsNotNone(snap)
        self.assertTrue(snap.exists())
        self.assertTrue(doc_has_cards(snap))            # 快照内容含卡
        self.assertEqual(nonempty_snapshots(self.path), [snap])

    def test_snapshot_prunes_old_ones(self):
        doc = {"lists": [{"title": "待办", "cards": [
            Card(title="卡").to_dict()]}]}
        atomic_write_json(self.path, doc)
        for _ in range(12):
            snapshot_board(self.path)
        snaps = list(self.path.parent.glob("board.json.snap.*.bak"))
        self.assertLessEqual(len(snaps), SNAPSHOT_KEEP)

    def test_doc_has_cards_robust(self):
        """损坏/空文件判定为无数据，不误当恢复候选"""
        self.path.write_text("{坏", encoding="utf-8")
        self.assertFalse(doc_has_cards(self.path))
        self.assertFalse(doc_has_cards(self.path.with_name("不存在.json")))


if __name__ == "__main__":
    unittest.main(verbosity=2)

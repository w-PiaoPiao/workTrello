# -*- coding: utf-8 -*-
"""
看板视图增量刷新测试：列/卡控件按 id 复用、内容指纹跳过重建、
增删移后的顺序对齐、空列提示与列头状态

用法（离屏环境）：
    QT_QPA_PLATFORM=offscreen python tests/test_board_view.py
"""

import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 数据目录隔离：单文件 / discover / pytest 运行方式下
# 都不得读写真实用户数据（防止全量回归清空 %LOCALAPPDATA% 看板）
os.environ["PET_BOARD_DATA_DIR"] = tempfile.mkdtemp()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QDrag
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

_qapp = QApplication.instance() or QApplication([])

from app.config import AppConfig
from app.models.board import BoardList, Card
from app.views import board_view
from app.views.board_view import WORKDIR_BADGE_TEXT, BoardView, CardWidget


def make_lists(spec):
    """[("列名", ["卡1", ...]), ...] → list[BoardList]"""
    lists = []
    for title, cards in spec:
        lst = BoardList(title=title)
        lst.cards = [Card(title=t) for t in cards]
        lists.append(lst)
    return lists


def frozen_date(day):
    """date 替身：today() 固定为 day，其余行为（fromisoformat / 减法）保持真实

    跨天用例要"拨钟"，但不能把 date 换成 Mock——视图内部靠 fromisoformat
    解析 due_date、靠日期减法算天数差。
    """
    return type("_FrozenDay", (date,),
                {"today": classmethod(lambda cls: day)})


class BoardViewRefreshTest(unittest.TestCase):
    def setUp(self):
        self.view = BoardView()
        self.lists = make_lists([("待办", ["A", "B"]), ("进行中", ["C"])])
        self.view.refresh(self.lists)

    def tearDown(self):
        self.view.deleteLater()

    def widget_titles(self, col):
        return [cw.card().title for cw in col._card_widgets]

    # ── 标签点击过滤（点卡片左缘色条） ─────────────────────

    def test_label_filter_via_stripe_click(self):
        """点色条按标签过滤：异色切换、同色清除、无标签列被滤空"""
        lst_a, lst_b = self.lists
        lst_a.cards[0].labels = ["red"]
        lst_a.cards[1].labels = ["red", "blue"]
        lst_b.cards[0].labels = []
        self.view.refresh(self.lists)
        col_a, col_b = self.view._columns
        # 点红色 → 只剩两张红标卡
        col_a.signal_label_clicked.emit("red")
        self.assertEqual(self.view._label_filter, "red")
        self.assertFalse(self.view._label_chip.isHidden())
        self.assertIn("红", self.view._label_chip.text())
        self.assertEqual(self.widget_titles(col_a), ["A", "B"])
        self.assertEqual(self.widget_titles(col_b), [])
        self.assertIn("标签 红", self.view._stats_label.text())
        # 异色切换 → 只剩蓝标卡
        col_a.signal_label_clicked.emit("blue")
        self.assertEqual(self.widget_titles(col_a), ["B"])
        # 再点同色清除 → 恢复全量
        col_a.signal_label_clicked.emit("blue")
        self.assertIsNone(self.view._label_filter)
        self.assertTrue(self.view._label_chip.isHidden())
        self.assertEqual(self.widget_titles(col_a), ["A", "B"])
        self.assertEqual(self.widget_titles(col_b), ["C"])

    def test_label_filter_cleared_via_chip(self):
        """过滤 chip 点击清除过滤态"""
        lst_a = self.lists[0]
        lst_a.cards[0].labels = ["teal"]
        self.view.refresh(self.lists)
        self.view._columns[0].signal_label_clicked.emit("teal")
        self.assertEqual(self.view._label_filter, "teal")
        self.view._label_chip.click()
        self.assertIsNone(self.view._label_filter)
        self.assertTrue(self.view._label_chip.isHidden())
        self.assertEqual(len(self.widget_titles(self.view._columns[0])), 2)

    def test_label_filter_survives_data_refresh(self):
        """数据变更（refresh）不丢标签过滤态，统计行持续反馈"""
        lst_a, lst_b = self.lists
        lst_a.cards[0].labels = ["orange"]
        self.view.refresh(self.lists)
        self.view._columns[0].signal_label_clicked.emit("orange")
        self.assertEqual(self.widget_titles(self.view._columns[0]), ["A"])
        # 数据变更路径：refresh（stats 单趟统计）
        self.view.refresh(self.lists)
        self.assertEqual(self.widget_titles(self.view._columns[0]), ["A"])
        self.assertIn("标签 橙色", self.view._stats_label.text())

    def test_label_key_at_matches_paint_geometry(self):
        """色条点击几何与 paintEvent 一致：x=1 起每条 4px，最多 4 条"""
        cw = CardWidget(Card(title="色条卡", labels=["red", "blue"]))
        self.assertEqual(cw._label_key_at(QPoint(2, 5)), "red")
        self.assertEqual(cw._label_key_at(QPoint(5, 5)), "blue")
        self.assertIsNone(cw._label_key_at(QPoint(0, 5)))   # 左边框
        self.assertIsNone(cw._label_key_at(QPoint(9, 5)))   # 越出两色条
        cw.deleteLater()
        cw2 = CardWidget(Card(title="无标签"))
        self.assertIsNone(cw2._label_key_at(QPoint(2, 5)))
        cw2.deleteLater()

    def _flush_search(self):
        """手动触发搜索防抖计时器（离屏无事件循环，直接驱动到点逻辑）"""
        self.view._search_timer.timeout.emit()

    def _settle(self, ms: int = 400) -> None:
        """跑事件循环直到折叠动画结束（过渡动画的最终态断言需要）

        动画是新增的纯视觉层：状态机在终点才由 _apply_collapsed_ui 一次刷齐，
        因此"最终态"断言必须等到动画结束再取，否则取到的是中间帧。
        """
        from PySide6.QtCore import QEventLoop, QTimer
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    # ── 复用性 ────────────────────────────────────────────

    def test_same_data_reuses_widgets(self):
        col_ids0 = [id(c) for c in self.view._columns]
        card_ids0 = [id(cw) for col in self.view._columns
                     for cw in col._card_widgets]
        self.view.refresh(self.lists)
        self.assertEqual(col_ids0, [id(c) for c in self.view._columns])
        card_ids1 = [id(cw) for col in self.view._columns
                     for cw in col._card_widgets]
        self.assertEqual(card_ids0, card_ids1)

    def test_add_card_keeps_existing_widgets(self):
        old_ids = {id(cw) for cw in self.view._columns[0]._card_widgets}
        self.lists[0].cards.insert(0, Card(title="新卡"))
        self.view.refresh(self.lists)
        col = self.view._columns[0]
        self.assertEqual(self.widget_titles(col), ["新卡", "A", "B"])
        self.assertTrue(old_ids.issubset(
            {id(cw) for cw in col._card_widgets}))
        self.assertEqual(col._header._count_label.text(), "3")

    def test_remove_card_deletes_widget(self):
        old_ids = {id(cw) for cw in self.view._columns[0]._card_widgets}
        self.lists[0].cards = [c for c in self.lists[0].cards
                               if c.title != "B"]
        self.view.refresh(self.lists)
        col = self.view._columns[0]
        self.assertEqual(self.widget_titles(col), ["A"])
        self.assertIn(next(id(cw) for cw in col._card_widgets), old_ids)

    def test_move_card_reorders_widgets(self):
        self.lists[0].cards.reverse()
        self.view.refresh(self.lists)
        self.assertEqual(self.widget_titles(self.view._columns[0]),
                         ["B", "A"])

    # ── 内容指纹 ──────────────────────────────────────────

    def test_toggle_done_updates_changed_card_only(self):
        col = self.view._columns[0]
        cw_a, cw_b = col._card_widgets
        self.lists[0].cards[0].done = True
        self.view.refresh(self.lists)
        self.assertIs(col._card_widgets[0], cw_a)      # 壳复用
        self.assertTrue(cw_a._card.done)
        # 完成态由 QSS 属性选择器 [done="true"] 驱动（配色统一由看板级样式表下发）
        self.assertEqual(cw_a._title_label.property("done"), True)
        self.assertIn('QLabel#cardTitle[done="true"]',
                      self.view.styleSheet())
        self.assertIs(col._card_widgets[1], cw_b)      # 未变卡不重建
        self.assertFalse(cw_b._card.done)
        self.assertFalse(cw_b._title_label.property("done"))

    def test_fingerprint_skips_rebuild(self):
        cw = self.view._columns[0]._card_widgets[0]
        # 同内容的新模型对象（模拟 reload）：不触发 rebuild
        rebuilds = []
        orig = cw.rebuild
        cw.rebuild = lambda: (rebuilds.append(1), orig())
        clone = Card(title="A", id=cw.card().id)
        cw.update_from_model(clone)
        self.assertEqual(rebuilds, [])
        self.assertIs(cw.card(), clone)
        # 内容变化：触发一次 rebuild
        clone.done = True
        cw.update_from_model(clone)
        self.assertEqual(rebuilds, [1])

    def test_rebuild_cleans_up_delete_button(self):
        """重复 rebuild 不残留绝对定位的旧删除按钮"""
        cw = self.view._columns[0]._card_widgets[0]
        first_btn = cw._delete_btn
        cw._card.done = not cw._card.done
        cw.update_from_model(cw._card)
        self.assertIsNot(cw._delete_btn, first_btn)
        self.assertNotIn(first_btn, cw.findChildren(QPushButton))

    # ── 指纹字段覆盖（防"改了不刷新"） ────────────────────

    def _badges(self, cw):
        return [b.text() for b, _ in cw._meta_badges]

    def test_fingerprint_covers_every_rendered_field(self):
        """指纹必须覆盖 rebuild 渲染的每个字段

        回归护栏：指纹曾漏掉 priority/repeat/pomodoros，导致这三项变化时
        卡片不重建、持续显示旧徽章，直到其他字段变化才连带刷新。
        """
        # rebuild 会渲染进徽章的模型字段 → 必须全部参与指纹
        cw = self.view._columns[0]._card_widgets[0]
        rendered = {"title", "done", "due_date", "labels",
                    "priority", "repeat", "pomodoros", "workdir"}
        fp = cw._content_fingerprint()
        before = fp
        # 逐个改动渲染字段，每个都必须让指纹变化
        for field in rendered:
            card = Card(title="A", id="fp-probe")
            original = getattr(card, field)
            if isinstance(original, bool):
                probe = not original
            elif isinstance(original, list):
                probe = ["blue"]
            elif isinstance(original, int):
                probe = original + 1
            elif original is None:
                probe = "2026-01-01"
            elif field == "due_date":
                probe = "2026-01-01"
            elif field == "repeat":
                probe = "daily"
            else:
                probe = str(original) + "x"
            setattr(card, field, probe)
            cw._card = card
            self.assertNotEqual(cw._content_fingerprint(), before,
                                f"字段 {field} 未参与指纹")
        # notes 只以 bool 参与（正文变化不重建，见 update_from_model）
        cw._card = Card(title="A", id="fp-probe", notes="")
        no_notes = cw._content_fingerprint()
        cw._card = Card(title="A", id="fp-probe", notes="有备注了")
        self.assertNotEqual(cw._content_fingerprint(), no_notes,
                            "notes 的有/无未参与指纹")

    # ── 资金分配监视徽标 ──────────────────────────────────

    @staticmethod
    def _fund(steps=None, start=None, role="lead", kind="finance") -> dict:
        return {"role": role, "kind": kind,
                "start_date": start or date.today().isoformat(),
                "steps": steps if steps is not None else []}

    def test_fund_badges_render(self):
        """资金卡徽标：角色类型 / 进度 / 下一道期限日期"""
        start = date.today() - timedelta(days=20)   # fund1 已超 6 天
        card = Card(title="资金卡", fund=self._fund(
            start=start.isoformat(),
            steps=[{"name": "收集分配方案", "done": True, "date": None},
                   {"name": "汇总上会材料", "done": False, "date": None}]))
        lists = make_lists([("资金", [])])
        lists[0].cards.append(card)
        self.view.refresh(lists)
        cw = self.view._columns[0]._card_widgets[0]
        badges = self._badges(cw)
        self.assertIn("牵头·财政", badges)
        self.assertIn("🧭 1/2", badges)
        # 有已过期期限 → 超期展示优先于下一道日期
        self.assertIn("2周期限超 6 天", badges)
        self.assertIn("danger", [tone for _b, tone in cw._meta_badges])

    def test_fund_overdue_badge_turns_danger(self):
        """两道期限都过期：徽标显示超期天数并转 danger 色"""
        start = date.today() - timedelta(days=40)   # fund2 已超 10 天
        card = Card(title="资金卡", fund=self._fund(
            role="assist", start=start.isoformat()))
        lists = make_lists([("资金", [])])
        lists[0].cards.append(card)
        self.view.refresh(lists)
        cw = self.view._columns[0]._card_widgets[0]
        badges = self._badges(cw)
        self.assertIn("30日期限超 10 天", badges)
        self.assertIn("danger", [tone for _b, tone in cw._meta_badges])

    def test_fund_deadline_badge_follows_day_change(self):
        """期限徽标是相对文案：跨天后必须重建（指纹含 fund + 今天）"""
        today = date.today()
        start = today - timedelta(days=14)          # fund1 = 今天到期
        card = Card(title="资金卡", fund=self._fund(
            role="assist", start=start.isoformat()))
        lists = make_lists([("资金", [])])
        lists[0].cards.append(card)
        self.view.refresh(lists)
        cw = self.view._columns[0]._card_widgets[0]
        self.assertIn("2周期限今天到期", self._badges(cw))
        with patch.object(board_view, "date",
                          frozen_date(today + timedelta(days=1))):
            self.view.refresh(lists)
        self.assertIn("2周期限超 1 天", self._badges(cw), "跨天后徽标未刷新")

    def test_fund_progress_change_refreshes_badge(self):
        """勾选环节后进度徽标立即刷新（指纹覆盖 steps）"""
        card = Card(title="资金卡", fund=self._fund(
            steps=[{"name": "收集分配方案", "done": False, "date": None},
                   {"name": "汇总上会材料", "done": False, "date": None}]))
        lists = make_lists([("资金", [])])
        lists[0].cards.append(card)
        self.view.refresh(lists)
        cw = self.view._columns[0]._card_widgets[0]
        self.assertIn("🧭 0/2", self._badges(cw))
        card.fund["steps"][0]["done"] = True
        self.view.refresh(lists)
        self.assertIn("🧭 1/2", self._badges(cw), "环节进度变化后徽标未刷新")

    def test_fund_tooltip_contains_flow_and_days(self):
        """tooltip 汇总流程摘要与环节耗时明细"""
        card = Card(title="资金卡", fund=self._fund(
            start="2026-09-01",
            steps=[{"name": "收集分配方案", "done": True,
                    "date": "2026-09-04"},
                   {"name": "汇总上会材料", "done": False, "date": None}]))
        lists = make_lists([("资金", [])])
        lists[0].cards.append(card)
        self.view.refresh(lists)
        cw = self.view._columns[0]._card_widgets[0]
        tip = cw.toolTip()
        self.assertIn("资金分配流程 1/2", tip)
        self.assertIn("当前：汇总上会材料", tip)
        self.assertIn("收集分配方案 3天", tip)

    def test_assist_fund_hides_progress_badge(self):
        """配合分配不监视环节：即使切换前残留 steps，也不显示进度徽标"""
        card = Card(title="配合卡", fund=self._fund(
            role="assist",
            steps=[{"name": "收集分配方案", "done": True, "date": None}]))
        lists = make_lists([("资金", [])])
        lists[0].cards.append(card)
        self.view.refresh(lists)
        cw = self.view._columns[0]._card_widgets[0]
        self.assertNotIn("🧭 1/1", self._badges(cw))
        self.assertIn("配合", self._badges(cw))

    def test_assist_fund_tooltip_mentions_deadline_only(self):
        """配合卡 tooltip：无环节进度，仅期限提示"""
        card = Card(title="配合卡", fund=self._fund(role="assist"))
        lists = make_lists([("资金", [])])
        lists[0].cards.append(card)
        self.view.refresh(lists)
        cw = self.view._columns[0]._card_widgets[0]
        self.assertIn("仅 2 次期限提醒", cw.toolTip())

    def test_due_badge_follows_day_change(self):
        """跨天且数据一字未改：截止徽标文案必须跟着重建

        回归：指纹只认 due_date 时，相对文案（今天/明天截止）在跨天后被
        指纹短路——看板开一夜仍显示昨天算出来的「明天截止」，直到进程
        重启。多待命一天同理变成逾期，配色也要一起翻转。
        """
        tomorrow = date.today() + timedelta(days=1)
        lists = make_lists([("待办", [])])
        lists[0].cards.append(
            Card(title="决算公开", due_date=tomorrow.isoformat()))
        self.view.refresh(lists)
        cw = self.view._columns[0]._card_widgets[0]
        self.assertIn("明天截止", self._badges(cw))

        # 时钟推进到截止当天：同一批模型对象、同一个卡片控件
        with patch.object(board_view, "date", frozen_date(tomorrow)):
            self.view.refresh(lists)
        self.assertIn("今天截止", self._badges(cw), "跨天后仍未换文案")
        self.assertIn("danger", [tone for _b, tone in cw._meta_badges])

        # 再推一天 → 逾期（文案与告警色同步）
        with patch.object(board_view, "date",
                          frozen_date(tomorrow + timedelta(days=1))):
            self.view.refresh(lists)
        self.assertTrue(any("已逾期" in t for t in self._badges(cw)))

    def test_no_due_date_card_keeps_fingerprint_across_days(self):
        """无截止日期卡片跨天不重建：日期只参与「有日期」卡片的指纹"""
        lists = make_lists([("待办", ["A"])])
        self.view.refresh(lists)
        cw = self.view._columns[0]._card_widgets[0]
        rebuilds = []
        orig = cw.rebuild
        cw.rebuild = lambda: (rebuilds.append(1), orig())
        with patch.object(board_view, "date",
                          frozen_date(date.today() + timedelta(days=1))):
            self.view.refresh(lists)
        self.assertEqual(rebuilds, [])

    def test_priority_change_refreshes_badge(self):
        """优先级变化立即刷新徽章（回归：指纹曾漏 priority）"""
        lists = make_lists([("待办", ["A"])])
        lists[0].cards[0].priority = 2
        self.view.refresh(lists)
        cw = self.view._columns[0]._card_widgets[0]
        self.assertEqual(self._badges(cw), ["P2"])
        lists[0].cards[0].priority = 1
        self.view.refresh(lists)
        self.assertEqual(self._badges(cw), ["P1"],
                         "优先级变化后徽章仍是旧值")

    def test_pomodoro_count_refreshes_badge(self):
        """番茄数变化立即刷新徽章（回归：指纹曾漏 pomodoros）"""
        lists = make_lists([("待办", ["A"])])
        self.view.refresh(lists)
        cw = self.view._columns[0]._card_widgets[0]
        self.assertEqual(self._badges(cw), [])
        lists[0].cards[0].pomodoros = 3
        self.view.refresh(lists)
        self.assertIn("🍅 ×3", self._badges(cw),
                      "番茄数变化后徽章未刷新")

    def test_repeat_change_refreshes_badge(self):
        """重复周期变化立即刷新徽章（回归：指纹曾漏 repeat）"""
        lists = make_lists([("待办", ["A"])])
        self.view.refresh(lists)
        cw = self.view._columns[0]._card_widgets[0]
        self.assertEqual(self._badges(cw), [])
        lists[0].cards[0].repeat = "daily"
        lists[0].cards[0].due_date = "2026-01-01"
        self.view.refresh(lists)
        self.assertTrue(any("每日" in t for t in self._badges(cw)),
                        "重复周期变化后徽章未刷新")

    def test_badge_tone_tracks_semantic_color(self):
        """徽章语义色经 tone 动态属性下发（P1 红 / P2 橙 / 逾期红）"""
        lists = make_lists([("待办", ["A"])])
        lists[0].cards[0].priority = 1
        lists[0].cards[0].due_date = "2020-01-01"   # 早已逾期
        self.view.refresh(lists)
        cw = self.view._columns[0]._card_widgets[0]
        tones = [tone for _b, tone in cw._meta_badges]
        self.assertEqual(tones[0], "danger")        # P1 → 红
        self.assertEqual(tones[1], "danger")        # 逾期 → 红

    # ── 模型对象重指向（备份恢复场景） ────────────────────

    def test_reload_repoints_new_model_objects(self):
        lists2 = [BoardList.from_dict(lst.to_dict()) for lst in self.lists]
        lists2[0].cards[0].done = True
        col_ids = [id(c) for c in self.view._columns]
        self.view.refresh(lists2)
        self.assertEqual(col_ids, [id(c) for c in self.view._columns])
        col = self.view._columns[0]
        self.assertIs(col._lst, lists2[0])
        self.assertIs(col._card_widgets[0].card(), lists2[0].cards[0])
        self.assertTrue(col._card_widgets[0]._card.done)

    # ── 列级增删与标题 ────────────────────────────────────

    def test_add_and_remove_list(self):
        new_lst = BoardList(title="新列")
        self.lists.append(new_lst)
        self.view.refresh(self.lists)
        self.assertEqual(len(self.view._columns), 3)
        self.assertEqual(self.view._columns[2].list_id(), new_lst.id)
        self.assertEqual(self.view._columns[2]._header._title_label.text(),
                         "新列")

        removed = self.lists.pop(0)
        self.view.refresh(self.lists)
        self.assertEqual(len(self.view._columns), 2)
        self.assertNotEqual(self.view._columns[0].list_id(), removed.id)

    def test_rename_list_updates_header(self):
        col = self.view._columns[0]
        self.lists[0].title = "改名了"
        self.view.refresh(self.lists)
        self.assertIs(self.view._columns[0], col)      # 列复用
        self.assertEqual(col._header._title_label.text(), "改名了")

    def test_empty_list_hint(self):
        self.lists[0].cards.clear()
        self.view.refresh(self.lists)
        col = self.view._columns[0]
        self.assertIsNotNone(col._hint)
        self.lists[0].cards.append(Card(title="回来了"))
        self.view.refresh(self.lists)
        self.assertIsNone(col._hint)
        self.assertEqual(self.widget_titles(col), ["回来了"])

    def test_all_lists_removed(self):
        self.view.refresh([])
        self.assertEqual(self.view._columns, [])
        self.assertEqual(self.view._lists, [])

    def test_stats(self):
        self.assertEqual(self.view._stats_label.text(),
                         "3 张卡片 · 完成 0")
        self.lists[0].cards[0].done = True
        self.view.refresh(self.lists)
        self.assertEqual(self.view._stats_label.text(),
                         "3 张卡片 · 完成 1")

    # ── 搜索过滤 ──────────────────────────────────────────

    def test_search_filters_columns_and_disables_drops(self):
        self.lists[0].cards.append(Card(title="ApplePie", notes="甜甜圈"))
        self.view.refresh(self.lists)
        self.view._search_edit.setText("apple")
        self._flush_search()
        col0, col1 = self.view._columns
        self.assertEqual([cw.card().title for cw in col0._card_widgets],
                         ["ApplePie"])
        self.assertFalse(col0.acceptDrops())      # 过滤态禁用拖放
        self.assertFalse(col1.acceptDrops())
        self.assertIsNotNone(col1._hint)
        self.assertIn("没有匹配", col1._hint.text())
        self.assertEqual(col0._header._count_label.text(), "1")

    def test_search_matches_notes_case_insensitive(self):
        self.lists[1].cards.append(Card(title="购物", notes="Buy Milk"))
        self.view.refresh(self.lists)
        self.view._search_edit.setText("MILK")
        self._flush_search()
        col1 = self.view._columns[1]
        self.assertEqual([cw.card().title for cw in col1._card_widgets],
                         ["购物"])

    def test_search_clear_restores_all(self):
        self.view._search_edit.setText("apple")
        self._flush_search()
        self.view._search_edit.clear()
        self._flush_search()
        col0, col1 = self.view._columns
        self.assertEqual([cw.card().title for cw in col0._card_widgets],
                         ["A", "B"])
        self.assertEqual([cw.card().title for cw in col1._card_widgets], ["C"])
        self.assertTrue(col0.acceptDrops())
        self.assertIsNone(col0._hint)

    def test_search_survives_data_refresh(self):
        self.view._search_edit.setText("a")
        self._flush_search()
        self.lists[0].cards.append(Card(title="Papaya"))
        self.view.refresh(self.lists)
        col0 = self.view._columns[0]
        self.assertEqual([cw.card().title for cw in col0._card_widgets],
                         ["A", "Papaya"])   # 新数据仍按当前关键词过滤

    def test_search_debounces_keystroke_bursts(self):
        """连续输入只重启计时器，停顿后仅触发一次过滤刷新"""
        self.view.refresh(make_lists([("待办", ["Apple", "Banana", "Avocado"])]))
        col0 = self.view._columns[0]
        calls = []
        orig = col0.refresh_cards
        col0.refresh_cards = lambda: (calls.append(1), orig())
        self.view._search_edit.setText("a")
        self.view._search_edit.setText("ap")
        self.view._search_edit.setText("app")
        self.assertEqual(calls, [])                   # 输入中未触发过滤
        self.assertTrue(self.view._search_timer.isActive())
        self.view._search_timer.timeout.emit()        # 停顿后只刷新一次
        self.assertEqual(calls, [1])
        self.assertEqual(self.widget_titles(col0), ["Apple"])

    # ── 今日聚焦 ──────────────────────────────────────────

    def test_today_filter(self):
        today = __import__("datetime").date.today()
        self.lists[0].cards.append(Card(title="星标卡", starred=True))
        self.lists[0].cards.append(Card(title="今天到期", due_date=today.isoformat()))
        self.lists[0].cards.append(Card(title="普通卡"))
        done_card = Card(title="已完成星标", starred=True, done=True)
        self.lists[0].cards.append(done_card)
        self.view.refresh(self.lists)
        self.view._today_btn.setChecked(True)
        col0, col1 = self.view._columns
        # 今日聚焦内排序：无优先级但星标的手工标注优先于"今天截止"
        self.assertEqual([cw.card().title for cw in col0._card_widgets],
                         ["星标卡", "今天到期"])
        self.assertFalse(col0.acceptDrops())          # 过滤态禁用拖放
        self.assertIn("今日 2", self.view._today_btn.text())
        # 优先级：高优先级置顶；同级星标提权（主动标注优先于被动"今天截止"）
        self.lists[0].cards.insert(0, Card(title="高优先卡", starred=True,
                                           priority=1))
        self.view.refresh(self.lists)
        self.assertEqual([cw.card().title for cw in col0._card_widgets],
                         ["高优先卡", "星标卡", "今天到期"])

    def test_today_composes_with_search(self):
        today = __import__("datetime").date.today()
        self.lists[0].cards.append(Card(title="星标甲", starred=True))
        self.lists[0].cards.append(Card(title="星标乙", starred=True))
        self.view.refresh(self.lists)
        self.view._today_btn.setChecked(True)
        self.view._search_edit.setText("甲")
        self._flush_search()
        col0 = self.view._columns[0]
        self.assertEqual([cw.card().title for cw in col0._card_widgets],
                         ["星标甲"])

    def test_collapsed_filtered_column_expand_keeps_drops_disabled(self):
        """过滤态(搜索/今日)折叠再展开：拖放禁用不被 set_collapsed 覆盖"""
        self.view._today_btn.setChecked(True)
        self.view._apply_filter()
        col0 = self.view._columns[0]
        self.assertFalse(col0.acceptDrops())          # 过滤态禁用拖放
        col0.toggle_collapsed()
        col0.toggle_collapsed()                       # 折叠又展开
        self.assertFalse(col0.acceptDrops())          # 仍禁用（A1 修复点）

    def test_header_leave_event_does_not_raise(self):
        """列头 leaveEvent 不得抛异常（QMenu 无 isOpen，误用会中断展开）"""
        from PySide6.QtCore import QEvent
        header = self.view._columns[0]._header
        header._menu_btn.set_active(True)
        header.leaveEvent(QEvent(QEvent.Leave))       # 修复前抛 AttributeError
        self.assertFalse(header._menu_btn._active)    # 菜单未开 → 熄灭

    def test_header_leave_keeps_menu_btn_while_menu_open(self):
        """菜单弹出期间列头收到 Leave 不能熄灭"⋯"（isVisible 判据的意图）"""
        from PySide6.QtCore import QEvent, QPoint
        header = self.view._columns[0]._header
        header._menu_btn.set_active(True)
        header._menu.popup(QPoint(0, 0))
        try:
            self.assertTrue(header._menu.isVisible())
            header.leaveEvent(QEvent(QEvent.Leave))
            self.assertTrue(header._menu_btn._active)
        finally:
            header._menu.close()

    def test_expand_with_cursor_on_header_restores_add_button(self):
        """光标停在折叠列头上时展开：添加按钮/箭头/滚动区全部随最终态恢复

        回归：展开时列头因位置变化同步收到 Leave，leaveEvent 里的异常会从
        setVisible 逸出、截断其后的状态同步 → 列满高却没有"添加卡片"按钮、
        箭头停在"▸"（用户截图症状）。此处按真实顺序派发鼠标事件复现。
        """
        from PySide6.QtCore import QPoint
        from PySide6.QtTest import QTest
        self.view.resize(1080, 640)
        self.view.show()
        QApplication.processEvents()

        col = self.view._columns[0]
        header = col._header
        col.set_collapsed(True, save=False)
        self._settle()                                # 等折叠动画走完，光标才会落在折叠后的列头上
        QApplication.processEvents()
        QTest.mouseMove(header._collapse_btn,
                        header._collapse_btn.rect().center())
        QApplication.processEvents()
        self.assertTrue(header.underMouse())          # 光标确实停在列头上

        col.toggle_collapsed()                        # 展开（Leave 在过程内派发）
        self._settle()                                # 等过渡动画走完再取最终态
        QApplication.processEvents()

        self.assertFalse(col.is_collapsed())
        self.assertFalse(col._scroll.isHidden())      # 卡片区回来
        self.assertFalse(col._add_btn.isHidden())     # 添加按钮回来（回归点）
        self.assertEqual(header._collapse_btn.text(), "▾")
        # 列改为顶部对齐 + 内容高度后，展开不再移动列头，光标可能仍在其上；
        # 点亮态必须与真实悬停一致——不能出现"没悬停却亮着"的残留
        self.assertEqual(header._menu_btn._active, header.underMouse())
        # 光标移开（列表区左上边距处）→ 残留点亮必须带走
        QTest.mouseMove(self.view._scroll.viewport(), QPoint(2, 2))
        QApplication.processEvents()
        self.assertFalse(header.underMouse())
        self.assertFalse(header._menu_btn._active)
        # 动画期的固定高度必须清除，否则列被钉死在过渡终点高度
        self.assertEqual(col.maximumHeight(), 16777215)
        self.assertIsNone(col._collapse_anim)

    def test_expand_survives_exception_in_child_event_handler(self):
        """子控件事件处理器抛异常也不得截断折叠状态同步（防回归护栏）"""
        col = self.view._columns[0]
        col.set_collapsed(True, save=False)
        # 模拟 showEvent/leaveEvent 之类的同步处理器抛错
        col._scroll.showEvent = lambda e: (_ for _ in ()).throw(
            RuntimeError("boom"))
        col.toggle_collapsed()                        # 不得向外抛
        self._settle()
        self.assertFalse(col.is_collapsed())
        self.assertFalse(col._add_btn.isHidden())     # 最终态仍刷齐
        self.assertEqual(col._header._collapse_btn.text(), "▾")

    # ── 过渡动画 ──────────────────────────────────────────

    def test_collapse_animates_then_settles(self):
        """折叠走过渡动画：起播后由动画驱动，终点刷齐最终态"""
        from app.views import motion
        col = self.view._columns[0]
        self.view.show()
        self._settle(50)
        col.set_collapsed(True)                       # save=True 走真实路径
        self.assertIsNotNone(col._collapse_anim)      # 动画已起播
        self.assertTrue(col.is_collapsed())           # 逻辑状态已切换
        self.assertIsNotNone(col._cards_host)         # 内容仍存活，随高度收窄
        self._settle()                                # 等动画走完
        self.assertIsNone(col._collapse_anim)
        self.assertTrue(col._scroll.isHidden())       # 终点才隐藏内容
        self.assertEqual(col.height(), col.COLLAPSED_HEIGHT)
        self.assertEqual(col.maximumHeight(), 16777215)
        self.assertEqual(col.minimumHeight(), 0)

    def test_animation_toggle_disables_transitions(self):
        """"暂停动画"总开关关闭 → 折叠瞬时生效（无动画对象）"""
        from app.views import motion
        original = motion.enabled()
        try:
            motion.set_enabled(False)
            col = self.view._columns[0]
            self.view.show()
            self._settle(50)
            col.set_collapsed(True, save=False)
            self.assertIsNone(col._collapse_anim)     # 无动画
            self.assertTrue(col.is_collapsed())
            self.assertTrue(col._scroll.isHidden())   # 状态立即到位
            col.set_collapsed(False, save=False)
            self.assertIsNone(col._collapse_anim)
            self.assertFalse(col._scroll.isHidden())
        finally:
            motion.set_enabled(original)

    def test_collapse_animation_interrupted_by_hide(self):
        """折叠动画进行中隐藏列：收尾为最终态，不停在中间高度"""
        col = self.view._columns[0]
        self.view.show()
        self._settle(50)
        col.set_collapsed(True, save=False)
        self.assertIsNotNone(col._collapse_anim)
        col.hide()                                    # 模拟列表被移除/隐藏
        self._settle(50)
        self.assertIsNone(col._collapse_anim)
        self.assertEqual(col.maximumHeight(), 16777215)

    def test_new_card_fades_in(self):
        """新增卡片淡入：透明度真的从低到高，且结束后摘掉 effect"""
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        from app.views import motion
        col = self.view._columns[0]
        self.view.show()
        self._settle(50)
        self.lists[0].cards.insert(0, Card(title="新卡"))
        self.view.refresh(self.lists)
        fresh = col._card_widgets[0]
        self.assertIsNotNone(getattr(fresh, "_motion_fade", None))
        # 起点必须是"接近全透明"——曾因起点被设成 1.0 导致 1.0→1.0 空动画
        eff = fresh.graphicsEffect()
        self.assertIsInstance(eff, QGraphicsOpacityEffect)
        self.assertLess(eff.opacity(), 0.9)
        self._settle(300)                             # 等淡入走完
        # 淡入结束必须摘掉临时 effect，否则控件持续走离屏渲染
        self.assertIsNone(fresh.graphicsEffect())
        self.assertIsNone(getattr(fresh, "_motion_fade", None))
        # 批量新增（搜索清空/导入）超过 ANIM_BATCH_LIMIT → 不播动画
        n = AppConfig.ANIM_BATCH_LIMIT + 2
        self.lists[0].cards = [Card(title=f"bulk{i}") for i in range(n)]
        self.view.refresh(self.lists)
        for cw in col._card_widgets:
            self.assertIsNone(getattr(cw, "_motion_fade", None))

    def test_fade_in_then_interrupt_settles_to_opaque(self):
        """淡入被新动画打断：从当前值续接，并最终收在完全不透明"""
        from app.views import motion
        from app.views.board_view import CardWidget
        w = CardWidget(Card(title="x"))
        w.resize(100, 40)
        try:
            motion.fade_in(w, 200)
            self._settle(60)
            self.assertLess(w.graphicsEffect().opacity(), 1.0)   # 淡入途中
            motion.fade_in(w, 200)                    # 打断：从当前值续接
            self._settle(300)
            self.assertIsNone(w.graphicsEffect())     # 结束已摘除
            self.assertIsNone(getattr(w, "_motion_fade", None))
        finally:
            w.deleteLater()

    def test_removed_card_fades_out_before_delete(self):
        """删除卡片先淡出再销毁（动画期间控件仍存活，否则动画播不出来）"""
        from app.views import motion
        col = self.view._columns[0]
        self.view.show()
        self._settle(50)
        gone = col._card_widgets[0]
        gone_id = gone.card().id
        self.lists[0].cards = self.lists[0].cards[1:]
        self.view.refresh(self.lists)
        self.assertNotIn(gone, col._card_widgets)      # 已脱离列
        self.assertIsNotNone(getattr(gone, "_motion_fade", None))
        self.assertEqual(gone.card().id, gone_id)      # 尚未销毁，仍可读模型
        motion.fade(gone, 0.0, 0, on_finished=gone.deleteLater)

    def test_delete_button_fades_in_on_hover(self):
        """悬停卡片：删除按钮先 show 再淡入"""
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QEnterEvent
        from app.views import motion
        cw = self.view._columns[0]._card_widgets[0]
        self.view.show()
        self._settle(50)
        cw.enterEvent(QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
        self.assertTrue(cw._delete_btn.isVisible())
        self.assertIsNotNone(getattr(cw._delete_btn, "_motion_fade", None))
        motion.fade(cw._delete_btn, 1.0, 0)           # 收尾，避免残留 effect

    def test_refresh_same_order_skips_layout_reinsert(self):
        """顺序未变的数据刷新不再整列 remove+insert（单卡变更的原位更新）"""
        col = self.view._columns[0]
        calls = []
        orig = col._cards_layout.removeWidget
        col._cards_layout.removeWidget = lambda w: (calls.append(1), orig(w))
        self.view.refresh(self.lists)          # 顺序未变 → 布局零操作
        self.assertEqual(calls, [])
        self.assertEqual(self.widget_titles(col), ["A", "B"])
        self.lists[0].cards.reverse()          # 移动卡片 → 仍需重排保序
        self.view.refresh(self.lists)
        self.assertGreater(len(calls), 0)
        self.assertEqual(self.widget_titles(col), ["B", "A"])

    def test_double_click_title_starts_rename(self):
        """标题双击经子类覆写进入重命名（替代实例 monkeypatch）"""
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        import app.views.board_view as bv
        header = self.view._columns[0]._header
        dbl = QMouseEvent(QEvent.Type.MouseButtonDblClick, QPointF(10, 5),
                          Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        try:
            header._title_label.mouseDoubleClickEvent(dbl)
            self.assertIsNotNone(bv._ACTIVE_RENAME)
        finally:
            bv.finish_active_rename(cancel=True)
        self.assertIsNone(bv._ACTIVE_RENAME)

    def test_rename_filter_installed_only_while_editing(self):
        """全局事件过滤器仅在重命名编辑器存在期间安装

        常态挂载会让全应用每个事件都过一遍 Python（实测 +23µs/事件），
        故按需装卸；本测试锁住该行为，防止改回常驻安装。
        """
        import app.views.board_view as bv
        self.assertFalse(self.view._rename_filter_installed)
        header = self.view._columns[0]._header
        header._start_rename()
        try:
            self.assertIsNotNone(bv._ACTIVE_RENAME)
            self.assertTrue(self.view._rename_filter_installed)
        finally:
            bv.finish_active_rename(cancel=True)
        self.assertIsNone(bv._ACTIVE_RENAME)
        self.assertFalse(self.view._rename_filter_installed)

    def test_refresh_same_order_skips_column_reinsert(self):
        """列顺序未变的刷新不再整列 remove+insert（与卡片同一策略）"""
        col = self.view._columns[0]
        calls = []
        orig = self.view._lists_layout.removeWidget
        self.view._lists_layout.removeWidget = (
            lambda w: (calls.append(1), orig(w)))
        try:
            self.view.refresh(self.lists)          # 顺序未变 → 布局零操作
            self.assertEqual(calls, [])
            self.assertEqual([c.list_id() for c in self.view._columns],
                             [l.id for l in self.lists])
            self.view.refresh([self.lists[1], self.lists[0]])   # 顺序变化 → 重排
            self.assertGreater(len(calls), 0)
            self.assertEqual([c.list_id() for c in self.view._columns],
                             [self.lists[1].id, self.lists[0].id])
        finally:
            self.view._lists_layout.removeWidget = orig
        self.assertIs(col, self.view._columns[1])   # 列控件仍复用

    def test_split_stylesheet_applied_once_on_board(self):
        """配色集中在 BoardView 一处下发（避免逐控件嵌套样式表重复 re-polish）"""
        self.assertIn("QFrame#listColumn", self.view.styleSheet())
        self.assertIn("QFrame#cardFrame", self.view.styleSheet())
        cw = self.view._columns[0]._card_widgets[0]
        self.assertEqual(cw.styleSheet(), "")       # 卡片不再自设配色
        self.assertEqual(cw._title_label.styleSheet(), "")

    # ── 整列拖拽 ──────────────────────────────────────────

    def _drop_list_on(self, col, moved_id, x):
        """构造 MIME_LIST 拖放事件投喂目标列"""
        from PySide6.QtCore import QMimeData, QPointF, Qt
        from PySide6.QtGui import QDropEvent
        from app.views.board_view import MIME_LIST
        mime = QMimeData()
        mime.setData(MIME_LIST, moved_id.encode("utf-8"))
        drop = QDropEvent(QPointF(x, 10), Qt.MoveAction, mime,
                          Qt.LeftButton, Qt.NoModifier)
        col.dropEvent(drop)

    def test_list_drop_before_and_after(self):
        """列拖放按落点 x 判 before/after 并经 BoardView 透传"""
        self.view.resize(1400, 700)
        got = []
        self.view.signal_list_move.connect(lambda *a: got.append(a))
        target = self.view._columns[1]
        w = max(target.width(), 40)
        moved = self.view._columns[0].list_id()
        self._drop_list_on(target, moved, 2)          # 左半 → before
        self._drop_list_on(target, moved, w - 2)      # 右半 → after
        self.assertEqual(got, [(moved, target.list_id(), True),
                               (moved, target.list_id(), False)])

    def test_refresh_keeps_column_order_synced(self):
        """lists 顺序变化后 _columns/布局/过滤配对跟随（防 zip 错位）"""
        reordered = [self.lists[1], self.lists[0]]   # 两列对调
        self.view.refresh(reordered)
        self.assertEqual([c.list_id() for c in self.view._columns],
                         [l.id for l in reordered])
        for col, lst in zip(self.view._columns, reordered):
            self.assertIs(col._lst, lst)          # 配对无错位
        # 过滤模式下 zip(_lists, _columns) 逐列应用可见集仍配对正确
        self.view._today_btn.setChecked(True)
        col0 = self.view._columns[0]
        self.assertFalse(col0.acceptDrops())       # 今日模式为过滤态
        self.view._today_btn.setChecked(False)

    def test_header_drag_threshold(self):
        """列表头按住：小位移不拖、超阈值拖一次、过滤态不拖"""
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        header = self.view._columns[0]._header
        calls = []
        header._start_list_drag = lambda: calls.append(1)

        press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(30, 10),
                            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        header.mousePressEvent(press)
        small = QMouseEvent(QEvent.Type.MouseMove, QPointF(35, 10),
                            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        header.mouseMoveEvent(small)              # 5px < 阈值
        self.assertEqual(calls, [])
        big = QMouseEvent(QEvent.Type.MouseMove, QPointF(60, 10),
                          Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        header.mouseMoveEvent(big)                # 30px > 阈值 → 启动一次
        self.assertEqual(calls, [1])
        header.mouseReleaseEvent(press)

        # 过滤态下列不可拖
        col = self.view._columns[0]
        col.set_visible_cards([])                 # 进入过滤态
        header.mousePressEvent(press)
        header.mouseMoveEvent(big)
        self.assertEqual(calls, [1])              # 未再触发
        col.set_visible_cards(None)

    def test_pomo_archive_signals_forward(self):
        received = {"pomo": None, "archive": None}
        self.view.signal_card_pomo.connect(
            lambda cid: received.__setitem__("pomo", cid))
        self.view.signal_card_archive.connect(
            lambda cid: received.__setitem__("archive", cid))
        cw = self.view._columns[0]._card_widgets[0]
        cw.signal_card_pomo.emit(cw.card().id)
        cw.signal_card_archive.emit(cw.card().id)
        self.assertEqual(received["pomo"], cw.card().id)
        self.assertEqual(received["archive"], cw.card().id)

    def test_workdir_signals_forward(self):
        received = {"open": None, "set": None}
        self.view.signal_card_workdir_open.connect(
            lambda cid: received.__setitem__("open", cid))
        self.view.signal_card_workdir_set.connect(
            lambda cid: received.__setitem__("set", cid))
        cw = self.view._columns[0]._card_widgets[0]
        cw.signal_card_workdir_open.emit(cw.card().id)
        cw.signal_card_workdir_set.emit(cw.card().id)
        self.assertEqual(received["open"], cw.card().id)
        self.assertEqual(received["set"], cw.card().id)

    def test_workdir_badge_click_emits_open(self):
        """点击 📂 徽章发出打开信号（事件过滤器 press 拦截路径）"""
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        lists = make_lists([("待办", ["A"])])
        lists[0].cards[0].workdir = "/tmp/some-project"
        self.view.refresh(lists)
        cw = self.view._columns[0]._card_widgets[0]
        badge = cw._workdir_badge
        self.assertIsNotNone(badge)   # 设置了目录才构建徽章
        self.assertEqual(badge.text(), WORKDIR_BADGE_TEXT)
        received = []
        cw.signal_card_workdir_open.connect(lambda cid: received.append(cid))
        event = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(2, 2),
                            QPointF(2, 2), Qt.MouseButton.LeftButton,
                            Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
        cw.eventFilter(badge, event)
        self.assertEqual(received, [cw.card().id])

    def test_workdir_badge_hidden_without_dir(self):
        """未设置目录不构建 📂 徽章（可选字段常态）"""
        lists = make_lists([("待办", ["A"])])
        self.assertFalse(lists[0].cards[0].workdir)
        self.view.refresh(lists)
        cw = self.view._columns[0]._card_widgets[0]
        self.assertIsNone(cw._workdir_badge)

    # ── 列高随内容收缩 ────────────────────────────────────

    def test_column_height_hugs_content(self):
        """列高 = 内容自然高度：短列不留空面板，长列顶到可用高度上限

        回归：此前列被拉伸满高，卡片少时列底是一大片空面板、"添加卡片"
        被顶到最底。改用 AlignTop + sizeHint 后列应贴合内容。
        """
        self.view.resize(1080, 640)
        self.view.show()
        self._settle(60)
        # 1 卡列 vs 2 卡列：内容多的列更高，且都不超过可用高度
        short_col, tall_col = self.view._columns[1], self.view._columns[0]
        avail = short_col._expanded_height()
        self.assertLess(short_col.height(), tall_col.height())
        self.assertLessEqual(tall_col.height(), avail)
        # 列高必须恰好装下内容（误差容许布局 1px 取整）
        for col in (short_col, tall_col):
            self.assertLessEqual(abs(col.height() - col._content_height()), 2)
        self.view.hide()

    def test_column_grows_when_card_added(self):
        """加卡后列高跟着长（refresh_cards 触发 updateGeometry）"""
        self.view.resize(1080, 640)
        self.view.show()
        self._settle(60)
        col = self.view._columns[1]
        before = col.height()
        self.lists[1].cards.append(Card(title="新增卡"))
        self.view.refresh(self.lists)
        self.view.repaint()
        self._settle(60)
        self.assertGreater(col.height(), before)
        self.view.hide()

    def test_column_fits_content_without_scrollbar(self):
        """恰好装下内容时不得冒出纵向滚动条（滚动条会挤窄卡片）"""
        self.view.resize(1080, 640)
        self.view.show()
        self._settle(60)
        for col in self.view._columns:
            vbar = col._scroll.verticalScrollBar()
            self.assertFalse(vbar.isVisible(),
                             f"列 {col.list_id()} 出现多余纵向滚动条")
        self.view.hide()

    def _content_height_columns(self):
        """建 4 列且某列初始就含两行卡（带备注）的看板

        复现条件：Qt 只在父布局项几何与 sizeHint 一致时才主动重排；列本身
        高度正确时，后续 sizeHint 变化不会触发重排。故测试必须从"列高正确"
        的稳定态出发，再改内容高度——这正是用户开着的看板所处的状态。
        """
        lists = [
            BoardList(title="待办", cards=[Card(title="甲"), Card(title="乙")]),
            BoardList(title="进行中", cards=[Card(title="丙")] * 3),
            BoardList(title="已完成",
                      cards=[Card(title="人力资源部档案", priority=2),
                             Card(title="成大附院的报告", notes="已发科教处")]),
            BoardList(title="问询",
                      cards=[Card(title="罗处：六医院结算新冠可能要扣减200万"),
                             Card(title="科研资金调整路径")]),
        ]
        self.view.refresh(lists)
        return lists, self.view._columns[2]

    def test_column_shrinks_when_card_content_shrinks(self):
        """卡片内容变矮（清空备注）后列高跟着缩，不残留空隙

        回归：Qt 不会因 sizeHint 变小而重排父布局中的列项，列高停在旧值。
        实测清空一张卡的备注后列高仍为 224（内容已降到 203），列尾留出
        21px 空隙；再补回备注又缩到 198 并冒出滚动条、卡片被下方
        「+ 添加卡片」压住——即用户截图里"已完成列没随卡片变化而变长"。
        """
        self.view.resize(1080, 640)
        self.view.show()
        self._settle(60)
        lists, col = self._content_height_columns()
        self._settle(120)
        card = col._card_widgets[1].card()
        tall = col.height()
        card.notes = ""
        self.view.refresh(lists)
        self._settle(120)
        self.assertLess(col.height(), tall, "列高未随内容收缩")
        self.assertLessEqual(abs(col.height() - col._content_height()), 2,
                             "列高与内容高度不符（父布局项几何未更新）")
        self.view.hide()

    def test_column_grows_when_card_content_grows(self):
        """卡片内容变高（补上备注）后列高跟着长，且不冒纵向滚动条"""
        self.view.resize(1080, 640)
        self.view.show()
        self._settle(60)
        lists, col = self._content_height_columns()
        self._settle(120)
        card = col._card_widgets[1].card()
        card.notes = ""
        self.view.refresh(lists)
        self._settle(120)
        short = col.height()
        card.notes = "09-11 15:33已发科教与服务业处汪莲"
        self.view.refresh(lists)
        self._settle(120)
        self.assertGreater(col.height(), short, "列高未随内容增长")
        self.assertLessEqual(abs(col.height() - col._content_height()), 2,
                             "列高与内容高度不符")
        self.assertFalse(col._scroll.verticalScrollBar().isVisible(),
                         "列尾内容被裁，冒出纵向滚动条")
        self.view.hide()

    # ── 徽章按实际宽度取舍 ────────────────────────────────

    def test_meta_badges_shrink_to_card_width(self):
        """徽章装不下时折叠为 "…"，且不把卡片撑得比列内可视宽还宽

        回归：Label 最小宽=文本宽，"P1+逾期+重复+备注+番茄"会把卡片撑到
        316px，而列内可视宽仅 258px（列横向滚动条关闭）→ 徽章被裁。
        """
        self.view.resize(1080, 640)
        self.view.show()
        card = Card(title="最坏组合", priority=1, due_date="2020-01-01",
                    repeat="weekly", notes="有备注", pomodoros=12)
        card.labels = ["blue", "green", "red", "purple"]
        self.lists[0].cards.append(card)
        self.view.refresh(self.lists)
        self.view.repaint()
        self._settle(60)

        cw = self.view._columns[0]._card_widgets[-1]
        # 卡片宽度不得超过列内可视宽
        self.assertLessEqual(cw.width(), self.view._columns[0]._scroll
                             .viewport().width())
        # 装不下的徽章被隐藏，并显示 "…" 指示
        visible = [b for b, _ in cw._meta_badges if b.isVisible()]
        self.assertLess(len(visible), len(cw._meta_badges))
        self.assertTrue(cw._more_badge.isVisible())
        # 可见徽章总宽不得超出卡片可用宽
        m = cw.layout().contentsMargins()
        need = (sum(b.sizeHint().width() for b in visible)
                + 6 * max(0, len(visible) - 1)
                + 6 + cw._more_badge.sizeHint().width())
        self.assertLessEqual(need, cw.width() - m.left() - m.right())
        self.view.hide()

    def test_meta_badges_fit_are_all_shown(self):
        """宽裕时徽章全显示、不出现 "…"（上限内且放得下）"""
        self.view.resize(1400, 700)
        self.view.show()
        self._settle(60)
        card = Card(title="两枚", priority=2, due_date="2030-01-01")
        self.lists[0].cards.append(card)
        self.view.refresh(self.lists)
        self.view.repaint()
        self._settle(60)
        cw = self.view._columns[0]._card_widgets[-1]
        self.assertTrue(all(b.isVisible() for b, _ in cw._meta_badges))
        self.assertFalse(cw._more_badge.isVisible())
        self.view.hide()

    def test_more_badge_absent_for_cards_without_meta(self):
        """无任何元信息的卡片不应创建 "…" 徽章"""
        cw = self.view._columns[0]._card_widgets[0]
        self.assertIsNone(cw._more_badge)

    def test_meta_badges_reevaluate_on_resize(self):
        """卡片变窄 → 徽章重新取舍（resizeEvent 重算）"""
        self.view.resize(1080, 640)
        self.view.show()
        self._settle(60)
        card = Card(title="窄卡", priority=1, due_date="2020-01-01",
                    repeat="weekly", notes="备注", pomodoros=5)
        self.lists[0].cards.append(card)
        self.view.refresh(self.lists)
        self.view.repaint()
        self._settle(60)
        cw = self.view._columns[0]._card_widgets[-1]
        self.assertTrue(cw._more_badge.isVisible())      # 窄列 → 折叠
        # 直接放大卡片：重排后应放下更多徽章
        cw.resize(600, cw.height())
        self.view.repaint()
        shown = len([b for b, _ in cw._meta_badges if b.isVisible()])
        self.assertGreater(shown, 0)
        self.view.hide()


class SelectionTest(unittest.TestCase):
    """多选交互：Ctrl/Cmd toggle + 锚点、Shift 列内范围与跨列退化、
    选区栏显隐与批量信号接线、Esc 链清空入口"""

    def setUp(self):
        self.view = BoardView()
        self.lists = make_lists([("待办", ["A", "B", "C"]),
                                 ("进行中", ["D"])])
        self.view.refresh(self.lists)
        self.view.resize(1080, 640)
        self.view.show()
        QApplication.processEvents()

    def tearDown(self):
        self.view.hide()
        self.view.deleteLater()

    def _ids(self, col_i=0):
        return [cw.card().id
                for cw in self.view._columns[col_i]._card_widgets]

    def _selected_widgets(self):
        return [cw for col in self.view._columns
                for cw in col._card_widgets if cw._selected]

    def test_ctrl_click_toggles_and_shows_bar(self):
        a = self._ids()[0]
        self.view._on_card_ctrl_clicked(a)
        self.assertEqual(self.view._selected_ids, {a})
        self.assertEqual(len(self._selected_widgets()), 1)   # 卡片高亮
        self.assertFalse(self.view._selection_bar.isHidden())
        self.assertIn("1", self.view._sel_count_label.text())

    def test_ctrl_click_same_card_deselects_and_hides_bar(self):
        a = self._ids()[0]
        self.view._on_card_ctrl_clicked(a)
        self.view._on_card_ctrl_clicked(a)
        self.assertEqual(self.view._selected_ids, set())
        # 操作栏改为淡出（CARD_EXIT_ANIM_MS）后 hide，断言前等动画收尾
        QTest.qWait(AppConfig.CARD_EXIT_ANIM_MS + 80)
        self.assertTrue(self.view._selection_bar.isHidden())

    def test_shift_click_selects_range_from_anchor(self):
        ids = self._ids()
        self.view._on_card_ctrl_clicked(ids[0])      # 锚点 = A
        self.view._on_card_shift_clicked(ids[2])     # Shift+C → A,B,C
        self.assertEqual(self.view._selected_ids, set(ids[:3]))
        self.assertEqual(len(self._selected_widgets()), 3)

    def test_shift_click_without_anchor_selects_single(self):
        ids = self._ids()
        self.view._on_card_shift_clicked(ids[1])     # 无锚点：退化为单选
        self.assertEqual(self.view._selected_ids, {ids[1]})

    def test_shift_click_across_columns_degrades_to_single(self):
        """跨列 Shift：范围只在同列内生效，否则只加该卡"""
        col0_ids, col1_ids = self._ids(0), self._ids(1)
        self.view._on_card_ctrl_clicked(col0_ids[0])
        self.view._on_card_shift_clicked(col1_ids[0])    # D 在另一列
        self.assertEqual(self.view._selected_ids,
                         {col0_ids[0], col1_ids[0]})

    def test_clear_selection_if_active_eats_esc(self):
        a = self._ids()[0]
        self.view._on_card_ctrl_clicked(a)
        self.assertTrue(self.view.clear_selection_if_active())   # 吞掉本次 Esc
        self.assertEqual(self.view._selected_ids, set())
        self.assertFalse(self.view.clear_selection_if_active())  # 无选中：放行
        # 显式按钮同样可清
        self.view._on_card_ctrl_clicked(a)
        self.view._sel_clear_btn.click()
        self.assertEqual(self.view._selected_ids, set())

    def test_batch_delete_button_emits_and_clears(self):
        ids = self._ids()
        got = []
        self.view.signal_batch_delete.connect(got.append)
        for cid in ids[:2]:
            self.view._on_card_ctrl_clicked(cid)
        self.view._sel_delete_btn.click()
        # selection_ids 出自集合：顺序不保证与点选序一致，按集合比较
        self.assertEqual(len(got), 1)
        self.assertEqual(set(got[0]), set(ids[:2]))
        self.assertEqual(self.view._selected_ids, set())     # 发出后清选区
        QTest.qWait(AppConfig.CARD_EXIT_ANIM_MS + 80)        # 等操作栏淡出
        self.assertTrue(self.view._selection_bar.isHidden())

    def test_batch_done_button_emits_toggle_done(self):
        ids = self._ids()
        got = []
        self.view.signal_batch_done.connect(
            lambda card_ids, done: got.append((card_ids, done)))
        self.view._on_card_ctrl_clicked(ids[0])
        self.view._sel_done_btn.click()
        self.assertEqual(got, [(ids[:1], True)])

    def test_batch_archive_button_emits(self):
        ids = self._ids()
        got = []
        self.view.signal_batch_archive.connect(got.append)
        self.view._on_card_ctrl_clicked(ids[0])
        self.view._sel_archive_btn.click()
        self.assertEqual([set(x) for x in got], [{ids[0]}])

    def test_selection_survives_data_refresh(self):
        """数据刷新（widget 按 id 复用）后选中高亮不丢"""
        a = self._ids()[0]
        self.view._on_card_ctrl_clicked(a)
        self.lists[0].cards.append(Card(title="新卡"))
        self.view.refresh(self.lists)
        self.assertEqual(self.view._selected_ids, {a})
        self.assertEqual(len(self._selected_widgets()), 1)


class CardHoverResetTest(unittest.TestCase):
    """删除按钮悬停态复位：漏投递的 Leave 必须在下一个动作自愈

    回归背景：✕ 的显隐只由 CardWidget._hovered 驱动，而 _hovered 仅在
    leaveEvent 复位。拖拽（原生模态循环）、隐藏/收起（只派发 Hide）、
    光标停在 ✕ 上的守卫分支都拿不到 Leave，rebuild() 又会把过期的
    _hovered 当权威重新 show() —— 一次漏投递就变成"✕ 永远留在卡上"，
    会话久了累积成多张卡同时残留。

    手法：Enter/Leave 用合成事件直接驱动（离屏下 QTest.mouseMove 的
    虚拟光标会跨用例残留、Enter/Leave 合成不稳定，不能作为断言依据），
    underMouse() 用 patch 显式给定——这样每条用例都只回答一个问题。
    """

    def setUp(self):
        self.view = BoardView()
        self.lists = make_lists([("待办", ["A", "B"]), ("进行中", ["C"])])
        self.view.refresh(self.lists)
        self.view.show()
        _qapp.processEvents()

    def tearDown(self):
        self.view.hide()
        self.view.deleteLater()

    def _card(self, col: int = 0, idx: int = 0) -> CardWidget:
        return self.view._columns[col]._card_widgets[idx]

    @staticmethod
    def _enter(cw) -> None:
        """合成 Enter（等价于光标移入卡片；Qt 会依次派发列→卡）"""
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QEnterEvent
        cw.enterEvent(QEnterEvent(QPointF(5, 5), QPointF(5, 5),
                                  QPointF(5, 5)))

    @staticmethod
    def _leave(cw) -> None:
        from PySide6.QtCore import QEvent
        cw.leaveEvent(QEvent(QEvent.Leave))

    def _stale(self, cw) -> None:
        """制造"漏投递 Leave"现场：先真实悬停（✕ 显示 + 看板记录跟踪），
        随后光标移走但不派发 Leave —— 这正是线上出问题的那个状态"""
        self._enter(cw)

    # ── 基本复位 ─────────────────────────────────────────

    def test_leave_hides_button(self):
        cw = self._card()
        self._enter(cw)
        self.assertTrue(cw._delete_btn.isVisible())
        self._leave(cw)
        self.assertFalse(cw._delete_btn.isVisible())
        self.assertFalse(cw._hovered)

    def test_sequence_hover_hide_show_rehover(self):
        """串行场景：悬停 → 隐藏（收起/最小化）→ 再显示 → 重新悬停"""
        cw = self._card()
        self._enter(cw)
        self.assertTrue(cw._delete_btn.isVisible())
        self.view.hide()                       # 收起为桌宠 / 隐藏到托盘
        _qapp.processEvents()
        self.assertFalse(cw._hovered)
        self.view.show()                       # 重新展开
        _qapp.processEvents()
        self.assertFalse(cw._delete_btn.isVisible(),
                         "重新展开后 ✕ 不该带着旧悬停态回来")
        self._enter(cw)                        # 再次悬停：正常出现
        self.assertTrue(cw._delete_btn.isVisible())

    def test_cursor_on_delete_btn_keeps_button(self):
        """光标停在 ✕ 上：父卡 Leave 不该收起它（否则按钮一点就没了）"""
        cw = self._card()
        self._enter(cw)
        cw._delete_btn.underMouse = lambda: True
        self._leave(cw)
        self.assertFalse(cw._hovered)
        self.assertTrue(cw._delete_btn.isVisible())

    # ── 悬停中重建：不闪，也不复活过期态 ──────────────────

    def test_rebuild_keeps_hover_when_cursor_on_card(self):
        cw = self._card()
        self._enter(cw)
        with patch.object(CardWidget, "underMouse", return_value=True):
            cw.card().title = "A（改过标题）"
            cw.update_from_model(cw.card())    # 指纹变化 → rebuild
            self.assertTrue(cw._delete_btn.isVisible(),
                            "光标还在卡上，重建后按钮不该消失")

    def test_rebuild_does_not_revive_stale_hover(self):
        """过期 _hovered（漏投递）再撞上一次重建 = 修复前会永久显示"""
        cw = self._card()
        self._stale(cw)
        with patch.object(CardWidget, "underMouse", return_value=False):
            cw.card().title = "A（改过标题）"
            cw.update_from_model(cw.card())
            self.assertFalse(cw._delete_btn.isVisible())
            self.assertFalse(cw._hovered)

    def test_reapply_style_clears_stale_hover(self):
        """主题切换路径同样不留残影（此前条件式 hide 会跳过）"""
        cw = self._card()
        self._stale(cw)
        with patch.object(CardWidget, "underMouse", return_value=False):
            cw.reapply_style()
            self.assertFalse(cw._delete_btn.isVisible())

    # ── 拖拽：起拖复位 + 预览不带 ✕ ───────────────────────

    def test_drag_start_resets_hover_before_grab(self):
        """起拖前复位悬停态：同列重排复用控件时不留 ✕，预览图也不带 ✕"""
        from PySide6.QtGui import QDrag, QPixmap
        cw = self._card()
        self._enter(cw)
        self.assertTrue(cw._delete_btn.isVisible())
        seen = {}

        def fake_grab():
            seen["btn_visible"] = cw._delete_btn.isVisible()
            return QPixmap(10, 10)

        cw.grab = fake_grab
        with patch.object(QDrag, "exec", return_value=None):
            cw._start_drag()
        self.assertFalse(seen.get("btn_visible"), "grab() 时 ✕ 应已复位")
        self.assertFalse(cw._delete_btn.isVisible())
        self.assertFalse(cw._hovered)

    # ── ✕ 自身 Leave 兜底 ────────────────────────────────

    def test_delete_btn_leave_revalidates(self):
        """✕ 的 Leave 触发复核：光标离开 ✕ 且不在卡上就收起"""
        from PySide6.QtCore import QEvent
        cw = self._card()
        self._enter(cw)
        # 光标已离开 ✕，且父卡也不在鼠标下 → 收起
        cw._delete_btn.underMouse = lambda: False
        with patch.object(CardWidget, "underMouse", return_value=False):
            cw.eventFilter(cw._delete_btn, QEvent(QEvent.Leave))
            self.assertFalse(cw._delete_btn.isVisible())
        # 光标仍在 ✕ 上 → 保持显示
        cw._delete_btn.show()
        cw._delete_btn.underMouse = lambda: True
        with patch.object(CardWidget, "underMouse", return_value=False):
            cw.eventFilter(cw._delete_btn, QEvent(QEvent.Leave))
            self.assertTrue(cw._delete_btn.isVisible())

    # ── 事件驱动自愈清扫 ─────────────────────────────────

    def test_enter_clears_previous_stale_hover_same_column(self):
        """同列从 A 划到 B：A 的残留悬停态被顺手清掉"""
        a, b = self._card(0, 0), self._card(0, 1)
        self._stale(a)
        self._enter(b)
        self.assertFalse(a._hovered)
        self.assertFalse(a._delete_btn.isVisible())
        self.assertTrue(b._delete_btn.isVisible())

    def test_enter_clears_previous_stale_hover_across_columns(self):
        """跨列同理：单光标下全局最多一张卡悬停，任一次悬停都自愈"""
        a, c = self._card(0, 0), self._card(1, 0)
        self._stale(a)
        self._enter(c)
        self.assertFalse(a._hovered)
        self.assertFalse(a._delete_btn.isVisible())

    def test_leave_clears_tracked_stale_hover(self):
        """光标离开整列：本列所有卡的残留悬停态一并复位"""
        from PySide6.QtCore import QEvent
        col = self.view._columns[0]
        for cw in col._card_widgets:
            self._stale(cw)
        col.leaveEvent(QEvent(QEvent.Leave))
        for cw in col._card_widgets:
            self.assertFalse(cw._hovered)
            self.assertFalse(cw._delete_btn.isVisible())

    def test_view_leave_clears_tracked_card(self):
        """光标离开整块看板：跟踪卡直接复位（列级 Leave 也没派发时的兜底）"""
        from PySide6.QtCore import QEvent
        cw = self._card()
        self._enter(cw)
        self.assertIs(self.view._hovered_card, cw)
        self.view.leaveEvent(QEvent(QEvent.Leave))
        self.assertFalse(cw._hovered)
        self.assertFalse(cw._delete_btn.isVisible())
        self.assertIsNone(self.view._hovered_card)


@unittest.skipUnless(sys.platform == "win32", "窗口控制键仅 Windows 创建")
class WindowCloseWiringTest(unittest.TestCase):
    """标题栏三键的接线：✕ 接「最小化到托盘」，不再接退出

    回归背景：✕ 原先直连 signal_quit_requested——常驻托盘的小挂件被误点一下
    就整个退出、要重新启动才回得来。折叠键（─）行为不变。
    """

    def setUp(self):
        self.view = BoardView()

    def tearDown(self):
        self.view.deleteLater()

    def test_close_emits_close_requested(self):
        hits = []
        self.view.signal_close_requested.connect(lambda: hits.append(True))
        self.view._window_controls.signal_close.emit()
        self.assertEqual(hits, [True])

    def test_board_view_no_longer_exposes_quit_signal(self):
        """看板视图不再有退出信号（退出只走托盘 / 桌宠右键 / 菜单栏）"""
        self.assertFalse(hasattr(self.view, "signal_quit_requested"))

    def test_minimize_still_collapses(self):
        hits = []
        self.view.signal_collapse_clicked.connect(lambda: hits.append(True))
        self.view._window_controls.signal_minimize.emit()
        self.assertEqual(hits, [True])


class DragHierarchyTest(unittest.TestCase):
    """拖拽层级：卡片上拖卡片、列表上拖整列、看板空白处才拖窗口

    回归背景：卡片与列头按下时都调 super().mousePressEvent()（= ignore），
    事件于是冒泡到主窗口，被"空白处拖动整个窗口"接手并成为鼠标抓取者——
    实测从卡片 / 列头 / 列内空白起拖都会把窗口搬走（各 120px），整列拖拽
    因此完全失效。这里用 QTest 派发真实鼠标事件（经窗口系统层、含 Qt 的
    隐式抓取），直接调 _start_drag() 测不到这条——它绕过了"谁接受按下"。
    """

    def setUp(self):
        from app.views import motion
        from app.views.main_window import MainWindow
        self._motion_was = motion.enabled()
        motion.set_enabled(False)      # 展开瞬时完成：测试不必等 240ms 动画
        self.win = MainWindow()
        self.win.resize(900, 620)
        self.view = BoardView()
        self.win.set_views(QWidget(), self.view)
        lst = BoardList(title="列")
        lst.cards.append(Card(title="卡片"))
        self.view.refresh([lst])
        self.win.show()
        self.win.expand()              # 看板须真的可见，否则按下的不是这些控件
        _qapp.processEvents()
        self.col = self.view._columns[0]
        self.card = self.col._card_widgets[0]

    def tearDown(self):
        from app.views import motion
        self.win.hide()
        self.win.deleteLater()
        motion.set_enabled(self._motion_was)

    def _drag(self, target, pos, offset=QPoint(60, 0)):
        """按下 → 拖过阈值 → 释放；返回 (窗口位移, QDrag 启动次数)

        位移在**释放前**取：释放会走 _snap_to_screen_edge() 把窗口吸附回
        屏幕边缘，而离屏虚拟屏装得下整窗，于是"拖完又回到原位"。
        """
        before = self.win.pos()
        with patch.object(QDrag, "exec", return_value=None) as dex:
            QTest.mousePress(target, Qt.LeftButton, pos=pos)
            _qapp.processEvents()
            QTest.mouseMove(target, pos + offset)
            _qapp.processEvents()
            QTest.mouseMove(target, pos + offset * 2)
            _qapp.processEvents()
            moved = self.win.pos() - before
            QTest.mouseRelease(target, Qt.LeftButton, pos=pos + offset * 2)
            _qapp.processEvents()
        self.win.move(before)          # 复位，避免影响后续断言
        _qapp.processEvents()
        return moved, dex.call_count

    def test_card_drag_moves_card_not_window(self):
        moved, drags = self._drag(self.card, self.card.rect().center())
        self.assertEqual((moved.x(), moved.y()), (0, 0), "拖卡片不该搬走窗口")
        self.assertEqual(drags, 1, "应当启动一次卡片拖拽")

    def test_list_header_drag_moves_list(self):
        moved, drags = self._drag(self.col._header,
                                  self.col._header.rect().center())
        self.assertEqual((moved.x(), moved.y()), (0, 0), "拖列头不该搬走窗口")
        self.assertEqual(drags, 1, "列头应启动整列拖拽")

    def test_list_blank_drag_moves_list(self):
        """列内空白（卡片下方的空处）也要能拖动整列"""
        blank = self.card.mapTo(
            self.col, self.card.rect().bottomLeft()) + QPoint(20, 40)
        self.assertTrue(self.col.rect().contains(blank),
                        "取点须落在列内，否则测的不是列身空白")
        moved, drags = self._drag(self.col, blank)
        self.assertEqual((moved.x(), moved.y()), (0, 0), "拖列内空白不该搬走窗口")
        self.assertEqual(drags, 1, "列内空白应启动整列拖拽")

    def test_board_blank_drag_moves_window(self):
        """看板空白（列之外的区域）才拖窗口——这一档要保住"""
        col_geo = self.col.geometry()
        blank = QPoint(col_geo.right() + 80, col_geo.center().y())
        self.assertTrue(self.view.rect().contains(blank),
                        f"取点 {blank} 须落列外空白处，看板尺寸 {self.view.size()}")
        moved, drags = self._drag(self.view, blank)
        self.assertNotEqual((moved.x(), moved.y()), (0, 0), "空白处应能拖窗口")
        self.assertEqual(drags, 0, "空白处不该启动任何 QDrag")


if __name__ == "__main__":
    unittest.main(verbosity=2)

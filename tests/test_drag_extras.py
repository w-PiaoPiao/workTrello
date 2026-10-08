# -*- coding: utf-8 -*-
"""
拖拽边界场景测试：多选成组拖拽、拖到折叠列自动展开、拖到「归档」/「今日」
按钮执行动作

用法（离屏环境）：
    QT_QPA_PLATFORM=offscreen python tests/test_drag_extras.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PET_BOARD_DATA_DIR"] = tempfile.mkdtemp()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication

_qapp = QApplication.instance() or QApplication([])

from app.models.board import BoardList, Card
from app.views.board_view import (
    MIME_CARD,
    MIME_CARDS,
    BoardView,
    _CardDropButton,
)

_LAST_MIME = None   # 事件不持有 mime 所有权，须持模块级引用防 GC 悬空


def _drag_event(mime: QMimeData, kind: str = "drop"):
    global _LAST_MIME
    _LAST_MIME = mime
    if kind == "dragEnter":
        return QDragEnterEvent(QPoint(10, 10), Qt.DropAction.MoveAction,
                               mime, Qt.LeftButton, Qt.NoModifier)
    return QDropEvent(QPointF(10, 10), Qt.MoveAction, mime,
                      Qt.LeftButton, Qt.NoModifier)


def _card_mime(*card_ids: str) -> QMimeData:
    """构造卡片拖拽数据：多张卡走 MIME_CARDS，单张走 MIME_CARD"""
    mime = QMimeData()
    mime.setData(MIME_CARD, card_ids[0].encode("utf-8"))
    if len(card_ids) > 1:
        mime.setData(MIME_CARDS, json.dumps(list(card_ids)).encode("utf-8"))
    return mime


def make_lists(spec):
    lists = []
    for title, cards in spec:
        lst = BoardList(title=title)
        lst.cards = [Card(title=t) for t in cards]
        lists.append(lst)
    return lists


class GroupDragPayloadTest(unittest.TestCase):
    """起拖侧：选中集合里的卡片带出整组 id"""

    def setUp(self):
        self.view = BoardView()
        self.lists = make_lists([("待办", ["A", "B", "C"]),
                                 ("进行中", ["D"])])
        self.view.refresh(self.lists)
        self.col = self.view._columns[0]

    def tearDown(self):
        self.view.deleteLater()

    def _card_widget(self, title):
        return next(cw for cw in self.col._card_widgets
                    if cw.card().title == title)

    def test_single_card_has_no_group_payload(self):
        """没多选时不发成组数据（单卡拖拽语义不变）"""
        self.assertEqual(self._card_widget("A")._group_drag_ids(), [])

    def test_selected_group_is_ordered_by_board(self):
        """组内顺序按看板顺序，不按点击顺序"""
        ids = [c.id for c in self.lists[0].cards]
        self.view._selected_ids = {ids[2], ids[0]}   # 先点 C 再点 A
        self.assertEqual(self._card_widget("A")._group_drag_ids(),
                         [ids[0], ids[2]])

    def test_dragging_unselected_card_ignores_selection(self):
        ids = [c.id for c in self.lists[0].cards]
        self.view._selected_ids = {ids[1], ids[2]}
        self.assertEqual(self._card_widget("A")._group_drag_ids(), [])

    def test_group_payload_contains_only_own_column(self):
        """跨列选择时只带走同列成员（跨列成组没有单一落点语义）"""
        ids_a = [c.id for c in self.lists[0].cards]
        ids_b = [c.id for c in self.lists[1].cards]
        self.view._selected_ids = {ids_a[0], ids_b[0]}
        self.assertEqual(self._card_widget("A")._group_drag_ids(), [ids_a[0]])


class GroupDropTest(unittest.TestCase):
    """落点侧：整组落到目标列"""

    def setUp(self):
        self.view = BoardView()
        self.lists = make_lists([("待办", ["A", "B", "C"]),
                                 ("进行中", ["D"])])
        self.view.refresh(self.lists)
        self.col_b = self.view._columns[1]

    def tearDown(self):
        self.view.deleteLater()

    def test_drop_group_emits_cards_move(self):
        ids = [c.id for c in self.lists[0].cards]
        got = []
        self.view.signal_cards_move.connect(
            lambda c, t, i: got.append((c, t, i)))
        self.col_b.dropEvent(_drag_event(_card_mime(ids[0], ids[2])))
        self.assertEqual(got, [(ids[0:1] + ids[2:3], self.lists[1].id, 0)])

    def test_bad_payload_is_ignored(self):
        mime = QMimeData()
        mime.setData(MIME_CARD, b"x")
        mime.setData(MIME_CARDS, b"{not json")
        got = []
        self.view.signal_cards_move.connect(
            lambda c, t, i: got.append(c))
        self.col_b.dropEvent(_drag_event(mime))
        self.assertEqual(got, [])


class CollapsedColumnDropTest(unittest.TestCase):
    """折叠列：接受拖入并自动展开"""

    def setUp(self):
        self.view = BoardView()
        self.lists = make_lists([("待办", ["A"]), ("进行中", ["B"])])
        self.view.refresh(self.lists)
        self.col_b = self.view._columns[1]
        self.col_b.set_collapsed(True, save=False, animate=False)

    def tearDown(self):
        self.view.deleteLater()

    def test_collapsed_column_accepts_drops(self):
        self.assertTrue(self.col_b.acceptDrops())

    def test_drag_enter_expands_column(self):
        self.assertTrue(self.col_b.is_collapsed())
        mime = _card_mime(self.lists[0].cards[0].id)
        self.col_b.dragEnterEvent(_drag_event(mime, "dragEnter"))
        self.assertFalse(self.col_b.is_collapsed())   # 已自动展开接收

    def test_file_drag_also_expands(self):
        mime = QMimeData()
        mime.setUrls([])   # 无本地文件：不构成文件拖拽
        self.col_b.dragEnterEvent(_drag_event(mime, "dragEnter"))
        self.assertTrue(self.col_b.is_collapsed())

    def test_filtered_column_still_rejects_drops(self):
        """过滤态（搜索/今日）落点不可靠，保持禁用——与折叠无关"""
        self.view.refresh(self.lists)
        self.col_b.set_visible_cards([])
        self.assertFalse(self.col_b.acceptDrops())


class DropTargetButtonTest(unittest.TestCase):
    """工具栏「归档」/「今日」按钮接收卡片拖放"""

    def setUp(self):
        self.view = BoardView()
        self.lists = make_lists([("待办", ["A", "B"])])
        self.view.refresh(self.lists)
        self.ids = [c.id for c in self.lists[0].cards]

    def tearDown(self):
        self.view.deleteLater()

    def test_single_card_drop_emits(self):
        got = []
        self.view.signal_batch_archive.connect(got.append)
        self.view._archive_btn.dropEvent(
            _drag_event(_card_mime(self.ids[0])))
        self.assertEqual(got, [[self.ids[0]]])

    def test_group_drop_emits_all_ids(self):
        got = []
        self.view.signal_batch_archive.connect(got.append)
        self.view._archive_btn.dropEvent(
            _drag_event(_card_mime(self.ids[0], self.ids[1])))
        self.assertEqual(got, [[self.ids[0], self.ids[1]]])

    def test_today_drop_emits_to_today_signal(self):
        got = []
        self.view.signal_cards_to_today.connect(got.append)
        self.view._today_btn.dropEvent(
            _drag_event(_card_mime(self.ids[0])))
        self.assertEqual(got, [[self.ids[0]]])

    def test_non_card_drop_ignored(self):
        got = []
        self.view.signal_batch_archive.connect(got.append)
        self.view._archive_btn.dropEvent(_drag_event(QMimeData()))
        self.assertEqual(got, [])

    def test_drop_clears_selection(self):
        """拖到按钮 = 对这几张执行动作，落点完成后多选栏随之收起"""
        self.view._selected_ids = set(self.ids)
        self.view._update_selection_bar()
        self.view._archive_btn.dropEvent(
            _drag_event(_card_mime(self.ids[0], self.ids[1])))
        self.assertEqual(self.view.selection_ids(), [])

    def test_highlight_property_toggles_on_enter_and_leave(self):
        btn = _CardDropButton("测试")
        mime = _card_mime("c1")
        btn.dragEnterEvent(_drag_event(mime, "dragEnter"))
        self.assertTrue(btn.property("dropTarget"))
        from PySide6.QtGui import QDragLeaveEvent
        btn.dragLeaveEvent(QDragLeaveEvent())
        self.assertFalse(btn.property("dropTarget"))
        btn.deleteLater()


class GroupMoveControllerTest(unittest.TestCase):
    """控制器：成组移动的落点修正与撤销"""

    @classmethod
    def setUpClass(cls):
        from tests import shared_controller
        cls.c = shared_controller()

    def setUp(self):
        board = self.c._store.load()
        for lst in board.lists:
            lst.cards.clear()
        # 直接摆好顺序：_on_card_add 是"插到列首"，连加三张会得到反序，
        # 用例里要断言组内顺序就必须自己掌控初始排列
        board.lists[0].cards[:] = [Card(title="A"), Card(title="B"),
                                   Card(title="C")]
        self.c._after_data_change(None)
        self.c._undo_stack.clear()
        self.lists = self.c._store.load().lists

    def _titles(self, index=0):
        return [c.title for c in self.c._store.load().lists[index].cards]

    def _ids(self, index=0):
        return [c.id for c in self.c._store.load().lists[index].cards]

    def test_group_move_to_other_list_keeps_order(self):
        ids = self._ids()
        self.c._on_cards_move([ids[0], ids[2]], self.lists[1].id, 0)
        self.assertEqual(self._titles(0), ["B"])
        self.assertEqual(self._titles(1), ["A", "C"])   # 组内顺序保持

    def test_group_move_within_same_list_lands_at_anchor(self):
        """同列内成组下移：落点按"锚卡片"计算，不因摘除成员而后移"""
        ids = self._ids()          # A B C
        # 拖 A、B 到 C 之前（index=2 指 C）→ 位置不变
        self.c._on_cards_move([ids[0], ids[1]], self.lists[0].id, 2)
        self.assertEqual(self._titles(0), ["A", "B", "C"])

    def test_group_move_to_end_of_same_list(self):
        ids = self._ids()
        self.c._on_cards_move([ids[0], ids[1]], self.lists[0].id, 3)
        self.assertEqual(self._titles(0), ["C", "A", "B"])

    def test_group_move_is_single_undo_step(self):
        ids = self._ids()
        self.c._on_cards_move([ids[0], ids[1]], self.lists[1].id, 0)
        self.c._on_undo_requested()
        self.assertEqual(self._titles(0), ["A", "B", "C"])
        self.assertEqual(self._titles(1), [])

    def test_group_move_skips_archived(self):
        board = self.c._store.load()
        ids = self._ids()
        board.lists[0].cards[0].archived = True
        self.c._on_cards_move(ids, self.lists[1].id, 0)
        self.assertEqual(self._titles(1), ["B", "C"])

    def test_empty_selection_is_noop(self):
        self.c._on_cards_move([], self.lists[1].id, 0)
        self.assertEqual(self.c._undo_stack, [])

    def test_drop_to_today_stars_cards(self):
        ids = self._ids()
        self.c._on_cards_to_today([ids[0], ids[1]])
        cards = self.c._store.load().lists[0].cards
        self.assertTrue(cards[0].starred and cards[1].starred)
        self.assertFalse(cards[2].starred)
        self.c._on_undo_requested()
        self.assertFalse(any(c.starred
                             for c in self.c._store.load().lists[0].cards))

    def test_drop_to_today_already_starred_is_noop(self):
        ids = self._ids()
        board = self.c._store.load()
        board.lists[0].cards[0].starred = True
        self.c._undo_stack.clear()
        self.c._on_cards_to_today([ids[0]])
        self.assertEqual(self.c._undo_stack, [])   # 不留空撤销步


if __name__ == "__main__":
    unittest.main(verbosity=2)

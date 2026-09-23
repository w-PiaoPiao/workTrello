# -*- coding: utf-8 -*-
"""
备注悬浮预览测试：有备注卡片才有「≡ 有备注」徽章；
悬停徽章弹出浮层并完整展示备注（保留换行）；移开延迟隐藏。

用法：QT_QPA_PLATFORM=offscreen python tests/test_notes_popover.py
"""

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 数据目录隔离：单文件 / discover / pytest 运行方式下
# 都不得读写真实用户数据（防止全量回归清空 %LOCALAPPDATA% 看板）
os.environ["PET_BOARD_DATA_DIR"] = tempfile.mkdtemp()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

_qapp = QApplication.instance() or QApplication([])

from app.models.board import BoardList, Card
from app.views.board_view import BoardView
from app.views.notes_popover import (
    _WATCH_MS,
    _reset_popover,
    hide_notes_popover,
    notes_popover,
)


def _make_view():
    view = BoardView()
    view.refresh([BoardList(title="待办", cards=[
        Card(title="带备注", notes="第一行\n09-08 14:30 完成 A\n第二行"),
        Card(title="无备注"),
        Card(title="另一条备注", notes="B 卡备注内容"),
    ])])
    view.resize(900, 600)
    view.show()
    return view


class NotesBadgeTest(unittest.TestCase):
    def setUp(self):
        _reset_popover()
        self.view = _make_view()

    def tearDown(self):
        hide_notes_popover()
        self.view.hide()
        self.view.deleteLater()
        _reset_popover()

    def _cards(self):
        return self.view._columns[0]._card_widgets

    def test_badge_only_when_notes(self):
        c1, c2, _c3 = self._cards()
        self.assertIsNotNone(c1._notes_badge)
        self.assertEqual(c1._notes_badge.text(), "≡ 有备注")
        self.assertIsNone(c2._notes_badge)      # 无备注无徽章

    def test_badge_has_no_native_tooltip(self):
        """徽章不挂原生 tooltip：否则悬停约 700ms 后系统再弹一个提示窗，
        压在自绘备注浮层上造成双重叠字（提示语已移入浮层内的提示行）"""
        c1, *_ = self._cards()
        self.assertEqual(c1._notes_badge.toolTip(), "")

    def test_hover_shows_full_notes(self):
        c1, *_ = self._cards()
        c1.eventFilter(c1._notes_badge, QEvent(QEvent.Enter))
        pop = notes_popover()
        self.assertTrue(pop.isVisible())
        self.assertEqual(pop._body.text(), "第一行\n09-08 14:30 完成 A\n第二行")
        pop.hide_now()

    def test_leave_schedules_hide(self):
        """Leave 只延迟不立即隐藏；hide_now 立即生效"""
        c1, *_ = self._cards()
        c1.eventFilter(c1._notes_badge, QEvent(QEvent.Enter))
        pop = notes_popover()
        c1.eventFilter(c1._notes_badge, QEvent(QEvent.Leave))
        self.assertTrue(pop.isVisible())        # 延迟期内仍可见
        pop.hide_now()
        self.assertFalse(pop.isVisible())

    def test_click_pins_and_hover_does_not_steal(self):
        """点击徽章固定展示；固定期间悬停其他徽章不抢占内容、移开不关闭"""
        c1, _c2, c3 = self._cards()
        press = QMouseEvent(QEvent.Type.MouseButtonPress,
                            QPoint(0, 0), Qt.LeftButton, Qt.LeftButton,
                            Qt.NoModifier)
        c1.eventFilter(c1._notes_badge, press)
        pop = notes_popover()
        self.assertTrue(pop.isVisible())
        self.assertTrue(pop.is_pinned())
        self.assertTrue(pop.pinned_for(c1.card().id))
        self.assertEqual(pop._body.text(), c1.card().notes)
        # 固定中：Leave 不自动隐藏
        c1.eventFilter(c1._notes_badge, QEvent(QEvent.Leave))
        self.assertTrue(pop.isVisible())
        # 固定中：悬停另一卡徽章不切换内容
        c3.eventFilter(c3._notes_badge, QEvent(QEvent.Enter))
        self.assertTrue(pop.pinned_for(c1.card().id))
        self.assertEqual(pop._body.text(), c1.card().notes)
        pop.hide_now()

    def test_click_again_unpins(self):
        """同一徽章再点一次收起；点击不冒泡打开卡片编辑"""
        c1, *_ = self._cards()
        press = QMouseEvent(QEvent.Type.MouseButtonPress,
                            QPoint(0, 0), Qt.LeftButton, Qt.LeftButton,
                            Qt.NoModifier)
        edited = []
        c1.signal_edit_requested.connect(edited.append)
        c1.eventFilter(c1._notes_badge, press)
        pop = notes_popover()
        self.assertTrue(pop.isVisible())
        self.assertEqual(edited, [])            # 点击徽章不误触编辑对话框
        c1.eventFilter(c1._notes_badge, press)  # 再点一次收起
        self.assertFalse(pop.isVisible())
        self.assertFalse(pop.is_pinned())

    def test_pin_switches_to_other_badge(self):
        """固定于 A 时点击 B 徽章：固定归属切到 B"""
        c1, _c2, c3 = self._cards()
        press = QMouseEvent(QEvent.Type.MouseButtonPress,
                            QPoint(0, 0), Qt.LeftButton, Qt.LeftButton,
                            Qt.NoModifier)
        c1.eventFilter(c1._notes_badge, press)
        pop = notes_popover()
        self.assertTrue(pop.pinned_for(c1.card().id))
        c3.eventFilter(c3._notes_badge, press)
        self.assertTrue(pop.is_pinned())
        self.assertTrue(pop.pinned_for(c3.card().id))
        self.assertEqual(pop._body.text(), c3.card().notes)
        pop.hide_now()

    def test_model_update_unpins_pinned_preview(self):
        """本卡模型刷新（如编辑保存）后收起其固定预览，避免残留旧备注文本"""
        c1, *_ = self._cards()
        press = QMouseEvent(QEvent.Type.MouseButtonPress,
                            QPoint(0, 0), Qt.LeftButton, Qt.LeftButton,
                            Qt.NoModifier)
        c1.eventFilter(c1._notes_badge, press)
        pop = notes_popover()
        self.assertTrue(pop.is_pinned())
        c1.card().notes = "编辑后的备注"
        c1.update_from_model(c1.card())         # 模拟控制器编辑保存后的刷新
        self.assertFalse(pop.isVisible())
        self.assertFalse(pop.is_pinned())

    def test_popover_theme_repaint(self):
        """深浅主题切换后浮层可正常绘制"""
        from app.views.theme import AppTheme
        c1, *_ = self._cards()
        c1.eventFilter(c1._notes_badge, QEvent(QEvent.Enter))
        pop = notes_popover()
        AppTheme.set_mode("dark")
        pop.grab()
        AppTheme.set_mode("light")
        pop.grab()
        pop.hide_now()

    # ── 今日清单浮窗的主题跟随 ────────────────────────────

    def test_today_popover_follows_theme(self):
        """切主题后今日浮窗配色跟随（回归：此前未注册主题回调，配色冻结）

        浮窗外壳与每行的配色都是构建时的快照，必须由 AppTheme.register
        的回调重刷；只测外壳不足以覆盖"行还是旧主题"的情形。
        """
        from app.views.theme import AppTheme
        from app.views.today_popover import TodayPopover
        from datetime import date
        card = Card(title="今日卡", due_date=date.today().isoformat())
        lst = BoardList(title="待办", cards=[card])
        pop = TodayPopover()
        try:
            pop.set_items([(lst, card)])
            AppTheme.set_mode("dark")
            self.assertIn(AppTheme.colors()["bg_card"],
                          pop._frame.styleSheet())          # 外壳已跟随
            dark_row = pop._rows[0][2]
            self.assertIn(AppTheme.colors()["text_primary"],
                          dark_row.findChildren(type(pop._title_label))[0]
                          .styleSheet())                     # 行文字已跟随
            # 行仍是同一份数据（重建后行数不减）
            self.assertEqual(len(pop._rows), 1)
            AppTheme.set_mode("light")
            self.assertIn(AppTheme.colors()["bg_card"],
                          pop._frame.styleSheet())
        finally:
            pop.close()
            pop.deleteLater()


class PopoverAppearanceTest(unittest.TestCase):
    """浮层配色与可读性：底色必须不透明

    回归背景：浮层此前复用看板列的 bg_panel（rgba 白 0.86 / 深色 0.92），
    叠加 WA_TranslucentBackground 后，下方卡片文字约有 14% 透上来，与备注
    正文叠成重影，正文基本读不出来。这里锁死"底色不透明"。
    """

    def setUp(self):
        _reset_popover()

    def tearDown(self):
        hide_notes_popover()
        _reset_popover()

    @staticmethod
    def _panel(pop):
        """承载外观的面板控件（重构前外观直接设在浮层自身）"""
        return getattr(pop, "_panel", pop)

    def _background(self, qss: str) -> str:
        m = re.search(r"background:\s*([^;]+);", qss)
        self.assertIsNotNone(m, f"样式表里没有 background 声明: {qss!r}")
        return m.group(1).strip()

    def test_popover_background_is_opaque(self):
        """底色解析后 alpha 必须为 255

        注：离屏下 render()/grab() 不对 WA_TranslucentBackground 的顶层窗
        合成内容（实测恒返回全透明），故按 QSS 口径断言而非像素比对。
        """
        pop = notes_popover()
        pop.show_for("备注正文", QRect(400, 400, 60, 18))
        bg = self._background(self._panel(pop).styleSheet())
        col = QColor(bg)
        self.assertTrue(col.isValid(), f"无法解析的底色 {bg!r}")
        self.assertEqual(col.alpha(), 255,
                         f"浮层底色 {bg} 半透明，下方卡片文字会透上来")

    def test_popover_bg_token_opaque_in_both_themes(self):
        """专属底色 token 在深浅主题下都是不透明色"""
        from app.views.theme import AppTheme
        try:
            for mode in ("light", "dark"):
                AppTheme.set_mode(mode)
                c = AppTheme.colors()
                self.assertIn("popover_bg", c, f"{mode} 主题缺少 popover_bg")
                col = QColor(c["popover_bg"])
                self.assertTrue(col.isValid(), c["popover_bg"])
                self.assertEqual(col.alpha(), 255,
                                 f"{mode} 的 popover_bg 必须不透明")
        finally:
            AppTheme.set_mode("light")

    def test_popover_uses_dedicated_border(self):
        """用专属重色描边，而非卡片那套 10% 透明度的细边

        白底浮层压在白卡上时，10% 的描边几乎看不出边界，观感上"糊在一起"。
        """
        from app.views.theme import AppTheme
        pop = notes_popover()
        pop.show_for("备注正文", QRect(400, 400, 60, 18))
        qss = self._panel(pop).styleSheet()
        c = AppTheme.colors()
        self.assertIn(c["popover_border"], qss, "未使用专属 popover_border")
        self.assertNotEqual(c["popover_border"], c["border"])

    def test_hint_visible_only_when_hovering(self):
        """「点击徽章固定」提示只在悬停态出现

        悬停 → 固定 的切换中，正文与锚点都没变，若重建短路判定漏掉提示行
        状态，提示会赖着不走。
        """
        pop = notes_popover()
        anchor = QRect(400, 400, 60, 18)
        pop.show_for("备注正文", anchor)
        self.assertFalse(pop._hint.isHidden(), "悬停态应显示「点击徽章固定」提示")
        pop.show_pinned("card-hint", "备注正文", anchor)
        self.assertTrue(pop._hint.isHidden(), "固定态不该再提示点击固定")
        pop.hide_now()


class PopoverPlacementTest(unittest.TestCase):
    """浮层定位：上方优先（盖住本卡下缘，不压下一张卡），边界自动翻转/夹紧"""

    def setUp(self):
        _reset_popover()
        self.avail = QApplication.primaryScreen().availableGeometry()

    def tearDown(self):
        hide_notes_popover()
        _reset_popover()

    def _show(self, anchor: QRect, text: str = "第一行\n09-08 14:30 完成 A\n第二行"):
        pop = notes_popover()
        pop.show_for(text, anchor)
        return pop

    def test_panel_sits_above_badge(self):
        """默认弹在徽章上方，且不与徽章所在行重叠"""
        anchor = QRect(self.avail.left() + 60,
                       self.avail.top() + self.avail.height() // 2, 60, 18)
        pop = self._show(anchor)
        panel = pop.panel_geometry_global()
        self.assertLess(panel.bottom(), anchor.top(), "面板应在徽章上方")
        self.assertFalse(panel.intersects(anchor), "面板压在徽章上")
        self.assertGreaterEqual(panel.top(), self.avail.top(), "面板越出屏幕顶部")

    def test_panel_flips_below_when_no_room_above(self):
        """顶部附近上方放不下时翻到徽章下方，且不越出屏幕底部"""
        anchor = QRect(self.avail.left() + 60, self.avail.top() + 2, 60, 18)
        pop = self._show(anchor, "\n".join(f"第{i}行备注内容" for i in range(12)))
        panel = pop.panel_geometry_global()
        self.assertGreaterEqual(panel.top(), anchor.bottom(), "应翻到徽章下方")
        self.assertLessEqual(panel.bottom(), self.avail.bottom(), "面板越出屏幕底部")

    def test_panel_clamped_inside_right_edge(self):
        """贴右缘的徽章：面板回拉进屏内"""
        anchor = QRect(self.avail.right() - 8,
                       self.avail.top() + self.avail.height() // 2, 60, 18)
        pop = self._show(anchor)
        panel = pop.panel_geometry_global()
        self.assertLessEqual(panel.right(), self.avail.right(), "面板越出屏幕右侧")
        self.assertGreaterEqual(panel.left(), self.avail.left(), "面板越出屏幕左侧")

    def test_long_notes_stay_above_badge_by_capping_body(self):
        """长备注：上方放不下时压缩正文留在上方，而不是翻到徽章下方

        回归：徽章在卡片底部信息行，长备注（进度流水）面板可高 370px，
        而看板上半部多数徽章的"上方余量"都不够——老判据（上方放不下就
        翻转）于是几乎必然触发，表现就是"还是压住下一张卡"。
        """
        note = "\n".join(f"09-{i:02d} 14:30 进展记录第{i}条"
                         for i in range(1, 21))
        # 屏幕中部：上方余量充足 → 按自然高展示
        roomy = self._show(QRect(self.avail.left() + 60,
                                 self.avail.bottom() - 400, 60, 18), note)
        natural_h = roomy.panel_geometry_global().height()
        # 上部：上方余量不足 → 压缩正文高度留在上方
        anchor = QRect(self.avail.left() + 60, self.avail.top() + 300, 60, 18)
        pop = self._show(anchor, note)
        panel = pop.panel_geometry_global()
        self.assertLess(panel.height(), natural_h, "正文高度未按可用空间压缩")
        self.assertLess(panel.bottom(), anchor.top(), "长备注翻到了徽章下方")
        self.assertGreaterEqual(panel.top(), self.avail.top(), "面板越出屏幕顶部")

    def test_panel_rendered_size_matches_placement(self):
        """长→短连续展示后浮层缩回短备注高度，且渲染尺寸=定位尺寸

        回归：顶层窗口的最小尺寸会被上一版布局缓存拖住，多出来的一截会
        向下压住徽章与下一张卡（截图里"浮层压住卡片"就是这个成因）。
        """
        anchor = QRect(self.avail.left() + 60,
                       self.avail.bottom() - 400, 60, 18)
        note = "\n".join(f"进展第{i}条" for i in range(1, 21))
        tall = self._show(anchor, note).panel_geometry_global().height()
        pop = self._show(anchor, "两行\n备注")
        QApplication.processEvents()   # 让布局真正跑一遍：缓存拖住的最小
                                       # 尺寸只在布局激活后才显形
        panel = pop.panel_geometry_global()
        self.assertLess(panel.height(), tall, "短备注没有缩回自然高度")
        self.assertEqual(panel.top() + panel.height(), anchor.top() - 6,
                         "渲染出的底边与定位用的高度不一致，浮层会多压一截")
        self.assertEqual((pop._panel.width(), pop._panel.height()),
                         (panel.width(), panel.height()),
                         "面板 live 尺寸与定位口径不一致")


class HoverScopeWatchTest(unittest.TestCase):
    """悬停巡检：光标离开浮层与锚点徽章就收起（漏投递 Leave 的兜底）

    回归背景：列表重建（徽章控件被销毁，Leave 无从投递）、看板滚动、拖拽、
    切换应用等路径都可能收不到 Leave；只靠事件补齐会留下"鼠标早就移开了、
    浮层还赖在原地"的残留（用户报的"有时候鼠标离开了不消失"）。
    """

    def setUp(self):
        _reset_popover()
        self.view = _make_view()

    def tearDown(self):
        hide_notes_popover()
        self.view.hide()
        self.view.deleteLater()
        _reset_popover()

    def _cards(self):
        return self.view._columns[0]._card_widgets

    def _hover(self, card):
        card.eventFilter(card._notes_badge, QEvent(QEvent.Enter))
        return notes_popover()

    @staticmethod
    def _at(pop, pos):
        """注入巡检读到的光标位置（离屏下没有真实光标可摆）"""
        pop._cursor_pos = lambda: pos
        return pop

    def test_badge_widget_registered_as_anchor(self):
        """徽章控件随悬停一起交给浮层：巡检才有实时矩形可用"""
        c1, *_ = self._cards()
        pop = self._hover(c1)
        self.assertIs(pop._anchor_widget, c1._notes_badge)
        self.assertTrue(pop.anchored_to(c1._notes_badge))
        self.assertFalse(pop.anchored_to(None))

    def test_watch_runs_only_while_visible(self):
        c1, *_ = self._cards()
        pop = self._hover(c1)
        self.assertTrue(pop._watch.isActive(), "悬停展示期间巡检没有启动")
        pop.hide_now()
        self.assertFalse(pop._watch.isActive(), "浮层已隐藏，巡检仍在空跑")

    def test_scope_follows_badge_instead_of_stale_rect(self):
        """按徽章实时矩形判定：登记矩形过期（卡片重排/滚动）也不误判"""
        c1, *_ = self._cards()
        pop = self._hover(c1)
        live = pop._anchor_rect()
        pop._recent_anchor = QRect(0, 0, 1, 1)      # 登记矩形已过期
        self.assertIsNotNone(live)
        self.assertTrue(pop._cursor_in_scope(live.center()),
                        "光标仍在徽章上却被判为已离开")

    def test_cursor_left_badge_and_panel_hides(self):
        c1, *_ = self._cards()
        pop = self._hover(c1)
        badge = pop._anchor_rect()
        self._at(pop, QPoint(badge.right() + 400, badge.bottom() + 400))
        pop._revalidate_scope()
        self.assertFalse(pop.isVisible(), "光标早已离开，浮层却没收起")

    def test_cursor_on_badge_keeps_popover(self):
        c1, *_ = self._cards()
        pop = self._hover(c1)
        self._at(pop, pop._anchor_rect().center())
        pop._revalidate_scope()
        self.assertTrue(pop.isVisible(), "光标还在徽章上就把浮层收掉了")

    def test_watch_reaps_after_cursor_leaves_without_any_event(self):
        """整条巡检链路（定时器 → 位置复核 → 收起）在真事件循环里跑通

        模拟"漏投递 Leave"：只挪光标位置，不发任何事件。
        """
        c1, *_ = self._cards()
        pop = self._hover(c1)
        self._at(pop, pop._anchor_rect().center())
        QTest.qWait(_WATCH_MS * 3)      # 巡检跑过两轮：仍悬停 → 不收
        self.assertTrue(pop.isVisible(), "巡检把仍在悬停的浮层收掉了")
        badge = pop._anchor_rect()
        self._at(pop, QPoint(badge.right() + 400, badge.bottom() + 400))
        QTest.qWait(_WATCH_MS * 3)      # 光标已离开 → 巡检收口
        self.assertFalse(pop.isVisible(), "漏投递 Leave 时巡检没有收口")

    def test_destroyed_badge_falls_back_to_registered_rect(self):
        """徽章被重建销毁：光标仍压原处不闪掉，离开照常收起"""
        from shiboken6 import delete
        c1, *_ = self._cards()
        pop = self._hover(c1)
        rect = pop._anchor_rect()
        delete(c1._notes_badge)         # 模拟 rebuild 销毁旧徽章控件
        c1._notes_badge = None
        self.assertEqual(pop._anchor_rect(), rect, "控件没了就丢了锚点矩形")
        self._at(pop, rect.center())
        pop._revalidate_scope()
        self.assertTrue(pop.isVisible(), "光标还压在原处，浮层却闪掉了")
        self._at(pop, QPoint(rect.right() + 300, rect.bottom() + 300))
        pop._revalidate_scope()
        self.assertFalse(pop.isVisible(), "光标离开后仍不收口")

    def test_pinned_popover_not_reaped_by_watch(self):
        """固定展示不受巡检影响：那是用户明示的常驻态"""
        c1, *_ = self._cards()
        press = QMouseEvent(QEvent.Type.MouseButtonPress,
                            QPoint(0, 0), Qt.LeftButton, Qt.LeftButton,
                            Qt.NoModifier)
        c1.eventFilter(c1._notes_badge, press)
        pop = notes_popover()
        self.assertTrue(pop.is_pinned())
        self._at(pop, QPoint(5000, 5000))
        pop._revalidate_scope()
        self.assertTrue(pop.isVisible(), "固定展示被巡检收掉了")
        pop.hide_now()

    def test_content_change_hides_hover_preview(self):
        """本卡内容真的变了 → 悬停预览立即收起，免得展示旧内容"""
        c1, *_ = self._cards()
        pop = self._hover(c1)
        c1.card().title = "改过的标题"
        c1.update_from_model(c1.card())
        self.assertFalse(pop.isVisible())

    def test_unrelated_refresh_keeps_hover_preview(self):
        """与指纹无关的刷新不打断悬停预览

        备注正文不进指纹（只 bool 参与）：别处一次无关刷新若顺手收起
        浮层，用户正在读的备注会莫名闪掉。
        """
        c1, *_ = self._cards()
        pop = self._hover(c1)
        c1.card().notes = "别处刷新时改的正文"
        c1.update_from_model(c1.card())
        self.assertTrue(pop.isVisible(), "无关刷新把正在读的浮层闪掉了")


class PopoverUnitTest(unittest.TestCase):
    def setUp(self):
        _reset_popover()

    def tearDown(self):
        hide_notes_popover()
        _reset_popover()

    def test_empty_text_hides(self):
        pop = notes_popover()
        pop.show_for("   ", pop.geometry())
        self.assertFalse(pop.isVisible())

    def test_multiline_kept_and_wrapped_width(self):
        """宽度按面板口径断言：外层还含自绘阴影的边距，窗口宽不再是可见宽"""
        pop = notes_popover()
        pop.show_for("短", pop.geometry())
        self.assertTrue(pop.isVisible())
        w_short = pop.panel_geometry_global().width()
        pop.show_for("很长" * 300, pop.geometry())
        self.assertTrue(pop.isVisible())
        # 长文至少不更窄
        self.assertGreaterEqual(pop.panel_geometry_global().width(), w_short)
        # 面板宽 = 正文封顶宽 + 左右内边距 + 左右描边
        m = pop._panel.layout().contentsMargins()
        cap = pop._MAX_WIDTH + m.left() + m.right() \
            + pop._panel.frameWidth() * 2
        self.assertLessEqual(pop.panel_geometry_global().width(), cap)
        pop.hide_now()

    def test_show_twice_reuses_singleton(self):
        self.assertIs(notes_popover(), notes_popover())

    def test_repeated_show_same_content_skips_rebuild(self):
        """显隐/换内容均不触发样式重设（样式只随主题回调下发，回归护栏）

        此前 _show 里调用 reapply_style：样式明明只依赖主题，却在多张
        带备注卡间快速划过时反复 reparse。
        """
        from PySide6.QtCore import QRect
        pop = notes_popover()
        calls = []
        orig = pop.reapply_style
        pop.reapply_style = lambda: (calls.append(1), orig())
        anchor = QRect(200, 200, 60, 18)
        pop.show_for("同一段备注", anchor)
        pop.show_for("同一段备注", anchor)      # 重复展示 → 跳过排版重建
        pop.show_for("换一段备注", anchor)      # 内容变化 → 只重建排版
        pop.hide_now()
        pop.show_for("同一段备注", anchor)      # 隐藏后重新展示
        self.assertEqual(len(calls), 0)        # 样式重设从不由 show 触发
        pop.hide_now()


if __name__ == "__main__":
    unittest.main(verbosity=2)

# -*- coding: utf-8 -*-
"""界面密度护栏：三档缩放必须真的生效，且放大后不能裁切/重叠

阶段 7 的验收要求是「三档密度 × 浅深主题 × 中英文共 12 种组合下无裁切无重叠」。
这里把可自动化的部分固化下来。

为什么不能只测 token 数值
------------------------
密度是**运行时**偏好，而 QSS 里的字号/间距/圆角都是生成时焊死的字符串。
只把 token 改对、忘了把缓存键带上档位（`_board_qss` 原本按 mode 缓存），
或者忘了重下样式表，都会出现"设置里切了、界面没变"——而 token 数值测试
照样全绿。所以这里同时断言生成物确实随档位变化。

运行：python -m unittest tests.test_density
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PET_BOARD_DATA_DIR"] = tempfile.mkdtemp()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import (      # noqa: E402
    QApplication,
    QVBoxLayout,
    QWidget,
)

_qapp = QApplication.instance() or QApplication([])

from app.config import AppConfig            # noqa: E402
from app.models.board import BoardList, Card   # noqa: E402
from app.views import tokens                # noqa: E402
from app.views.board_view import BoardView, _board_qss   # noqa: E402
from app.views.theme import AppTheme        # noqa: E402

SCALES = ("compact", "normal", "comfortable")


class ScaleTeardownMixin:
    """还原偏好设置；**不碰全局样式**

    密度是进程级偏好，`save_ui_scale` 写进共享 settings，不还原会污染后续
    700 多个用例。

    但这里刻意**不调用 `AppTheme.apply()`**：那会通知所有已注册监听者，
    而全量套件跑到本文件时，共享 AppController 已经挂着前序用例建出的一大棵
    控件树，每次全局 apply 都要把它整棵重新 polish。实测本文件 11 个用例
    里几十次全局 apply 会把全量套件从 37s 拖到 130s——而本文件单独跑只要
    0.8s，因为那时树还是空的。
    真正需要验证全局通路的只有 `GlobalStylePathTest` 两个用例，它们自己负责
    还原。
    """

    def setUp(self):
        self._prev_scale = AppConfig.get_ui_scale()

    def tearDown(self):
        AppConfig.save_ui_scale(self._prev_scale)


class TokenScalingTest(ScaleTeardownMixin, unittest.TestCase):
    """token 换算层：单调、有下限、覆盖字号/间距/圆角/控件尺寸"""

    def test_sizes_increase_monotonically(self):
        seen = {}
        for s in SCALES:
            AppConfig.save_ui_scale(s)
            seen[s] = (tokens.font(AppConfig.UI_FONT_BODY),
                       tokens.space(8),
                       tokens.radius("control"),
                       tokens.size(56))
        self.assertLess(seen["compact"], seen["normal"])
        self.assertLess(seen["normal"], seen["comfortable"])

    def test_caption_never_drops_below_floor(self):
        """紧凑档也不能把最小字号压回 10px——那正是本计划要消灭的档位"""
        for s in SCALES:
            AppConfig.save_ui_scale(s)
            self.assertGreaterEqual(
                tokens.font(AppConfig.UI_FONT_CAPTION), 11,
                f"{s} 档下最小字号被压到 11px 以下")

    def test_control_size_has_its_own_floor(self):
        """控件尺寸的下限比间距高：12px 的圆形按钮在任何档位都点不中

        区别只在**小值**上体现——间距可以细到 1px（发丝分隔线），
        控件尺寸不行（点不中）。大值上两者同系数，本来就该相等。
        """
        AppConfig.save_ui_scale("compact")
        self.assertGreaterEqual(tokens.size(10), 8)
        self.assertGreater(tokens.size(6), tokens.space(6),
                           "控件尺寸与间距在小值上必须拉开：前者有 8px 下限")

    def test_invalid_scale_falls_back_to_normal(self):
        AppConfig.save_ui_scale("gigantic")     # 白名单外，应被忽略
        self.assertEqual(AppConfig.get_ui_scale(), "normal")


class GeneratedStyleFollowsScaleTest(ScaleTeardownMixin, unittest.TestCase):
    """生成物必须真的随档位变化（缓存键/重下样式表最容易漏的地方）

    这几个用例只读 `_board_qss` / `global_qss` 字符串，不需要下发到任何
    控件，因此完全不碰全局状态。
    """

    def test_board_qss_differs_per_scale(self):
        AppConfig.save_ui_scale("compact")
        small = _board_qss("light", tokens.scale())
        AppConfig.save_ui_scale("comfortable")
        big = _board_qss("light", tokens.scale())
        self.assertNotEqual(small, big,
                            "看板 QSS 没随密度变化——多半是缓存键漏了 scale")

    def test_board_qss_cache_is_keyed_by_scale(self):
        """切档后必须拿到新样式表，而不是缓存里的旧串"""
        AppConfig.save_ui_scale("normal")
        normal = _board_qss("light", tokens.scale())
        AppConfig.save_ui_scale("comfortable")
        comfy = _board_qss("light", tokens.scale())
        AppConfig.save_ui_scale("normal")
        again = _board_qss("light", tokens.scale())
        self.assertNotEqual(normal, comfy)
        self.assertEqual(normal, again, "回到原档位应命中同一份缓存")



class NoClippingTest(ScaleTeardownMixin, unittest.TestCase):
    """放大字号后工具栏不得重叠、控件不得被挤出窗口

    样式直接下发给被测控件（`setStyleSheet(_board_qss(...))`），不走
    `AppTheme.apply()`——见 ScaleTeardownMixin 的说明。
    """

    def _apply(self, scale, mode="light"):
        AppConfig.save_ui_scale(scale)
        self.view.setStyleSheet(_board_qss(mode, tokens.scale()))

    def setUp(self):
        super().setUp()          # 记录改前的全局状态
        self.host = QWidget()
        lay = QVBoxLayout(self.host)
        lay.setContentsMargins(0, 0, 0, 0)
        self.view = BoardView()
        lay.addWidget(self.view)
        lst = BoardList(title="待办")
        for t in ("A", "B", "C"):
            lst.cards.append(Card(title=t))
        self.view.refresh([lst])
        self.host.show()
        QApplication.processEvents()

    def tearDown(self):
        self.host.hide()
        self.host.deleteLater()
        super().tearDown()

    def _visible_toolbar_items(self):
        return [(k, w) for k, w in self.view._toolbar_items
                if not w.isHidden()
                and not (k == "overflow" and w.isHidden())]

    def test_toolbar_never_overlaps_at_any_density(self):
        for scale in SCALES:
            self._apply(scale)
            for width in (1100, 900, 760, 640, 480):
                with self.subTest(scale=scale, width=width):
                    # setFixedWidth 而非 resize：顶层控件 resize 会被
                    # minimumSizeHint 顶回去，窄窗用例会假绿
                    self.host.setFixedWidth(width)
                    QApplication.processEvents()
                    self.view._apply_toolbar_overflow()
                    QApplication.processEvents()
                    rects = [(k, w.geometry())
                             for k, w in self._visible_toolbar_items()]
                    for i in range(len(rects)):
                        for j in range(i + 1, len(rects)):
                            k1, r1 = rects[i]
                            k2, r2 = rects[j]
                            self.assertFalse(
                                r1.intersects(r2),
                                f"{scale}@{width}px: {k1} 与 {k2} 重叠")

    def test_columns_are_not_squeezed_below_usable_width(self):
        """列宽随密度放大，且不会被压到装不下一张卡"""
        widths = {}
        for scale in SCALES:
            self._apply(scale)
            self.host.setFixedWidth(1400)
            QApplication.processEvents()
            widths[scale] = self.view._columns[0].width()
        self.assertLess(widths["compact"], widths["comfortable"])
        for scale, w in widths.items():
            self.assertGreater(w, 180, f"{scale} 档下列宽 {w}px 过窄")

    def test_all_themes_and_scales_render(self):
        """三档 × 两主题实际渲染一遍，不抛异常"""
        from PySide6.QtGui import QPixmap
        from PySide6.QtCore import Qt
        for scale in SCALES:
            for mode in ("light", "dark"):
                self._apply(scale, mode)
                self.host.setFixedWidth(900)
                QApplication.processEvents()
                pm = QPixmap(self.host.size())
                pm.fill(Qt.transparent)
                self.host.render(pm)
        AppTheme.set_mode("light")


class GlobalStylePathTest(unittest.TestCase):
    """真正走一遍全局下发通路（本文件里唯一碰全局状态的地方）

    `global_qss()` 是纯字符串生成，不走这里也能测；但"密度档位改了之后
    QApplication 上的样式表与基准字体确实跟着变"只能在全局通路上验证——
    缓存键漏了 scale、或忘了重下样式表，都会表现为"设置里切了、界面没变"。
    这里只有两个用例，代价可控。
    """

    def setUp(self):
        self._prev_scale = AppConfig.get_ui_scale()
        self._prev_mode = AppTheme.mode()

    def tearDown(self):
        AppConfig.save_ui_scale(self._prev_scale)
        AppTheme.set_mode(self._prev_mode)
        AppTheme.apply()          # 本类改过全局，必须还原

    def test_global_qss_and_base_font_follow_scale(self):
        """下发给 QApplication 的样式表与基准字体都必须随档位变化

        只取两端（compact / comfortable）而不是三档：每次 `apply()` 都会让
        Qt 重 polish 整棵树，全量套件跑到这里时共享 AppController 已经挂着
        前序用例建出的一大棵控件树，实测一次 apply 约 4s。两端足以证明
        "随档位变化"，中间档的单调性由 TokenScalingTest 在纯数值层验证。
        """
        AppConfig.save_ui_scale("compact")
        AppTheme.apply()
        # 下发给 QApplication 的就是当档生成的那一份
        self.assertEqual(_qapp.styleSheet(), AppTheme.global_qss())
        small = (_qapp.font().pixelSize(), len(AppTheme.global_qss()))

        AppConfig.save_ui_scale("comfortable")
        AppTheme.apply()
        self.assertEqual(_qapp.styleSheet(), AppTheme.global_qss())
        big = (_qapp.font().pixelSize(), len(AppTheme.global_qss()))

        self.assertLess(small[0], big[0], "基准字体没随密度变化")
        self.assertNotEqual(small[1], big[1], "全局 QSS 没随密度变化")


if __name__ == "__main__":
    unittest.main(verbosity=2)

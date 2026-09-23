"""
备注悬浮预览卡片

鼠标悬停看板卡片上的「≡ 有备注」徽章时，弹出一张不抢焦点的
顶层小卡片完整展示备注正文（保留换行），移开自动关闭；
点击徽章可切换为「固定展示」（移开不关闭，再点收起）。

设计要点：
- Qt.ToolTip 顶层窗：不激活、不占任务栏、可悬浮在看板滚动区之上
  （若做成卡片子控件会被 QScrollArea 裁剪）
- 进程级单例，避免每张卡都建窗口
- 样式实时取 AppTheme.colors()，深浅主题自动跟随
- 底色不透明：复用看板列的 bg_panel 是半透明色，下方卡片文字会透上来
  与正文叠成重影；专用 popover_bg 保证正文清晰可读
- 默认弹在徽章上方（盖住本卡下缘，不压住下一张卡）；上方空间不足时压缩
  正文高度、超出部分改在浮层内滚动，只有连最小正文都放不下才翻到下方。
  翻转判据此后基本不再命中：长备注（进度流水）面板可高 370px，而徽章在
  看板上半部的卡片几乎都在该阈值以内 → 一翻就压住下一张卡，等于没修
- 悬停模式带巡检自愈：可见期间定期按光标实际位置复核，光标既不在浮层也
  不在锚点徽章上就立即收起。列表重建（徽章控件被销毁）、看板滚动、拖拽
  等路径都可能漏投递 Leave，只靠事件补齐会留下"鼠标早移开了、浮层还在"
- 软阴影在 paintEvent 手绘（静态，仅显隐/移动时重绘）；不用
  QGraphicsDropShadowEffect——motion.py 记录了其 8 倍于 opacity 的性能代价
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QPainter
from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QVBoxLayout
# 徽章控件可能已被重建销毁：C++ 对象失效后调 mapToGlobal 会抛异常
from shiboken6 import isValid as is_valid

from app.views.theme import AppTheme
from app.views import motion
from app.config import AppConfig
from app.i18n import tr

_HIDE_DELAY_MS = 160   # 徽章→浮层移动时的容忍延迟
_GAP = 6               # 浮层与锚点徽章之间的间隙
_SHADOW = 8            # 自绘软阴影的向外扩散边距（窗口命中区随之略大）
_MAX_BODY_H = 320      # 正文区高度上限：超长备注改为在浮层内滚动
_MIN_BODY_H = 44       # 正文区最小高度（约两行）：再矮就没有阅读价值，
                       # 此时才允许翻转到徽章下方
_WATCH_MS = 220        # 悬停巡检周期：漏投递 Leave 时的收口时延上限


class NotesPopover(QFrame):
    """备注悬浮预览（单例使用，不抢焦点）"""

    _MAX_WIDTH = 340

    def __init__(self):
        # Qt.Tool + 置顶：看板主窗是 WindowStaysOnTopHint，浮层必须同样置顶，
        # 否则会被压在看板之下（屏幕上不可见）；Tool 窗不占任务栏，
        # WA_ShowWithoutActivating 保证弹出不抢焦点
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._recent_anchor = None      # 最近锚定徽章（全局矩形）
        self._recent_hint = None        # 最近一次提示行可见性（重建判据之一）
        self._anchor_widget = None      # 锚点徽章控件：巡检按它的实时矩形
                                        # 判"光标还在徽章上"，被销毁则退回
                                        # 登记矩形（见 _anchor_rect）
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(_HIDE_DELAY_MS)
        self._hide_timer.timeout.connect(self._do_hide)
        # 巡检只在可见期间跑：漏投递的 Leave 不能指望事件补齐
        self._watch = QTimer(self)
        self._watch.setInterval(_WATCH_MS)
        self._watch.timeout.connect(self._revalidate_scope)
        self.setMouseTracking(True)
        self._pin_card_id: str | None = None   # 固定展示所属卡片 id（None=悬停模式）

        # 面板：承载底色/描边/圆角。外层自身保持透明，四周留 _SHADOW 画阴影
        self._panel = QFrame(self)
        self._panel.setObjectName("notesPopoverPanel")
        self._title = QLabel(tr("备注"))
        self._title.setObjectName("popTitle")
        self._body = QLabel()
        self._body.setObjectName("popBody")
        self._body.setWordWrap(True)
        self._body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._body.setMaximumWidth(self._MAX_WIDTH)
        # 正文进滚动区：长备注此前会把浮层撑到比屏幕还高、两端内容够不到
        # （今日浮窗有 330 上限＋滚动，两者标准本就该一致）
        self._body_scroll = QScrollArea()
        self._body_scroll.setFrameShape(QFrame.NoFrame)
        self._body_scroll.setWidgetResizable(True)
        self._body_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff)
        self._body_scroll.viewport().setAutoFillBackground(False)
        self._body_scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget"
            " { background: transparent; }")
        self._body_scroll.setWidget(self._body)
        # 徽章上原有一个原生 tooltip「悬停预览 · 点击固定」，悬停约 700ms 后
        # 会再弹一个系统提示窗压在浮层上；提示语移到这里，避免双层弹窗
        self._hint = QLabel(tr("点击徽章固定"))
        self._hint.setObjectName("popHint")

        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(12, 8, 12, 10)
        panel_layout.setSpacing(4)
        panel_layout.addWidget(self._title)
        panel_layout.addWidget(self._body_scroll)
        panel_layout.addWidget(self._hint)

        layout = QVBoxLayout(self)
        m = _SHADOW
        layout.setContentsMargins(m, m, m, m)
        layout.addWidget(self._panel)

        self.reapply_style()
        # 主题切换实时刷新（浮层打开时也跟随）
        AppTheme.register(self._on_theme_changed)

    def _on_theme_changed(self) -> None:
        self.reapply_style()
        self.update()

    def retexts(self) -> None:
        """语言切换：标题与提示行取当前语言（固定态隐藏提示行）"""
        self._title.setText(tr("备注"))
        self._hint.setText(tr("点击徽章固定"))
        self._hint.setVisible(self._pin_card_id is None)

    # ── 样式 ──────────────────────────────────────────────

    def reapply_style(self) -> None:
        c = AppTheme.colors()
        self._panel.setStyleSheet(f"""
            QFrame#notesPopoverPanel {{
                background: {c['popover_bg']};
                border: 1px solid {c['popover_border']};
                border-radius: 10px;
            }}
            QLabel#popTitle {{
                color: {c['text_secondary']};
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 1px;
                background: transparent;
            }}
            QLabel#popBody {{
                color: {c['text_primary']};
                font-size: 12px;
                background: transparent;
            }}
            QLabel#popHint {{
                color: {c['text_disabled']};
                font-size: 10px;
                background: transparent;
            }}
        """)

    # ── 阴影（手绘）──────────────────────────────────────

    def paintEvent(self, event) -> None:
        """在面板四周手绘软阴影

        由外向内叠加多圈递减透明度的圆角填充，形成柔和渐隐。静态绘制：
        只在显隐/移动/尺寸变化时重绘，无每帧开销。
        """
        super().paintEvent(event)
        shadow = QColor(AppTheme.colors()["popover_shadow"])
        if not shadow.isValid():
            return
        panel = QRectF(self._panel.geometry())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        base_radius = 10.0
        for i in range(_SHADOW, 0, -1):
            alpha = int(26 * (1 - i / (_SHADOW + 1)) ** 2) + 3
            col = QColor(shadow)
            col.setAlpha(alpha)
            painter.setBrush(col)
            painter.drawRoundedRect(panel.adjusted(-i, -i, i, i),
                                    base_radius + i, base_radius + i)
        painter.end()

    # ── 显隐 ──────────────────────────────────────────────

    def show_for(self, text: str, anchor_global: QRect,
                 badge=None) -> None:
        """悬停展示备注全文（临时：移开后延迟自动关闭）

        badge 为锚点徽章控件（可选）：巡检按它的实时矩形判定"光标还在徽章
        上"；控件被重建销毁时退回登记矩形，光标仍压在原处也不会闪掉。
        """
        self._pin_card_id = None
        self._anchor_widget = badge
        self._show(text, anchor_global)

    def show_pinned(self, card_id: str, text: str, anchor_global: QRect,
                    badge=None) -> None:
        """点击徽章固定展示：鼠标移开不自动关闭，直至再次点击该徽章收起"""
        self._pin_card_id = card_id
        self._anchor_widget = badge
        self._show(text, anchor_global)

    def is_pinned(self) -> bool:
        """浮层是否处于固定展示状态"""
        return self._pin_card_id is not None

    def pinned_for(self, card_id: str) -> bool:
        return self._pin_card_id == card_id

    def _show(self, text: str, anchor_global: QRect) -> None:
        """展示备注全文（text 为空则隐藏）"""
        if not text.strip():
            self._pin_card_id = None
            self._stop_watch()
            self.hide()
            return
        hint = self._pin_card_id is None
        # 内容、锚点、提示行均未变（如同一徽章反复进出）→ 跳过样式/排版重建
        same = (self.isVisible()
                and self._body.text() == text
                and self._recent_anchor == anchor_global
                and self._recent_hint == hint)
        was_visible = self.isVisible()
        if not same:
            # 样式只依赖主题（主题回调已下发），内容/锚点变化不再
            # reapply_style——多张带备注卡间快速划过时反复 reparse 纯浪费
            self._body.setText(text)
            self._hint.setVisible(hint)
            # 按最长行估算自然宽度并封顶，保证多行/长文本折行且短文本不拉宽
            fm = self._body.fontMetrics()
            natural = max((fm.horizontalAdvance(line)
                           for line in text.splitlines()), default=0) + 8
            body_w = max(120, min(natural, self._MAX_WIDTH))
            self._body.setFixedWidth(body_w)
            # 正文区高度：折行后的自然高，超过上限则内部滚动
            body_natural = min(self._body_height_for(body_w), _MAX_BODY_H)
            self._body_scroll.setFixedHeight(body_natural)
            # 版式尺寸全部显式算，不读面板 sizeHint：布局缓存会滞留在上一次
            # 的尺寸（连续展示"长备注→短备注"时量到长备注的高度），按它定位
            # 会压住徽章。全新窗口在 show() 前也不跑布局，子控件 live 几何
            # 同样无效（实测面板读到 624x464），故按各控件 sizeHint 求和
            lay = self._panel.layout()
            m = lay.contentsMargins()
            spacing = lay.spacing()
            frame = self._panel.frameWidth()
            title_h = self._title.sizeHint().height()
            hint_h = self._hint.sizeHint().height() if hint else 0
            chrome = (m.top() + m.bottom() + 2 * frame + title_h
                      + spacing + (spacing + hint_h if hint else 0))
            body_h = self._fit_body_height(anchor_global, chrome, body_natural)
            if body_h != body_natural:
                self._body_scroll.setFixedHeight(body_h)
            # 正文需要在浮层内滚动时，给竖向滚动条留出宽度，
            # 否则它压掉每行右缘十来个像素
            bar_w = (self._body_scroll.verticalScrollBar().sizeHint().width()
                     if body_h < body_natural else 0)
            panel_w = (m.left() + m.right() + 2 * frame
                       + max(body_w + bar_w, self._title.sizeHint().width(),
                             self._hint.sizeHint().width() if hint else 0))
            panel_h = chrome + body_h
            x, y = self._place(anchor_global, panel_w, panel_h)
            self._panel.setGeometry(_SHADOW, _SHADOW, panel_w, panel_h)
            # 尺寸用固定值锁定，不用 resize：顶层窗口的最小尺寸会被上一版
            # 布局缓存在 300ms 级的时间内拖住（实测"长备注→短备注"时窗口
            # 不肯缩回去），结果"渲染出的高度"大于"定位时用的高度"——浮层
            # 顶部按 6px 间隙摆好、却向下多长出一截压住徽章与下一张卡
            self.setFixedSize(panel_w + _SHADOW * 2, panel_h + _SHADOW * 2)
            self.move(x - _SHADOW, y - _SHADOW)
            self._recent_anchor = anchor_global
            self._recent_hint = hint
        self._hide_timer.stop()
        self.show()
        self.raise_()
        # 淡入只在"从无到有"时播放；锚点变化等重定位不重播，避免闪烁
        if not was_visible:
            motion.fade_in(self, AppConfig.POPOVER_ANIM_MS)
        self._start_watch()

    def _body_height_for(self, width: int) -> int:
        """正文按给定宽度折行后的自然高度（heightForWidth 不可用时退回 sizeHint）"""
        h = self._body.heightForWidth(width)
        if h <= 0:
            h = self._body.sizeHint().height()
        return max(h + 2, 18)

    def schedule_hide(self) -> None:
        """徽章/浮层 leave：延迟关闭（固定展示中不自动关闭），
        鼠标快速移入目标时可被取消"""
        if self.is_pinned():
            return
        self._hide_timer.start()

    def _do_hide(self) -> None:
        if self._cursor_in_scope():
            self._hide_timer.start()   # 光标仍在浮层/徽章上：稍后再试
            return
        self._stop_watch()
        self.hide()

    def hide_now(self) -> None:
        """立即隐藏（并取消延迟计时与固定状态）"""
        self._pin_card_id = None
        self._hide_timer.stop()
        self._stop_watch()
        self.hide()

    def cancel_pending_hide(self) -> None:
        """鼠标进入浮层/徽章时调用：取消将要发生的延迟关闭"""
        self._hide_timer.stop()

    def enterEvent(self, event) -> None:
        self.cancel_pending_hide()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        # 移回徽章时徽章 enter 会重新显示并停表；移向别处则延迟隐藏
        self.schedule_hide()
        super().leaveEvent(event)

    # ── 悬停巡检（漏投递 Leave 的自愈收口）────────────────

    def _start_watch(self) -> None:
        """悬停模式启动巡检；固定展示本就不自动关闭，无须巡检"""
        if not self.is_pinned():
            self._watch.start()

    def _stop_watch(self) -> None:
        self._watch.stop()

    def _revalidate_scope(self) -> None:
        """按光标实际位置复核：既不在浮层也不在锚点徽章上就收起

        列表重建（徽章控件被销毁，Leave 无从投递）、看板滚动、拖拽、
        切到别的应用等路径都可能让"移开"这件事收不到事件；巡检把这些
        残留统一收口，失败方向固定为"藏起来"——下次真实悬停自会再显示。
        按住鼠标键时不收：用户可能正在框选正文，光标划出浮层是选择动作的
        一部分；松手后下一次巡检照常收口。
        """
        from PySide6.QtWidgets import QApplication
        if not self.isVisible() or self.is_pinned():
            return
        if QApplication.mouseButtons() != Qt.NoButton:
            return
        if self._cursor_in_scope():
            return
        self._hide_timer.stop()
        self._stop_watch()
        self.hide()

    def _cursor_pos(self) -> QPoint:
        """当前光标位置（屏幕坐标）；单列出来便于测试注入"""
        return QCursor.pos()

    def _cursor_in_scope(self, pos=None) -> bool:
        """光标是否仍在浮层窗口或锚点徽章上（浮层含自绘阴影的命中余量）"""
        if not self.isVisible():
            return False
        if pos is None:
            pos = self._cursor_pos()
        if self.geometry().contains(pos):
            return True
        anchor = self._anchor_rect()
        return anchor is not None and anchor.contains(pos)

    def _anchor_rect(self) -> "QRect | None":
        """锚点徽章的实时矩形（屏幕坐标）；控件缺失/已销毁退回登记矩形

        退回登记矩形是有意的：徽章因重建（内容变化、换语言/主题）被销毁而
        光标仍压在原处时，浮层不该闪掉。真正的"光标已离开"由巡检按位置判。
        """
        badge = self._anchor_widget
        if badge is not None and is_valid(badge):
            return QRect(badge.mapToGlobal(QPoint(0, 0)), badge.size())
        return self._recent_anchor

    def anchored_to(self, badge) -> bool:
        """是否正以该控件为锚点展示（悬停/固定都算）"""
        return self.isVisible() and badge is not None \
            and self._anchor_widget is badge

    # ── 几何口径 ──────────────────────────────────────────

    def panel_geometry_global(self) -> "QRect":
        """可见面板矩形（屏幕坐标）：不含自绘阴影的外扩边距

        定位与测试都以面板为准，避免阴影边距渗进几何断言。
        """
        return QRect(self.mapToGlobal(self._panel.pos()), self._panel.size())

    # ── 定位 ──────────────────────────────────────────────

    @staticmethod
    def _available(anchor: "QRect") -> "QRect":
        """锚点所在屏幕的可用区域（避开任务栏）"""
        from PySide6.QtWidgets import QApplication
        screen = QApplication.screenAt(anchor.center())
        if screen is None:
            screen = QApplication.primaryScreen()
        return screen.availableGeometry()

    def _fit_body_height(self, anchor: "QRect", chrome: int,
                         natural_body: int) -> int:
        """按锚点上方可用空间决定正文高度（上方优先，不够就压缩而非翻转）

        徽章位于卡片底部信息行，朝下弹必然压住下一张卡。长备注（进度流水）
        面板可高 370px，比看板上半部多数徽章的"上方余量"还高，于是老判据
        （上方放不下就翻转）几乎必然触发——用户看到的就是"还是压在下一张
        卡上"。改为：上方不够就压缩正文高度、超出部分在浮层内滚动，
        只有连最小正文（约两行）都放不下（徽章贴着屏幕顶）才翻到下方。
        """
        avail = self._available(anchor)
        room_above = anchor.top() - _GAP - avail.top()
        if room_above - chrome >= _MIN_BODY_H:
            return min(natural_body, room_above - chrome)
        room_below = avail.bottom() - anchor.bottom() - _GAP
        return max(min(natural_body, room_below - chrome), _MIN_BODY_H)

    def _place(self, anchor: "QRect", panel_w: int,
               panel_h: int) -> tuple[int, int]:
        """面板左上角落点（屏幕坐标）：上方优先，越界翻转/夹紧"""
        avail = self._available(anchor)
        y = anchor.top() - _GAP - panel_h
        if y < avail.top():
            y = anchor.bottom() + _GAP        # 上方连最小正文都放不下
            if y + panel_h > avail.bottom():
                y = max(avail.top(), avail.bottom() - panel_h)   # 贴底
        x = anchor.left()
        if x + panel_w > avail.right():
            x = avail.right() - panel_w
        return max(avail.left(), x), y


_popover: NotesPopover | None = None


def notes_popover() -> NotesPopover:
    """进程级单例悬浮卡片"""
    global _popover
    if _popover is None:
        _popover = NotesPopover()
    return _popover


def hide_notes_popover() -> None:
    """立即隐藏（应用退出等场景用）"""
    if _popover is not None:
        _popover.hide_now()


def notes_pinned_for(card_id: str) -> bool:
    """浮层是否正固定于该卡片（只读判定，不触发单例创建）"""
    return _popover is not None and _popover.pinned_for(card_id)


def retexts_if_created() -> None:
    """语言切换：仅当浮层已创建时刷新（不因此创建单例）"""
    if _popover is not None:
        _popover.retexts()


def notes_popover_hovering() -> bool:
    """浮层存在且处于悬停模式（未固定）"""
    return _popover is not None and not _popover.is_pinned()


def notes_popover_anchored_to(badge) -> bool:
    """浮层是否正锚定在该徽章上（只读判定，不触发单例创建）"""
    return _popover is not None and _popover.anchored_to(badge)


# 供测试直接清理单例
def _reset_popover() -> None:
    global _popover
    if _popover is not None:
        _popover.deleteLater()
    _popover = None

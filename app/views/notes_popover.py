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
- 默认弹在徽章上方（盖住本卡下缘，不压住下一张卡）；仅上方放不下才翻转
- 软阴影在 paintEvent 手绘（静态，仅显隐/移动时重绘）；不用
  QGraphicsDropShadowEffect——motion.py 记录了其 8 倍于 opacity 的性能代价
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QPainter
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

from app.views.theme import AppTheme
from app.views import motion
from app.config import AppConfig

_HIDE_DELAY_MS = 160   # 徽章→浮层移动时的容忍延迟
_SHADOW = 8            # 自绘软阴影的向外扩散边距（窗口命中区随之略大）


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
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(_HIDE_DELAY_MS)
        self._hide_timer.timeout.connect(self._do_hide)
        self.setMouseTracking(True)
        self._pin_card_id: str | None = None   # 固定展示所属卡片 id（None=悬停模式）

        # 面板：承载底色/描边/圆角。外层自身保持透明，四周留 _SHADOW 画阴影
        self._panel = QFrame(self)
        self._panel.setObjectName("notesPopoverPanel")
        self._title = QLabel("备注")
        self._title.setObjectName("popTitle")
        self._body = QLabel()
        self._body.setObjectName("popBody")
        self._body.setWordWrap(True)
        self._body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._body.setMaximumWidth(self._MAX_WIDTH)
        # 徽章上原有一个原生 tooltip「悬停预览 · 点击固定」，悬停约 700ms 后
        # 会再弹一个系统提示窗压在浮层上；提示语移到这里，避免双层弹窗
        self._hint = QLabel("点击徽章固定")
        self._hint.setObjectName("popHint")

        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(12, 8, 12, 10)
        panel_layout.setSpacing(4)
        panel_layout.addWidget(self._title)
        panel_layout.addWidget(self._body)
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

    def show_for(self, text: str, anchor_global: QRect) -> None:
        """悬停展示备注全文（临时：移开后延迟自动关闭）"""
        self._pin_card_id = None
        self._show(text, anchor_global)

    def show_pinned(self, card_id: str, text: str,
                    anchor_global: QRect) -> None:
        """点击徽章固定展示：鼠标移开不自动关闭，直至再次点击该徽章收起"""
        self._pin_card_id = card_id
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
            self._body.setFixedWidth(max(120, min(natural, self._MAX_WIDTH)))
            self._recent_anchor = anchor_global
            self._recent_hint = hint
            # 全新窗口在 show() 前不跑布局，子控件 live 几何是无效值
            # （实测面板读到 624x464，按它定位会把浮层摆错位置），
            # 故按 sizeHint 显式固定面板与窗口尺寸后再定位
            hint_size = self._panel.sizeHint()
            self._panel.setGeometry(_SHADOW, _SHADOW,
                                    hint_size.width(), hint_size.height())
            self.resize(hint_size.width() + _SHADOW * 2,
                        hint_size.height() + _SHADOW * 2)
            self._place_near(anchor_global)
        self._hide_timer.stop()
        self.show()
        self.raise_()
        # 淡入只在"从无到有"时播放；锚点变化等重定位不重播，避免闪烁
        if not was_visible:
            motion.fade_in(self, AppConfig.POPOVER_ANIM_MS)

    def schedule_hide(self) -> None:
        """徽章/浮层 leave：延迟关闭（固定展示中不自动关闭），
        鼠标快速移入目标时可被取消"""
        if self.is_pinned():
            return
        self._hide_timer.start()

    def _do_hide(self) -> None:
        if self.mouse_inside():
            self._hide_timer.start()   # 鼠标仍在本浮层上：稍后再试
            return
        self.hide()

    def hide_now(self) -> None:
        """立即隐藏（并取消延迟计时与固定状态）"""
        self._pin_card_id = None
        self._hide_timer.stop()
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

    def mouse_inside(self) -> bool:
        """鼠标是否仍在本浮层内（用于徽章 leave 时判断是否真隐藏）"""
        return self.isVisible() and self.geometry().contains(QCursor.pos())

    # ── 几何口径 ──────────────────────────────────────────

    def panel_geometry_global(self) -> "QRect":
        """可见面板矩形（屏幕坐标）：不含自绘阴影的外扩边距

        定位与测试都以面板为准，避免阴影边距渗进几何断言。
        """
        return QRect(self.mapToGlobal(self._panel.pos()), self._panel.size())

    # ── 定位 ──────────────────────────────────────────────

    def _place_near(self, anchor: "QRect") -> None:
        """锚点（徽章屏幕矩形）上方放置；越界自动翻转/回拉

        徽章位于卡片底部信息行，朝下弹必然压住下一张卡；改为朝上弹只盖住
        本卡下缘，鼠标仍在徽章上、正文也不与下方卡片叠字。
        占位用 _panel（不含阴影），窗口位置相应内缩 _SHADOW。
        """
        from PySide6.QtWidgets import QApplication
        screen = QApplication.screenAt(anchor.center())
        avail = (screen.availableGeometry()
                 if screen is not None
                 else QApplication.primaryScreen().availableGeometry())
        gap = 6
        w, h = self._panel.width(), self._panel.height()
        x = anchor.left()
        y = anchor.top() - gap - h
        if y < avail.top():
            # 上方放不下则放到徽章下方
            y = anchor.bottom() + gap
            if y + h > avail.bottom():
                y = max(avail.top(), avail.bottom() - h)   # 上下都紧张：贴顶
        if x + w > avail.right():
            x = avail.right() - w
        x = max(avail.left(), x)
        self.move(x - _SHADOW, y - _SHADOW)


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


def notes_popover_hovering() -> bool:
    """浮层存在且处于悬停模式（未固定）"""
    return _popover is not None and not _popover.is_pinned()


# 供测试直接清理单例
def _reset_popover() -> None:
    global _popover
    if _popover is not None:
        _popover.deleteLater()
    _popover = None

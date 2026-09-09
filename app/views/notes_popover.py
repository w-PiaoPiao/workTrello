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
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

from app.views.theme import AppTheme

_HIDE_DELAY_MS = 160   # 徽章→浮层移动时的容忍延迟


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
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(_HIDE_DELAY_MS)
        self._hide_timer.timeout.connect(self._do_hide)
        self.setMouseTracking(True)
        self._pin_card_id: str | None = None   # 固定展示所属卡片 id（None=悬停模式）

        self._title = QLabel("备注")
        self._title.setObjectName("popTitle")
        self._body = QLabel()
        self._body.setObjectName("popBody")
        self._body.setWordWrap(True)
        self._body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._body.setMaximumWidth(self._MAX_WIDTH)

        self.setObjectName("notesPopover")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 10)
        layout.setSpacing(4)
        layout.addWidget(self._title)
        layout.addWidget(self._body)
        self.reapply_style()
        # 主题切换实时刷新（浮层打开时也跟随）
        AppTheme.register(self._on_theme_changed)

    def _on_theme_changed(self) -> None:
        self.reapply_style()
        self.update()

    # ── 样式 ──────────────────────────────────────────────

    def reapply_style(self) -> None:
        c = AppTheme.colors()
        self.setStyleSheet(f"""
            QFrame#notesPopover {{
                background: {c['bg_panel']};
                border: 1px solid {c['border']};
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
        """)

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
        self.reapply_style()
        self._body.setText(text)
        # 按最长行估算自然宽度并封顶，保证多行/长文本折行且短文本不拉宽
        fm = self._body.fontMetrics()
        natural = max((fm.horizontalAdvance(line) for line in text.splitlines()),
                      default=0) + 8
        self._body.setFixedWidth(max(120, min(natural, self._MAX_WIDTH)))
        self._recent_anchor = anchor_global
        self.adjustSize()
        self._place_near(anchor_global)
        self._hide_timer.stop()
        self.show()
        self.raise_()

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

    # ── 定位 ──────────────────────────────────────────────

    def _place_near(self, anchor: "QRect") -> None:
        """锚点（徽章屏幕矩形）下方偏右放置；越界自动翻转/回拉"""
        from PySide6.QtWidgets import QApplication
        screen = QApplication.screenAt(anchor.center())
        avail = (screen.availableGeometry()
                 if screen is not None
                 else QApplication.primaryScreen().availableGeometry())
        gap = 4
        x = anchor.left()
        y = anchor.bottom() + gap
        if x + self.width() > avail.right():
            x = avail.right() - self.width()
        if y + self.height() > avail.bottom():
            # 下方放不下则放到徽章上方
            y = anchor.top() - gap - self.height()
            if y < avail.top():
                y = avail.top()
        x = max(avail.left(), x)
        self.move(x, y)


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

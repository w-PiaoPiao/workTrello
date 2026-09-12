"""
今日清单悬浮气泡：桌宠右键打开，不用展开看板即可概览并勾选今日待办

- 半透明圆角浮窗（Qt.Popup：点击外部自动关闭）
- 每行：勾选框 + 标题（点击标题打开编辑对话框）+ 截止徽章
- 完成勾选后该行即时移除；全部勾完显示空态
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.config import AppConfig
from app.models.board import BoardList, Card
from app.views import motion
from app.views.board_view import _CardCheckButton
from app.views.theme import AppTheme

_ROW_MIN_H = 42
_MAX_POP_H = 330


def _fmt_due_short(due: str) -> str:
    """截止日期简写：9/10 或 已逾期 9/1（气泡内行尾徽章）"""
    from datetime import date
    try:
        d = date.fromisoformat(due)
    except ValueError:
        return due
    delta = (d - date.today()).days
    if delta < 0:
        return f"已逾期 {d.month}/{d.day}"
    if delta == 0:
        return "今天截止"
    return f"{d.month}/{d.day}"


class _TitleLabel(QLabel):
    """可点击的标题标签（点击打开卡片编辑）"""

    clicked = Signal()

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class _PopRow(QFrame):
    """今日清单行：勾选框 + 标题 + 截止徽章（控件复用，数据可重绑）

    浮窗可见时每次数据变更都会 set_items（勾选今日卡正是在浮窗打开时
    操作的），此前全量销毁重建所有行 + 每行 3 次 setStyleSheet 是热路径；
    与 ListColumn.refresh_cards 同一策略按 card.id 复用行控件。
    """

    signal_card_done = Signal(str, str, bool)   # list_id, card_id, done=勾选完成
    signal_card_edit = Signal(str, str)   # list_id, card_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self._list_id = ""
        self._card_id = ""
        self.setObjectName("popRow")
        self.setMinimumHeight(_ROW_MIN_H)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(6)

        self._check = _CardCheckButton()
        self._check.clicked.connect(self._emit_done)
        lay.addWidget(self._check)

        self._title = _TitleLabel("")
        self._title.clicked.connect(self._emit_edit)
        lay.addWidget(self._title, 1)

        self._badge = QLabel()
        self._badge.hide()
        lay.addWidget(self._badge)
        self.reapply_theme()

    def _emit_done(self) -> None:
        self.signal_card_done.emit(self._list_id, self._card_id, True)

    def _emit_edit(self) -> None:
        self.signal_card_edit.emit(self._list_id, self._card_id)

    def update_row(self, lst: BoardList, card: Card) -> None:
        """重绑到另一张卡：换 id、标题与截止徽章，勾选态复位"""
        self._list_id = lst.id
        self._card_id = card.id
        self._check.set_done(False)
        self._title.setText(card.title)
        if card.due_date:
            self._badge.setText(_fmt_due_short(card.due_date))
            self._badge.show()
        else:
            self._badge.hide()

    def reapply_theme(self) -> None:
        """行配色快照随主题重下（内容相同也 re-polish，仅主题切换时调用）"""
        c = AppTheme.colors()
        self.setStyleSheet(f"""
            QFrame#popRow {{
                background: {c['bg_card']};
                border: 1px solid {c['border']};
                border-radius: 8px;
            }}
            QFrame#popRow:hover {{ border: 1px solid {c['accent']}; }}
        """)
        self._title.setStyleSheet(
            f"color: {c['text_primary']}; font-size: 12px; background: transparent;")
        self._badge.setStyleSheet(f"""
            QLabel {{
                color: {c['text_secondary']};
                font-size: 11px;
                background: {c['accent_soft']};
                border-radius: 6px;
                padding: 1px 6px;
            }}
        """)


class TodayPopover(QWidget):
    """今日待办浮窗（内容由 set_items 注入，勾选即从列表移除）"""

    signal_card_done = Signal(str, str, bool)   # list_id, card_id, done
    signal_card_edit = Signal(str, str)         # list_id, card_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedWidth(280)
        self._rows: list[tuple[str, str, QWidget]] = []   # (list_id, card_id, row)
        self._items: list[tuple[BoardList, Card]] = []    # 缓存供主题切换后重建
        self._built = False
        AppTheme.register(self._on_theme_changed)

    # ── 主题 ──────────────────────────────────────────────

    def _on_theme_changed(self) -> None:
        """主题切换：外壳与每行的配色都是构建时的快照，需重刷

        行控件不重建，只对现存的行重下样式（set_items 会复用行）。
        """
        if not self._built:
            return
        self._apply_style()
        for _lid, _cid, row in self._rows:
            row.reapply_theme()

    # ── 数据注入 ──────────────────────────────────────────

    def set_items(self, items: list[tuple[BoardList, Card]]) -> None:
        self._rebuild_row_widget()
        self._items = list(items)
        self._title_label.setText(f"今日待办 · {len(items)}")
        layout = self._rows_layout
        # 按 card.id 复用行控件（与 ListColumn.refresh_cards 同一策略）
        reusable = {card_id: row for _lid, card_id, row in self._rows}
        self._rows.clear()
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is None:
                continue
            w.setParent(None)
            if isinstance(w, _PopRow) and w not in reusable.values():
                w.deleteLater()   # 没有卡可再绑定的行才销毁

        if not items:
            empty = QLabel("今天没有待办 🎉")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(
                f"color: {AppTheme.colors()['text_disabled']};"
                " font-size: 12px; padding: 18px;")
            layout.addWidget(empty)
            self.setFixedHeight(110)
            return

        for lst, card in items:
            row = reusable.pop(card.id, None)
            if row is None:
                row = _PopRow()
                # 行信号直通浮窗信号（每行只连一次，随行复用）
                row.signal_card_done.connect(self.signal_card_done)
                row.signal_card_edit.connect(self.signal_card_edit)
            else:
                row.setParent(self)
            row.update_row(lst, card)
            layout.addWidget(row)
            self._rows.append((lst.id, card.id, row))
        layout.addStretch(1)
        h = min(_MAX_POP_H, 62 + _ROW_MIN_H * len(items))
        self.setFixedHeight(h)

    # ── 基础 UI ──────────────────────────────────────────

    def _rebuild_row_widget(self) -> None:
        if self._built:
            return
        self._built = True
        self.setStyleSheet("""
            QScrollArea { background: transparent; }
            QWidget#rowsHost { background: transparent; }
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QFrame(self)
        frame.setObjectName("popFrame")
        outer.addWidget(frame)

        root = QVBoxLayout(frame)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        head = QHBoxLayout()
        self._title_label = QLabel("今日待办")
        self._title_label.setStyleSheet(
            "font-size: 13px; font-weight: bold; background: transparent;")
        head.addWidget(self._title_label)
        head.addStretch(1)
        self._close_btn = QPushButton("✕")
        self._close_btn.setFixedSize(22, 22)
        self._close_btn.setCursor(Qt.PointingHandCursor)
        self._close_btn.clicked.connect(self.close)
        head.addWidget(self._close_btn)
        root.addLayout(head)

        self._frame = frame
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setStyleSheet("QScrollArea, QWidget#rowsHost"
                                   " { background: transparent; }")
        self._rows_host = QWidget()
        self._rows_host.setObjectName("rowsHost")
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(6)
        self._scroll.setWidget(self._rows_host)
        root.addWidget(self._scroll, 1)
        self._apply_style()

    def _apply_style(self) -> None:
        """外壳（面板 + 关闭钮）配色；行配色在 _make_row 里各自快照"""
        c = AppTheme.colors()
        self._frame.setStyleSheet(f"""
            QFrame#popFrame {{
                background: {c['bg_card']};
                border: 1px solid {c['border']};
                border-radius: 12px;
            }}
        """)
        self._close_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c['mask']};
                color: {c['text_primary']};
                border: none;
                border-radius: 11px;
                font-size: 10px;
            }}
            QPushButton:hover {{ background: {c['danger']}; color: white; }}
        """)

    def show_below(self, anchor: QRect) -> None:
        """在主窗口(anchor 全局矩形)上方/下方弹出，屏幕内夹紧"""
        from PySide6.QtWidgets import QApplication
        screen = QApplication.screenAt(anchor.center()) \
            or QApplication.primaryScreen()
        geo = screen.availableGeometry()
        y = anchor.top() - self.height() - 8
        if y < geo.top():
            y = anchor.bottom() + 8
        x = anchor.center().x() - self.width() // 2
        x = max(geo.left() + 6, min(x, geo.right() - self.width() - 6))
        self.move(QPoint(x, y))
        was_visible = self.isVisible()
        self.show()
        self.raise_()
        if not was_visible:
            motion.fade_in(self, AppConfig.POPOVER_ANIM_MS)

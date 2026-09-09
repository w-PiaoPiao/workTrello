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
        self._built = False

    # ── 数据注入 ──────────────────────────────────────────

    def set_items(self, items: list[tuple[BoardList, Card]]) -> None:
        self._rebuild_row_widget()
        layout = self._rows_layout
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._rows.clear()

        self._title_label.setText(f"今日待办 · {len(items)}")
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
            row = self._make_row(lst, card)
            layout.addWidget(row)
            self._rows.append((lst.id, card.id, row))
        layout.addStretch(1)
        h = min(_MAX_POP_H, 62 + _ROW_MIN_H * len(items))
        self.setFixedHeight(h)

    def _make_row(self, lst: BoardList, card: Card) -> QFrame:
        c = AppTheme.colors()
        row = QFrame()
        row.setObjectName("popRow")
        row.setMinimumHeight(_ROW_MIN_H)
        row.setStyleSheet(f"""
            QFrame#popRow {{
                background: {c['bg_card']};
                border: 1px solid {c['border']};
                border-radius: 8px;
            }}
            QFrame#popRow:hover {{ border: 1px solid {c['accent']}; }}
        """)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(6)

        check = QPushButton("☐")
        check.setFlat(True)
        check.setFixedSize(22, 22)
        check.setCursor(Qt.PointingHandCursor)
        check.setStyleSheet(
            "QPushButton { color: %s; font-size: 14px; padding: 0; }"
            % c["text_disabled"])
        check.clicked.connect(
            lambda: self.signal_card_done.emit(lst.id, card.id, True))
        lay.addWidget(check)

        title = _TitleLabel(card.title)
        title.setStyleSheet(
            f"color: {c['text_primary']}; font-size: 12px; background: transparent;")
        title.clicked.connect(
            lambda: self.signal_card_edit.emit(lst.id, card.id))
        lay.addWidget(title, 1)

        if card.due_date:
            badge = QLabel(_fmt_due_short(card.due_date))
            badge.setStyleSheet(f"""
                QLabel {{
                    color: {c['text_secondary']};
                    font-size: 10px;
                    background: {c['accent_soft']};
                    border-radius: 4px;
                    padding: 1px 5px;
                }}
            """)
            lay.addWidget(badge)
        return row

    # ── 勾选联动：行即时移除 ──────────────────────────────

    def remove_row_for(self, card_id: str) -> None:
        """某卡已完成/被删后移除其行（剩余为空则显示空态）"""
        remain = []
        for lst_id, cid, row in self._rows:
            if cid == card_id:
                row.setParent(None)
                row.deleteLater()
            else:
                remain.append((lst_id, cid, row))
        self._rows = remain
        if not remain:
            empty = QLabel("今天全部搞定 🎉")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(
                f"color: {AppTheme.colors()['success']};"
                " font-size: 12px; padding: 18px;")
            self._rows_layout.addWidget(empty)
        self._title_label.setText(f"今日待办 · {len(remain)}")
        h = min(_MAX_POP_H, 62 + _ROW_MIN_H * max(1, len(remain)))
        self.setFixedHeight(h)

    # ── 基础 UI ──────────────────────────────────────────

    def _rebuild_row_widget(self) -> None:
        if self._built:
            return
        self._built = True
        c = AppTheme.colors()
        self.setStyleSheet("""
            QScrollArea { background: transparent; }
            QWidget#rowsHost { background: transparent; }
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QFrame(self)
        frame.setObjectName("popFrame")
        frame.setStyleSheet(f"""
            QFrame#popFrame {{
                background: {c['bg_card']};
                border: 1px solid {c['border']};
                border-radius: 12px;
            }}
        """)
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
        close = QPushButton("✕")
        close.setFixedSize(22, 22)
        close.setCursor(Qt.PointingHandCursor)
        close.setStyleSheet(f"""
            QPushButton {{
                background: rgba(128, 128, 128, 0.2);
                color: {c['text_primary']};
                border: none;
                border-radius: 11px;
                font-size: 10px;
            }}
            QPushButton:hover {{ background: {c['danger']}; color: white; }}
        """)
        close.clicked.connect(self.close)
        head.addWidget(close)
        root.addLayout(head)

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
        self.show()
        self.raise_()

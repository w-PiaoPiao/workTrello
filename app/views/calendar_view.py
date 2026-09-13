"""日历视图：按截止日期在月历中查看/拖动卡片

- 月历网格（周一起始），有截止日期的未归档卡片以小条目（chip）呈现
- 点击条目 → 打开卡片编辑；拖动条目到另一天 → 改截止日期
- 也接受从看板拖来的卡片（同 MIME），拖放即改期
- 数据由控制器注入 set_month(y, m, due_map)；翻月时发 month_changed
  请求控制器重取（视图不自持数据，避免与编辑对话框双写）
"""

from __future__ import annotations

import calendar as _cal
from datetime import date

from PySide6.QtCore import (
    QMimeData,
    QPoint,
    Qt,
    Signal,
)
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.i18n import tr
from app.models.board import Card
from app.views.board_view import MIME_CARD
from app.views.theme import AppTheme

_MAX_CHIPS_PER_CELL = 5

_WEEKDAY_HEADERS = ("一", "二", "三", "四", "五", "六", "日")


def _fmt_month(y: int, m: int) -> str:
    return tr("{year} 年 {month} 月").format(year=y, month=m)


class _Chip(QLabel):
    """日历条目：可点击（打开编辑）可拖拽（改期）"""

    signal_edit = Signal(str)      # card_id

    def __init__(self, card: Card, parent=None):
        super().__init__(parent)
        self._card_id = card.id
        self._done = card.done
        title = card.title
        fm = self.fontMetrics()
        self.setText(fm.elidedText(title, Qt.ElideRight, 96))
        self.setToolTip(f"{title} · {card.due_date}")
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("calChip")
        self._apply_style()

    def card_id(self) -> str:
        return self._card_id

    def _apply_style(self) -> None:
        c = AppTheme.colors()
        if self._done:
            color, bg = c["text_disabled"], c["mask"]
        else:
            color, bg = c["text_primary"], c["bg_card"]
        self.setStyleSheet(f"""
            QLabel#calChip {{
                color: {color};
                background: {bg};
                border: 1px solid {c['border']};
                border-radius: 5px;
                font-size: 10px;
                padding: 1px 5px;
            }}
            QLabel#calChip:hover {{ border: 1px solid {c['accent']}; }}
        """)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (event.buttons() & Qt.LeftButton
                and hasattr(self, "_press_pos")
                and (event.position().toPoint() - self._press_pos)
                .manhattanLength() > 8):
            mime = QMimeData()
            mime.setData(MIME_CARD, self._card_id.encode("utf-8"))
            drag = QDrag(self)
            drag.setMimeData(mime)
            drag.exec(Qt.MoveAction)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if (event.button() == Qt.LeftButton
                and hasattr(self, "_press_pos")
                and (event.position().toPoint() - self._press_pos)
                .manhattanLength() <= 8):
            self.signal_edit.emit(self._card_id)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _DayCell(QFrame):
    """日期格：日期号 + 卡片条目列表；接受拖放改期"""

    signal_drop_card = Signal(str, str)   # card_id, iso_date
    signal_edit_card = Signal(str)        # card_id

    def __init__(self, day: int, in_month: bool, is_today: bool,
                 iso: str, parent=None):
        super().__init__(parent)
        self._iso = iso
        self.setAcceptDrops(True)
        self.setObjectName("dayCell")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(3, 2, 3, 2)
        lay.setSpacing(2)
        head = QLabel(str(day))
        head.setObjectName("dayNum")
        head.setAlignment(Qt.AlignRight)
        lay.addWidget(head)
        self._head = head
        self._chips_host = QWidget()
        self._chips_layout = QVBoxLayout(self._chips_host)
        self._chips_layout.setContentsMargins(0, 0, 0, 0)
        self._chips_layout.setSpacing(2)
        self._chips_layout.addStretch(1)
        lay.addWidget(self._chips_host, 1)
        self._in_month = in_month
        self._is_today = is_today
        self._chip_widgets: list[QWidget] = []
        self._apply_style()

    def iso(self) -> str:
        return self._iso

    def _apply_style(self) -> None:
        c = AppTheme.colors()
        border = c["accent"] if self._is_today else c["border"]
        width = 2 if self._is_today else 1
        bg = "transparent" if self._in_month else c["mask"]
        num_color = (c["accent"] if self._is_today
                     else c["text_primary"] if self._in_month
                     else c["text_disabled"])
        self.setStyleSheet(f"""
            QFrame#dayCell {{
                background: {bg};
                border: {width}px solid {border};
                border-radius: 8px;
            }}
            QLabel#dayNum {{
                color: {num_color};
                font-size: 11px;
                font-weight: {'bold' if self._is_today else 'normal'};
                background: transparent;
            }}
        """)

    def set_cards(self, cards: list[Card]) -> None:
        while self._chips_layout.count():
            item = self._chips_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._chip_widgets = []
        for i, card in enumerate(cards[:_MAX_CHIPS_PER_CELL]):
            chip = _Chip(card)
            chip.signal_edit.connect(self.signal_edit_card)
            self._chips_layout.insertWidget(self._chips_layout.count() - 1,
                                            chip)
            self._chip_widgets.append(chip)
        rest = len(cards) - _MAX_CHIPS_PER_CELL
        if rest > 0:
            more = QLabel(tr("还有 {n} 项…").format(n=rest))
            more.setObjectName("dayMore")
            more.setStyleSheet(
                f"color: {AppTheme.colors()['text_secondary']};"
                " font-size: 9px; background: transparent;")
            self._chips_layout.insertWidget(
                self._chips_layout.count() - 1, more)

    # ── 拖放改期 ──────────────────────────────────────────

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_CARD) and self._iso:
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_CARD) and self._iso:
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_CARD) and self._iso:
            card_id = bytes(event.mimeData().data(MIME_CARD)).decode("utf-8")
            self.signal_drop_card.emit(card_id, self._iso)
            event.acceptProposedAction()

    def enterEvent(self, event) -> None:
        self._head.setStyleSheet(
            f"color: {AppTheme.colors()['accent']}; font-size: 11px;"
            " background: transparent;")
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._apply_style()
        super().leaveEvent(event)


class CalendarDialog(QDialog):
    """日历视图对话框（非模态；数据经 set_month 注入）"""

    signal_month_changed = Signal(int, int)          # year, month
    signal_due_change = Signal(str, str)             # card_id, iso_date
    signal_edit_requested = Signal(str)              # card_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("日历视图"))
        self.setModal(False)
        self.setMinimumSize(760, 540)
        self._year = date.today().year
        self._month = date.today().month
        self._cells: list[_DayCell] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        head = QHBoxLayout()
        self._prev_btn = QPushButton("◀")
        self._prev_btn.setFixedWidth(36)
        self._prev_btn.setCursor(Qt.PointingHandCursor)
        self._prev_btn.clicked.connect(lambda: self._shift_month(-1))
        head.addWidget(self._prev_btn)
        self._month_label = QLabel()
        self._month_label.setObjectName("calMonth")
        head.addWidget(self._month_label, 1, Qt.AlignCenter)
        self._next_btn = QPushButton("▶")
        self._next_btn.setFixedWidth(36)
        self._next_btn.setCursor(Qt.PointingHandCursor)
        self._next_btn.clicked.connect(lambda: self._shift_month(1))
        head.addWidget(self._next_btn)
        self._today_btn = QPushButton(tr("回到今天"))
        self._today_btn.setCursor(Qt.PointingHandCursor)
        self._today_btn.clicked.connect(self._go_today)
        head.addWidget(self._today_btn)
        self._close_btn = QPushButton(tr("关闭"))
        self._close_btn.setCursor(Qt.PointingHandCursor)
        self._close_btn.clicked.connect(self.close)
        head.addWidget(self._close_btn)
        root.addLayout(head)

        grid_head = QGridLayout()
        for i, name in enumerate(_WEEKDAY_HEADERS):
            lab = QLabel(name)
            lab.setAlignment(Qt.AlignCenter)
            lab.setObjectName("weekdayHead")
            grid_head.addWidget(lab, 0, i)
        grid_host = QWidget()
        grid_host.setLayout(grid_head)
        root.addWidget(grid_host)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(4)
        self._scroll.setWidget(self._grid_host)
        root.addWidget(self._scroll, 1)

        self.reapply_theme()
        AppTheme.register(self._on_theme_changed)

    # ── 导航 ──────────────────────────────────────────────

    def _shift_month(self, delta: int) -> None:
        m = self._month + delta
        y = self._year
        if m < 1:
            y, m = y - 1, 12
        elif m > 12:
            y, m = y + 1, 1
        self._year, self._month = y, m
        self.signal_month_changed.emit(y, m)

    def _go_today(self) -> None:
        t = date.today()
        self._year, self._month = t.year, t.month
        self.signal_month_changed.emit(t.year, t.month)

    # ── 数据注入 ──────────────────────────────────────────

    def set_month(self, year: int, month: int,
                  due_map: dict[str, list[Card]]) -> None:
        """重建月历网格（due_map: ISO 日期 → 该日卡片）"""
        self._year, self._month = year, month
        self._month_label.setText(_fmt_month(year, month))
        today = date.today()
        # 清空旧格
        for cell in self._cells:
            cell.setParent(None)
            cell.deleteLater()
        self._cells = []
        first = date(year, month, 1)
        # 周一起始偏移（0=周一）
        offset = first.weekday()
        days_in_month = _cal.monthrange(year, month)[1]
        prev_days = _cal.monthrange(
            year if month > 1 else year - 1,
            month - 1 if month > 1 else 12)[1]
        rows = (offset + days_in_month + 6) // 7
        for i in range(rows * 7):
            day_num = i - offset + 1
            r, c = divmod(i, 7)
            if day_num < 1:
                # 上月补位（仅显示，无落点）
                cell = _DayCell(prev_days + day_num, in_month=False,
                                is_today=False, iso="")
            elif day_num > days_in_month:
                # 下月补位
                cell = _DayCell(day_num - days_in_month, in_month=False,
                                is_today=False, iso="")
            else:
                d = date(year, month, day_num)
                iso = d.isoformat()
                cell = _DayCell(day_num, in_month=True,
                                is_today=d == today, iso=iso)
                cell.set_cards(due_map.get(iso, []))
            cell.signal_drop_card.connect(self.signal_due_change)
            cell.signal_edit_card.connect(self.signal_edit_requested)
            self._grid.addWidget(cell, r, c)
            self._cells.append(cell)
        for c in range(7):
            self._grid.setColumnStretch(c, 1)
        for r in range(rows):
            self._grid.setRowStretch(r, 1)

    # ── 主题 ──────────────────────────────────────────────

    def _on_theme_changed(self) -> None:
        self.reapply_theme()

    def reapply_theme(self) -> None:
        c = AppTheme.colors()
        self.setStyleSheet(f"""
            QDialog {{ background: {c['bg_primary']}; }}
            QLabel#calMonth {{
                color: {c['text_primary']};
                font-size: 15px;
                font-weight: bold;
                background: transparent;
            }}
            QLabel#weekdayHead {{
                color: {c['text_secondary']};
                font-size: 11px;
                background: transparent;
            }}
            QLabel#dayMore {{
                color: {c['text_secondary']};
                font-size: 9px;
                background: transparent;
            }}
            QPushButton {{
                background: {c['bg_card']};
                color: {c['text_primary']};
                border: 1px solid {c['border']};
                border-radius: 8px;
                padding: 4px 12px;
            }}
            QPushButton:hover {{ background: {c['bg_hover']}; }}
        """)
        for cell in self._cells:
            cell._apply_style()
            for w in cell._chip_widgets:
                if isinstance(w, _Chip):
                    w._apply_style()

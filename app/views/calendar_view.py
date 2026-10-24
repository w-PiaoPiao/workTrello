"""日历视图：按截止日期在月历中查看/拖动卡片

- 月历网格（周一起始），有截止日期的未归档卡片以小条目（chip）呈现
- 点击条目 → 打开卡片编辑；拖动条目到另一天 → 改截止日期
- 也接受从看板拖来的卡片（同 MIME），拖放即改期
- 数据由控制器注入 set_month(y, m, due_map)；翻月时发 month_changed
  请求控制器重取（视图不自持数据，避免与编辑对话框双写）
"""

from __future__ import annotations

import calendar as _cal
from datetime import date, timedelta

from PySide6.QtCore import (
    QMimeData,
    QPoint,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.config import AppConfig
from app.i18n import tr, weekday_short
from app.models.board import Card
from app.views import icons, tokens
from app.views.board_view import MIME_CARD
from app.views.theme import AppTheme

# 每格最多渲染的条目数（含"还有 N 项…"按钮共 4 行，正好塞进最小行高；
# 多出的走按钮菜单——此前是死标签，被折叠的卡片完全不可达）
_MAX_CHIPS_PER_CELL = 3
# 条目/折叠入口的标称高度（与 #calChip / #dayMore 的 QSS 对齐，用于按行高
# 估算条目预算；装完还会用布局 sizeHint 实测复核，见 _DayCell._content_fits）
_CHIP_NOMINAL_H = 20
_MORE_NOMINAL_H = 26     # #dayMore：min-height 20 + 上下 padding 3px×2

# 方向键 → 焦点在 7 列网格里的位移
_ARROW_STEPS = {Qt.Key_Left: -1, Qt.Key_Right: 1,
                Qt.Key_Up: -7, Qt.Key_Down: 7}

# 行高按视口推导（见 CalendarDialog._fit_row_height）：写死 108 时 6 行 =
# 668px 塞进约 386px 的视口，月历一打开就带纵向滚动条、首屏只见 3.6 行。
# 理想行高（日期号 + 3 条 chip + 「还有 N 项…」全放得下）不写死像素：
# 字体度量与全局 QSS 都会影响它（实测"全局样式表已下发"后同一份内容高
# 2px），写死的那一版会把最后一条 chip 无谓折叠掉。改为 `_DayCell.
# natural_height()` 用探针实测，这里的常量只作空列表时的兜底。
# 52 是压缩下限：日期号 + 「还有 N 项…」（26px）+ 内边距共约 45px，再矮
# 那个按钮就会被裁掉——它是触达其余卡片的唯一入口。下限不取 62 是因为
# 6 行月份 × 62 + 行距 20 = 392px 已经超过 386px 的视口，月历又会带回
# 滚动条；52 × 6 + 20 = 332px，6 行月份同样完整可见。
_CELL_IDEAL_H = 112
_CELL_MIN_H = 52

_WEEKDAY_COUNT = 7


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
        # 键盘可达：条目可聚焦（Tab 由 CalendarDialog.focusNextPrevChild
        # 送出网格），Enter/Space 打开编辑
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(title)
        self._apply_style()

    def card_id(self) -> str:
        return self._card_id

    def keyPressEvent(self, event) -> None:
        """Enter/Space 打开编辑（Tab 由 CalendarDialog.focusNextPrevChild 接管）"""
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.signal_edit.emit(self._card_id)
            event.accept()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._apply_style()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._apply_style()

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
                border-radius: {tokens.radius('chip')}px;
                {tokens.font_css(11)};
                {tokens.pad(1, 5)};
            }}
            QLabel#calChip:hover {{ border: 1px solid {c['accent']}; }}
            QLabel#calChip:focus {{ border: 1px solid {c['focus_ring']}; }}
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
    """日期格：日期号 + 卡片条目列表；接受拖放改期

    上下月补位格同样是**真实日期**（只是配色区分"不在本月"）：它们既渲染
    当天条目、也接受拖放，还是"拖拽中自动翻月"的入口——拖到灰色的上月
    格子并停一下，日历会翻到上个月。
    """

    signal_drop_card = Signal(str, str)    # card_id, iso_date
    signal_edit_card = Signal(str)         # card_id
    signal_create_card = Signal(str)       # iso_date（双击空白格新建）
    signal_drag_hover = Signal(int)        # 拖拽悬停的月份偏移（-1/0/+1）
    signal_drag_leave = Signal()
    signal_move_focus = Signal(str, int)   # iso, 方向键（网格内移动焦点）

    def __init__(self, day: int, in_month: bool, is_today: bool,
                 iso: str, month_offset: int = 0, parent=None):
        super().__init__(parent)
        self._iso = iso
        self._month_offset = month_offset
        self.setAcceptDrops(True)
        self.setObjectName("dayCell")
        # 竖直方向忽略 sizeHint：月历所有行等高（此前某天卡片多就把整行
        # 撑高，网格看着像渲染错乱），行高交给 grid 的 rowStretch 均分
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Ignored)
        # 行高：None = 尚未由对话框推导，按内容自然高度走（独立格/测试）
        self._row_h: int | None = None
        self._natural_h: int | None = None
        self._unit_h: tuple[int, int] | None = None
        self.setMinimumHeight(_CELL_IDEAL_H)
        # 键盘可达：格子进 tab 链（Tab 由 CalendarDialog.focusNextPrevChild
        # 直接送出网格），方向键在日期间移动焦点
        self.setFocusPolicy(Qt.StrongFocus)
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
        self._more_btn: QPushButton | None = None
        self._all_cards: list[Card] = []
        self._apply_style()

    def iso(self) -> str:
        return self._iso

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._apply_style()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._apply_style()

    def _apply_style(self, hover: bool = False, drop: bool = False) -> None:
        c = AppTheme.colors()
        # 焦点优先于"今日/落点"描边：键盘用户得先看清焦点在哪一格
        if self.hasFocus():
            border, width = c["focus_ring"], 2
        else:
            border = c["accent"] if (self._is_today or drop) else c["border"]
            width = 2 if (self._is_today or drop) else 1
        bg = ("transparent" if self._in_month else c["mask"])
        if drop:
            bg = c["accent_soft"]   # 拖拽落点：整格点亮，与卡片插入线同一套语言
        num_color = (c["accent"] if self._is_today
                     else c["text_primary"] if self._in_month
                     else c["text_disabled"])
        self.setStyleSheet(f"""
            QFrame#dayCell {{
                background: {bg};
                border: {width}px solid {border};
                border-radius: {tokens.radius('control')}px;
            }}
            QLabel#dayNum {{
                color: {c['accent'] if hover else num_color};
                {tokens.font_css(11)};
                font-weight: {'bold' if self._is_today else 'normal'};
                background: transparent;
            }}
            QPushButton#dayMore {{
                color: {c['text_secondary']};
                {tokens.font_css(11)};
                text-align: left;
                background: transparent;
                border: none;
                /* 命中区 ≥20 高（原 ~14px）：它是触达当天第 4 张以后卡片的
                   唯一入口，太矮既难点也难瞄 */
                {tokens.pad(3, 4)};
                min-height: 20px;
            }}
            QPushButton#dayMore:hover {{ color: {c['accent']}; }}
        """)

    def set_cards(self, cards: list[Card]) -> None:
        """重建当天的条目（顺序即传入顺序）

        注意清理循环会把 __init__ 里加的末尾弹性项一并摘掉，所以这里
        按序 addWidget 并在最后补回弹性——此前用 count()-1 定位插入点，
        弹性项消失后每个新条目都被插到倒数第二位，**日历条目顺序与
        数据顺序不一致**（第 4 条会跑到第 3 条前面）。
        """
        while self._chips_layout.count():
            item = self._chips_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._chip_widgets = []
        self._more_btn = None
        self._all_cards = list(cards)
        for card in cards[:self._chip_budget()]:
            chip = _Chip(card)
            chip.signal_edit.connect(self.signal_edit_card)
            self._chips_layout.addWidget(chip)
            self._chip_widgets.append(chip)
        rest = len(cards) - len(self._chip_widgets)
        if rest > 0:
            # 此前是死标签：被折叠的卡片既看不到也点不开，只能逐张翻
            # 编辑框去找。改成按钮，点开列出当天全部卡片
            more = QPushButton(tr("还有 {n} 项…").format(n=rest))
            more.setObjectName("dayMore")
            more.setCursor(Qt.PointingHandCursor)
            more.setToolTip(tr("点击查看当天全部卡片"))
            more.clicked.connect(self._show_all_cards)
            self._chips_layout.addWidget(more)
            self._more_btn = more
        self._chips_layout.addStretch(1)
        if self._row_h is None:
            # 独立格（未被对话框推导过行高）：以内容自然高度为准，
            # 保证"上限条 chip + 折叠入口"完整可见
            self._row_h = self._content_height()
            self.setMinimumHeight(self._row_h)
        # 预算按标称值估的，字体比预期大时会差几个像素：装完再用布局
        # 实测一次，超出就把最后一条 chip 折叠进「还有 N 项…」
        while self._chip_widgets and not self._content_fits():
            chip = self._chip_widgets.pop()
            self._chips_layout.removeWidget(chip)
            chip.setParent(None)
            chip.deleteLater()
            rest += 1
            if self._more_btn is None:
                more = QPushButton(tr("还有 {n} 项…").format(n=rest))
                more.setObjectName("dayMore")
                more.setCursor(Qt.PointingHandCursor)
                more.setToolTip(tr("点击查看当天全部卡片"))
                more.clicked.connect(self._show_all_cards)
                self._chips_layout.insertWidget(self._chips_layout.count() - 1,
                                                more)
                self._more_btn = more
            else:
                self._more_btn.setText(tr("还有 {n} 项…").format(n=rest))

    def set_row_height(self, height: int) -> None:
        """设定行高（由 CalendarDialog 按视口推导，见 _fit_row_height）

        行高变了要重算条目预算：矮行高下硬塞 3 条 chip 会把「还有 N 项…」
        挤出格子，而它是触达其余卡片的唯一入口。
        """
        height = max(_CELL_MIN_H, int(height))
        if height == self._row_h:
            return
        self._row_h = height
        self.setMinimumHeight(height)
        if self._all_cards:
            self.set_cards(self._all_cards)

    def _units(self) -> tuple[int, int]:
        """实测一条 chip / 折叠入口的高度（含 QSS 的 padding 与 min-height）

        字体度量与全局样式表都会影响这两个值，写死像素在"全局 QSS 已下发"
        时会差几像素；探针挂在本格下，量到的就是本格真实样式。
        """
        if self._unit_h is None:
            chip = _Chip(Card(title="测"), self._chips_host)
            more = QPushButton(tr("还有 {n} 项…").format(n=1), self._chips_host)
            more.setObjectName("dayMore")
            chip.hide()
            more.hide()
            chip.ensurePolished()
            more.ensurePolished()
            self._unit_h = (chip.sizeHint().height(), more.sizeHint().height())
            chip.deleteLater()
            more.deleteLater()
        return self._unit_h

    def natural_height(self) -> int:
        """理想行高：日期号 + 上限条 chip + 折叠入口全放得下所需的高度"""
        if self._natural_h is None:
            lay = self.layout()
            c = lay.contentsMargins()
            gap = self._chips_layout.spacing()
            chip_h, more_h = self._units()
            self._natural_h = (
                c.top() + c.bottom() + self._head.sizeHint().height()
                + lay.spacing()
                + _MAX_CHIPS_PER_CELL * chip_h + (_MAX_CHIPS_PER_CELL - 1) * gap
                + more_h + gap)
        return self._natural_h

    def invalidate_metrics(self) -> None:
        """样式表换过（主题切换）→ 探针与自然高度作废，下次重测"""
        self._natural_h = None
        self._unit_h = None

    def _row_height(self) -> int:
        """当前生效的行高：未推导过时取内容自然高度"""
        return self._row_h if self._row_h is not None else self.natural_height()

    def _chip_budget(self) -> int:
        """当前行高放得下几条 chip（始终给「还有 N 项…」留一行）

        先按标称值估，装完还会用布局实测复核（见 set_cards 尾部）。
        """
        row_h = self._row_height()
        if row_h >= self.natural_height():
            return _MAX_CHIPS_PER_CELL
        lay = self.layout()
        c = lay.contentsMargins()
        fixed = (c.top() + c.bottom() + self._head.sizeHint().height()
                 + lay.spacing())
        avail = row_h - fixed - _MORE_NOMINAL_H
        step = _CHIP_NOMINAL_H + self._chips_layout.spacing()
        # 可以是 0：极矮行高下先保「还有 N 项…」，chip 全折叠进去
        return max(0, min(_MAX_CHIPS_PER_CELL, (avail + step) // step))

    def _content_height(self) -> int:
        """日期号 + 现有条目（含折叠入口）实际需要的高度

        直接按子控件的 sizeHint 求和，不读布局的 sizeHint：刚增删完条目时
        布局缓存还没刷新（实测读到 0，裁剪循环因此少裁一条，折叠入口被
        挤出格子）。
        """
        lay = self.layout()
        c = lay.contentsMargins()
        gap = self._chips_layout.spacing()
        heights = [w.sizeHint().height() for w in self._chip_widgets]
        if self._more_btn is not None:
            heights.append(self._more_btn.sizeHint().height())
        total = (c.top() + c.bottom() + self._head.sizeHint().height()
                 + lay.spacing())
        if heights:
            total += sum(heights) + gap * (len(heights) - 1)
        return total

    def _content_fits(self) -> bool:
        return self._content_height() <= self._row_height()

    def _show_all_cards(self) -> None:
        """列出当天全部卡片（点「还有 N 项…」，或在该格按 Enter）

        此前 `_more_btn is None` 时直接返回，于是"条目没超上限"的格子按
        Enter 毫无反应；锚点退回到格子本身即可。
        """
        if not self._all_cards:
            return
        menu = QMenu(self)
        fm = menu.fontMetrics()
        for card in self._all_cards:
            act = menu.addAction(
                fm.elidedText(card.title, Qt.ElideRight, 260))
            act.triggered.connect(
                lambda _=False, cid=card.id: self.signal_edit_card.emit(cid))
        anchor_widget = self._more_btn if self._more_btn is not None else self
        anchor = anchor_widget.mapToGlobal(QPoint(0, anchor_widget.height()))
        menu.exec(anchor)

    # ── 拖放改期 ──────────────────────────────────────────

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_CARD) and self._iso:
            self._apply_style(drop=True)
            if self._month_offset:
                # 补位格：悬停一会儿就翻月（见 CalendarDialog 的翻月定时器）
                self.signal_drag_hover.emit(self._month_offset)
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_CARD) and self._iso:
            if self._month_offset:
                self.signal_drag_hover.emit(self._month_offset)
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_CARD) and self._iso:
            card_id = bytes(event.mimeData().data(MIME_CARD)).decode("utf-8")
            self.signal_drop_card.emit(card_id, self._iso)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._apply_style()
        if self._month_offset:
            self.signal_drag_leave.emit()
        super().dragLeaveEvent(event)

    def keyPressEvent(self, event) -> None:
        """键盘可达：方向键在日期间移动焦点，Enter 打开当天菜单

        Tab 不在这里处理：`QWidget::event()` 会先把 Tab 交给
        `focusNextPrevChild()`，走不到 keyPressEvent——"整组只占一个 tab 站"
        由 CalendarDialog.focusNextPrevChild 兜住。
        """
        key = event.key()
        if key in _ARROW_STEPS:
            self.signal_move_focus.emit(self._iso, key)
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            self._show_all_cards()
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        """双击空白格 → 新建一张截止日为该天的卡片

        补位格不响应：格子显示的是别的月份，双击在那里建卡却看不到结果
        （当前月视图不会翻过去），只会让人以为没生效。
        """
        if event.button() == Qt.LeftButton and self._iso and self._in_month:
            self.signal_create_card.emit(self._iso)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def enterEvent(self, event) -> None:
        self._apply_style(hover=True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._apply_style()
        super().leaveEvent(event)


class CalendarDialog(QDialog):
    """日历视图对话框（非模态；数据经 set_month 注入）"""

    signal_month_changed = Signal(int, int)          # year, month
    signal_due_change = Signal(str, str)             # card_id, iso_date
    signal_edit_requested = Signal(str)              # card_id
    signal_create_requested = Signal(str)            # iso_date（双击空白格建卡）

    # 拖拽悬停在上下月补位格上多久后自动翻月（毫秒）：够看清落点又不会
    # 因为"路过"就翻页——补位格只在月历首尾两行，正常拖拽很难误入
    _FLIP_HOVER_MS = 650

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("日历视图"))
        self.setModal(False)
        self.setMinimumSize(760, 540)
        self._year = date.today().year
        self._month = date.today().month
        self._cells: list[_DayCell] = []
        # 拖拽悬停补位格 → 到点翻月（拖动中的手一直按着，翻月后新格子
        # 继续接收同一拖拽的 dragMove，落点自然接续）
        self._flip_offset = 0
        self._flip_timer = QTimer(self)
        self._flip_timer.setSingleShot(True)
        self._flip_timer.setInterval(self._FLIP_HOVER_MS)
        self._flip_timer.timeout.connect(self._on_flip_timeout)
        self._row_h = _CELL_IDEAL_H

        root = QVBoxLayout(self)
        root.setContentsMargins(*tokens.dialog_margins(bottom_trim=2))
        root.setSpacing(8)

        head = QHBoxLayout()
        self._prev_btn = icons.IconButton("chevron-left", 16)
        self._prev_btn.setFixedWidth(36)
        self._prev_btn.setCursor(Qt.PointingHandCursor)
        self._prev_btn.clicked.connect(lambda: self._shift_month(-1))
        head.addWidget(self._prev_btn)
        self._month_label = QLabel()
        self._month_label.setObjectName("calMonth")
        head.addWidget(self._month_label, 1, Qt.AlignCenter)
        self._next_btn = icons.IconButton("chevron-right", 16)
        self._next_btn.setFixedWidth(36)
        self._next_btn.setCursor(Qt.PointingHandCursor)
        self._next_btn.clicked.connect(lambda: self._shift_month(1))
        head.addWidget(self._next_btn)
        self._today_btn = QPushButton(tr("回到今天"))
        self._today_btn.setCursor(Qt.PointingHandCursor)
        self._today_btn.clicked.connect(self._go_today)
        head.addWidget(self._today_btn)
        root.addLayout(head)

        grid_head = QGridLayout()
        for i in range(_WEEKDAY_COUNT):
            lab = QLabel(weekday_short(i))
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

        # 底部右对齐按钮行：与归档等对话框同布局（顶部只留导航）
        foot = QHBoxLayout()
        self._hint_label = QLabel(
            tr("双击空白格新建卡片 · 拖动条目改期（拖到灰色格自动翻月）"))
        self._hint_label.setObjectName("calHint")
        foot.addWidget(self._hint_label)
        foot.addStretch(1)
        self._close_btn = QPushButton(tr("关闭"))
        self._close_btn.setCursor(Qt.PointingHandCursor)
        self._close_btn.clicked.connect(self.close)
        foot.addWidget(self._close_btn)
        root.addLayout(foot)

        self.reapply_theme()
        AppTheme.register(self._on_theme_changed)

    # ── 导航 ──────────────────────────────────────────────

    def current_year(self) -> int:
        """当前显示的年（公开访问器：控制器刷新数据用，勿直读 _year）"""
        return self._year

    def current_month(self) -> int:
        """当前显示的月（同上）"""
        return self._month

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

    # ── 拖拽悬停自动翻月 ──────────────────────────────────

    def _on_drag_hover(self, month_offset: int) -> None:
        self._flip_offset = month_offset
        if not self._flip_timer.isActive():
            self._flip_timer.start()

    def _on_drag_leave(self) -> None:
        self._flip_offset = 0
        self._flip_timer.stop()

    def _on_flip_timeout(self) -> None:
        offset = self._flip_offset
        self._flip_offset = 0
        if offset:
            self._shift_month(offset)

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
        rows = (offset + days_in_month + 6) // 7
        for i in range(rows * 7):
            day_num = i - offset + 1
            r, c = divmod(i, 7)
            if day_num < 1 or day_num > days_in_month:
                # 上下月补位格：用**真实日期**（不是占位），这样它们既能
                # 渲染当天条目、也能作为拖放落点、还是自动翻月的入口
                d = first + timedelta(days=day_num - 1)
                cell = _DayCell(d.day, in_month=False, is_today=d == today,
                                iso=d.isoformat(),
                                month_offset=-1 if day_num < 1 else 1)
                cell.set_cards(due_map.get(d.isoformat(), []))
            else:
                d = date(year, month, day_num)
                iso = d.isoformat()
                cell = _DayCell(day_num, in_month=True,
                                is_today=d == today, iso=iso)
                cell.set_cards(due_map.get(iso, []))
            cell.signal_drop_card.connect(self.signal_due_change)
            cell.signal_edit_card.connect(self.signal_edit_requested)
            cell.signal_create_card.connect(self.signal_create_requested)
            cell.signal_drag_hover.connect(self._on_drag_hover)
            cell.signal_drag_leave.connect(self._on_drag_leave)
            cell.signal_move_focus.connect(self._move_cell_focus)
            self._grid.addWidget(cell, r, c)
            self._cells.append(cell)
        for c in range(7):
            self._grid.setColumnStretch(c, 1)
        # rowStretch 只增不清：从 6 行月份切到 4 行月份时，旧行的残留
        # stretch 会在底部拉出一条空白带（格子不再撑满）。月份最多 6 行，
        # 固定清 0..5（rowCount 只反映当前行数，清不到残留行）
        for r in range(6):
            self._grid.setRowStretch(r, 0)
        for r in range(rows):
            self._grid.setRowStretch(r, 1)
        self._fit_row_height()

    # ── 键盘可达与行高 ────────────────────────────────────

    def _fit_row_height(self) -> None:
        """行高按视口推导：整月各行在视口内完整可见，不再一打开就带滚动条

        行高 = 可用视口高 / 行数，夹在 [_CELL_MIN_H, 内容自然高度]：行数少
        的月份不会被拉得过高（上限就是"3 条 chip + 折叠入口全放得下"），
        行数多的月份压缩到下限为止——再矮折叠入口就会被裁掉。

        上限取 `natural_height()` 而不是常量：它随字体与全局样式表变
        （实测差 2px），取小了会在宽裕的窗口里也无谓折叠一条 chip。
        """
        rows = len(self._cells) // _WEEKDAY_COUNT
        if rows <= 0:
            return
        avail = self._scroll.viewport().height() - self._grid.spacing() * (rows - 1)
        if avail <= 0:
            return                     # 布局还没跑过（构造期 / 未显示）
        ideal = max((c.natural_height() for c in self._cells),
                    default=_CELL_IDEAL_H)
        self._row_h = max(_CELL_MIN_H, min(ideal, avail // rows))
        for cell in self._cells:
            cell.set_row_height(self._row_h)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_row_height()

    def _move_cell_focus(self, iso: str, key: int) -> None:
        """方向键在日期间移动焦点（7 列网格，roving focus）"""
        idx = next((i for i, c in enumerate(self._cells)
                    if c.iso() == iso), None)
        if idx is None:
            return
        target = idx + _ARROW_STEPS.get(key, 0)
        if 0 <= target < len(self._cells):
            cell = self._cells[target]
            cell.setFocus(Qt.TabFocusReason)
            self._scroll.ensureWidgetVisible(cell)

    def focusNextPrevChild(self, next: bool) -> bool:  # noqa: A002 — Qt 命名
        """Tab 从月历网格直接跳出，而不是逐格跳

        35 个格子 + 每格最多 3 条 chip 都在 tab 链里，逐格 Tab 过去没法用；
        roving focus 的"整组只占一个 tab 站"语义在这里兜住。注意
        `QWidget::event()` 会先把 Tab 交给本方法，所以格子那边拿不到
        keyPressEvent（这也是不在 keyPressEvent 里处理 Tab 的原因）。
        """
        if isinstance(self.focusWidget(), (_DayCell, _Chip)):
            target = self._close_btn if next else self._today_btn
            target.setFocus(Qt.TabFocusReason)
            return True
        return super().focusNextPrevChild(next)

    # ── 主题 ──────────────────────────────────────────────

    def _on_theme_changed(self) -> None:
        self.reapply_theme()

    def reapply_theme(self) -> None:
        c = AppTheme.colors()
        self.setStyleSheet(f"""
            QDialog {{ background: {c['bg_primary']}; }}
            QLabel#calMonth {{
                color: {c['text_primary']};
                {tokens.font_css(15)};
                font-weight: bold;
                background: transparent;
            }}
            QLabel#weekdayHead {{
                color: {c['text_secondary']};
                {tokens.font_css(11)};
                background: transparent;
            }}
            QLabel#calHint {{
                color: {c['text_secondary']};
                {tokens.font_css(11)};
                background: transparent;
            }}
            QPushButton {{
                background: {c['bg_card']};
                color: {c['text_primary']};
                border: 1px solid {c['border']};
                border-radius: {tokens.radius('control')}px;
                {tokens.pad_preset(AppConfig.UI_PAD_SECONDARY)};
            }}
            QPushButton:hover {{ background: {c['bg_hover']}; }}
        """)
        for cell in self._cells:
            cell._apply_style()
            for w in cell._chip_widgets:
                if isinstance(w, _Chip):
                    w._apply_style()
        # 样式表整份换过：条目/折叠入口的探针高度可能变，行高与预算重推
        for cell in self._cells:
            cell.invalidate_metrics()
        self._fit_row_height()

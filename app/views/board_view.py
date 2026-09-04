"""
看板视图（展开态）：Trello 风格

- 渐变背景 + 顶部工具栏（标题、统计、主题切换、折叠按钮）
- 横向滚动的列表区，每个列表是半透明圆角卡片容器
- 卡片支持跨列表拖拽（内部 QDrag）与列表内重排
- 单击卡片编辑（标题/备注/标签/截止日期/完成），悬停右上角删除
- 列表头部：双击标题重命名、悬停显示"…"菜单（重命名/删除列表）
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import (
    QMimeData,
    QPoint,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QDrag,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QColor,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.config import AppConfig
from app.models.board import BoardList, Card
from app.views.theme import AppTheme

MIME_LIST = "application/x-petboard-list"
MIME_CARD = "application/x-petboard-card"


def _clear_layout_recursive(layout) -> None:
    """递归清空布局：把所有 widget（含子布局内的）立即脱离父级再延迟删除

    只遍历 takeAt 顶层会漏掉子布局里的控件——它们会变成无布局的孤儿
    子件继续叠加渲染（表现为旧徽章/色条铺满卡片）。
    """
    while layout.count():
        item = layout.takeAt(0)
        child_layout = item.layout()
        if child_layout is not None:
            _clear_layout_recursive(child_layout)
            continue
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()


def _fmt_due(due: str) -> tuple[str, bool]:
    """截止日期 → (显示文本, 是否已过期)"""
    try:
        d = date.fromisoformat(due)
    except ValueError:
        return due, False
    today = date.today()
    diff = (d - today).days
    if diff < 0:
        return f"已逾期 {d.month}/{d.day}", True
    if diff == 0:
        return "今天截止", True
    if diff == 1:
        return "明天截止", False
    return f"{d.month}月{d.day}日", False


class CardWidget(QFrame):
    """看板卡片"""

    signal_edit_requested = Signal(object)      # card
    signal_done_toggled = Signal(str, bool)     # card_id, done
    signal_delete_requested = Signal(str)       # card_id

    def __init__(self, card: Card, parent=None):
        super().__init__(parent)
        self._card = card
        self._drag_start = QPoint()
        self._pressing = False
        self._hovered = False
        self._delete_btn: QPushButton | None = None
        self.setCursor(Qt.PointingHandCursor)
        self.rebuild()

    def card(self) -> Card:
        return self._card

    def set_card(self, card: Card) -> None:
        self._card = card
        self.rebuild()

    def reapply_style(self) -> None:
        """主题切换后轻量刷新卡片背景/边框（不重建子控件，保留悬停状态）"""
        c = AppTheme.colors()
        self.setStyleSheet(f"""
            QFrame#cardFrame {{
                background: {c['bg_card']};
                border: 1px solid {c['border']};
                border-radius: 10px;
            }}
            QFrame#cardFrame:hover {{
                border: 1px solid {c['accent']};
            }}
        """)
        if self._delete_btn is not None:
            btn = self._delete_btn
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(128, 128, 128, 0.25);
                    color: {c['text_primary']};
                    border: none;
                    border-radius: {AppConfig.CARD_DELETE_BTN_H // 2}px;
                    font-size: 9px;
                    font-weight: bold;
                    padding: 0;
                }}
                QPushButton:hover {{ background: {c['danger']}; color: white; }}
            """)
            if not self._hovered and not btn.underMouse():
                btn.hide()

    # ── 构建 UI ───────────────────────────────────────────

    def rebuild(self) -> None:
        # 清空旧布局：递归清空并让旧子件立刻脱离卡片，
        # 否则子布局里的控件会变成孤儿继续叠加渲染
        old = self.layout()
        if old is not None:
            _clear_layout_recursive(old)
            QWidget().setLayout(old)  # type: ignore[arg-type]

        c = AppTheme.colors()
        card = self._card
        self.setObjectName("cardFrame")
        self.setStyleSheet(f"""
            QFrame#cardFrame {{
                background: {c['bg_card']};
                border: 1px solid {c['border']};
                border-radius: 10px;
            }}
            QFrame#cardFrame:hover {{
                border: 1px solid {c['accent']};
            }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        # 标签色条
        if card.labels:
            labels_row = QHBoxLayout()
            labels_row.setSpacing(4)
            for key in card.labels[:6]:
                bg, fg = AppTheme.label_style(key)
                chip = QLabel()
                chip.setFixedSize(30, 8)
                chip.setStyleSheet(
                    f"background: {bg}; border-radius: 3px;")
                labels_row.addWidget(chip)
            labels_row.addStretch(1)
            root.addLayout(labels_row)

        # 标题行（勾选 + 文本）
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        check = QPushButton("☑" if card.done else "☐")
        check.setFlat(True)
        check.setFixedWidth(20)
        check.setCursor(Qt.PointingHandCursor)
        check.setToolTip("点击切换完成状态")
        check.setStyleSheet(f"""
            QPushButton {{
                color: {c['success'] if card.done else c['text_disabled']};
                font-size: 15px;
                background: transparent;
                border: none;
                padding: 0;
            }}
            QPushButton:hover {{ color: {c['accent']}; }}
        """)
        check.clicked.connect(
            lambda: self.signal_done_toggled.emit(self._card.id,
                                                  not self._card.done))
        title_row.addWidget(check)

        title = QLabel(card.title)
        title.setWordWrap(True)
        title.setStyleSheet(f"""
            QLabel {{
                font-size: 13px;
                font-weight: 500;
                color: {c['text_disabled'] if card.done else c['text_primary']};
                text-decoration: {'line-through;' if card.done else 'none;'}
                background: transparent;
                border: none;
            }}
        """)
        title_row.addWidget(title, 1)
        root.addLayout(title_row)

        # 右上角删除按钮：仅作 child 绝对定位（不占布局，不挤压标题），悬停卡片才出现
        self._delete_btn = QPushButton("✕", self)
        self._delete_btn.setFixedSize(AppConfig.CARD_DELETE_BTN_H,
                                      AppConfig.CARD_DELETE_BTN_H)
        self._delete_btn.setCursor(Qt.PointingHandCursor)
        self._delete_btn.setToolTip("删除卡片")
        self._delete_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(128, 128, 128, 0.25);
                color: {c['text_primary']};
                border: none;
                border-radius: {AppConfig.CARD_DELETE_BTN_H // 2}px;
                font-size: 9px;
                font-weight: bold;
                padding: 0;
            }}
            QPushButton:hover {{ background: {c['danger']}; color: white; }}
        """)
        self._delete_btn.clicked.connect(
            lambda: self.signal_delete_requested.emit(self._card.id))
        self._delete_btn.hide()

        # 底部信息行（截止日期 / 备注图标）
        meta_items = []
        if card.due_date:
            text, overdue = _fmt_due(card.due_date)
            color = c["danger"] if overdue else c["accent"]
            meta_items.append((text, color))
        if card.notes:
            meta_items.append(("≡ 有备注", c["text_secondary"]))

        if meta_items:
            meta_row = QHBoxLayout()
            meta_row.setSpacing(8)
            for text, color in meta_items:
                badge = QLabel(text)
                badge.setStyleSheet(f"""
                    QLabel {{
                        background: {c['accent_soft']};
                        color: {color};
                        border-radius: 5px;
                        padding: 2px 7px;
                        font-size: 11px;
                    }}
                """)
                meta_row.addWidget(badge)
            meta_row.addStretch(1)
            root.addLayout(meta_row)

        # 底部弹性：防止上面的控件（如徽章）被布局纵向拉伸满整个卡片
        root.addStretch(1)

    def resizeEvent(self, event) -> None:
        """跟随卡片把删除按钮钉在右上角"""
        super().resizeEvent(event)
        if self._delete_btn is not None:
            self._delete_btn.move(
                self.width() - self._delete_btn.width() - 6, 6)

    def enterEvent(self, event) -> None:
        self._hovered = True
        if self._delete_btn is not None:
            self._delete_btn.show()
            self._delete_btn.raise_()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        if self._delete_btn is not None and not self._delete_btn.underMouse():
            self._delete_btn.hide()
        super().leaveEvent(event)

    # ── 拖拽 ──────────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._pressing = True
            self._drag_start = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._pressing and event.buttons() == Qt.LeftButton:
            if (event.position().toPoint() - self._drag_start
                    .manhattanLength() > AppConfig.CARD_DRAG_THRESHOLD):
                self._start_drag()
                self._pressing = False
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self._pressing:
            self._pressing = False
            # 单击（无明显位移）→ 打开编辑对话框
            if (event.position().toPoint() - self._drag_start
                    .manhattanLength() <= AppConfig.CARD_DRAG_THRESHOLD):
                self.signal_edit_requested.emit(self._card)
        super().mouseReleaseEvent(event)

    def _start_drag(self) -> None:
        mime = QMimeData()
        mime.setData(MIME_CARD, self._card.id.encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        # 拖拽预览：卡片自身截图
        pixmap = self.grab()
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(pixmap.width() // 2, 14))
        drag.exec(Qt.MoveAction)
        self._pressing = False


class ListHeader(QWidget):
    """列表头部：标题（双击重命名）+ 计数 + "⋯"菜单（重命名/删除）"""

    signal_title_changed = Signal(str, str)   # list_id, new_title
    signal_delete_requested = Signal(str)     # list_id

    def __init__(self, board_list: BoardList, parent=None):
        super().__init__(parent)
        self._lst = board_list

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 10, 6)
        layout.setSpacing(6)

        c = AppTheme.colors()
        accent = AppConfig.LIST_ACCENTS[hash(board_list.id) % len(AppConfig.LIST_ACCENTS)]

        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background: {accent}; border-radius: 4px;")
        layout.addWidget(dot)

        self._title_label = QLabel(board_list.title)
        self._title_label.setStyleSheet(f"""
            QLabel {{
                font-size: 13px;
                font-weight: bold;
                color: {c['text_primary']};
                background: transparent;
            }}
        """)
        layout.addWidget(self._title_label, 1)

        self._count_label = QLabel()
        self._count_label.setStyleSheet(f"""
            QLabel {{
                color: {c['text_secondary']};
                font-size: 11px;
                background: rgba(128, 128, 128, 0.15);
                border-radius: 8px;
                padding: 1px 7px;
            }}
        """)
        layout.addWidget(self._count_label)

        self._menu_btn = _HeaderMenuButton(self)
        self._menu_btn.setToolTip("列表操作")
        self._menu_btn.clicked.connect(self._show_menu)
        layout.addWidget(self._menu_btn)

        # 双击标题进入重命名编辑
        self._title_label.mouseDoubleClickEvent = self._start_rename  # type: ignore
        self._menu_btn.mouseDoubleClickEvent = self._start_rename     # type: ignore

        # 列表操作菜单
        self._menu = QMenu(self)
        self._act_rename = QAction("重命名", self._menu)
        self._act_delete = QAction("删除列表", self._menu)
        self._menu.addAction(self._act_rename)
        self._menu.addAction(self._act_delete)
        self._act_rename.triggered.connect(self._start_rename)
        self._act_delete.triggered.connect(
            lambda: self.signal_delete_requested.emit(self._lst.id))

    def _show_menu(self) -> None:
        self._menu.exec(self._menu_btn.mapToGlobal(
            QPoint(0, self._menu_btn.height() + 2)))

    def update_count(self, n: int) -> None:
        self._count_label.setText(str(n))

    def reapply_theme(self) -> None:
        """主题切换后刷新头部颜色（标题/计数随主题变化）"""
        c = AppTheme.colors()
        self._title_label.setStyleSheet(f"""
            QLabel {{
                font-size: 13px;
                font-weight: bold;
                color: {c['text_primary']};
                background: transparent;
            }}
        """)
        self._count_label.setStyleSheet(f"""
            QLabel {{
                color: {c['text_secondary']};
                font-size: 11px;
                background: rgba(128, 128, 128, 0.15);
                border-radius: 8px;
                padding: 1px 7px;
            }}
        """)
        self._menu_btn.reapply()

    def _start_rename(self, event=None) -> None:
        # 双击标题/按钮传入 QMouseEvent；QAction.triggered 传入 False(bool)
        if isinstance(event, QMouseEvent) and event.button() != Qt.LeftButton:
            return
        edit = QLineEdit(self._lst.title, self)
        edit.selectAll()
        edit.setFixedWidth(self._title_label.width() + 20)
        edit.setStyleSheet("""
            QLineEdit { font-size: 13px; font-weight: bold; }
        """)

        def commit() -> None:
            new_title = edit.text().strip()
            edit.deleteLater()
            if new_title and new_title != self._lst.title:
                self.signal_title_changed.emit(self._lst.id, new_title)

        edit.returnPressed.connect(commit)
        edit.editingFinished.connect(commit)
        edit.setParent(self.parentWidget())
        edit.move(self._title_label.mapTo(self.parentWidget(), QPoint(0, 0)))
        edit.show()
        edit.setFocus()


class _HeaderMenuButton(QPushButton):
    """列表头部自绘"⋯"按钮（无文本，避免样式表被全局 QPushButton 规则改写）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(22, 22)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("headerMenuBtn")
        self.reapply()

    def paintEvent(self, event) -> None:
        c = AppTheme.colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        hovered = self.underMouse() or self.isDown()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(c["text_secondary"])
                         if not hovered else QColor(c["text_primary"]))
        y = self.height() // 2
        for i in (-1, 0, 1):
            painter.drawEllipse(self.width() - 15, y - 1 + i * 4, 3, 3)
        painter.end()

    def reapply(self) -> None:
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")


class ListColumn(QFrame):
    """看板列表列（头部 + 卡片区 + 添加按钮）"""

    signal_card_edit = Signal(object)
    signal_card_done = Signal(str, bool)
    signal_card_delete = Signal(str)
    signal_card_move = Signal(str, str, int)   # card_id, target_list_id, index
    signal_add_card = Signal(str)              # list_id
    signal_title_changed = Signal(str, str)
    signal_delete_list = Signal(str)

    def __init__(self, board_list: BoardList, parent=None):
        super().__init__(parent)
        self._lst = board_list
        self._card_widgets: list[CardWidget] = []
        self.setAcceptDrops(True)

        self.setObjectName("listColumn")
        self.reapply_frame_style()

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 4, 6, 8)
        root.setSpacing(6)

        self._header = ListHeader(board_list, self)
        self._header.signal_title_changed.connect(self.signal_title_changed)
        self._header.signal_delete_requested.connect(self.signal_delete_list)
        root.addWidget(self._header)

        # 卡片滚动区
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self._scroll.viewport().setAutoFillBackground(False)
        self._cards_host = QWidget()
        self._cards_host.setStyleSheet("background: transparent;")
        self._cards_layout = QVBoxLayout(self._cards_host)
        self._cards_layout.setContentsMargins(2, 2, 2, 2)
        self._cards_layout.setSpacing(8)
        self._cards_layout.addStretch(1)
        self._scroll.setWidget(self._cards_host)
        root.addWidget(self._scroll, 1)

        # 添加按钮
        self._add_btn = AddCardButton("+ 添加卡片")
        self._add_btn.clicked.connect(
            lambda: self.signal_add_card.emit(self._lst.id))
        root.addWidget(self._add_btn)

        self.refresh()

    # ── 样式 ──────────────────────────────────────────────

    def reapply_frame_style(self) -> None:
        c = AppTheme.colors()
        self.setStyleSheet(f"""
            QFrame#listColumn {{
                background: {c['bg_panel']};
                border: 1px solid {c['border']};
                border-radius: 14px;
            }}
        """)
        self._add_btn = getattr(self, "_add_btn", None)
        if self._add_btn is not None:
            self._add_btn.reapply()

    def minimumSizeHint(self):
        return QSize(AppConfig.LIST_WIDTH, 200)

    def sizeHint(self):
        return QSize(AppConfig.LIST_WIDTH, 400)

    # ── 数据刷新 ──────────────────────────────────────────

    def list_id(self) -> str:
        return self._lst.id

    def refresh(self) -> None:
        """按 board_list.cards 重建卡片控件（保持滚动位置）"""
        sb = self._scroll.verticalScrollBar()
        scroll_pos = sb.value()

        # 清空旧控件
        for w in self._card_widgets:
            w.setParent(None)
            w.deleteLater()
        self._card_widgets = []

        # 去掉旧项（含 stretch；旧控件先脱离父级再延迟删除）
        while self._cards_layout.count() > 0:
            item = self._cards_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        for card in self._lst.cards:
            cw = CardWidget(card)
            cw.signal_edit_requested.connect(self.signal_card_edit)
            cw.signal_done_toggled.connect(self.signal_card_done)
            cw.signal_delete_requested.connect(self.signal_card_delete)
            self._cards_layout.addWidget(cw)
            self._card_widgets.append(cw)

        self._cards_layout.addStretch(1)
        self._header.update_count(len(self._lst.cards))

        sb.setValue(scroll_pos)

    # ── 拖放 ──────────────────────────────────────────────

    def _drop_index_from_y(self, y_global: int) -> int:
        """根据全局 y 坐标计算插入位置（卡片序号）"""
        for i, cw in enumerate(self._card_widgets):
            top = cw.mapToGlobal(QPoint(0, 0)).y()
            if y_global < top + cw.height() // 2:
                return i
        return len(self._card_widgets)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_CARD):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_CARD):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        if not event.mimeData().hasFormat(MIME_CARD):
            return
        card_id = bytes(event.mimeData().data(MIME_CARD)).decode("utf-8")
        index = self._drop_index_from_y(
            event.position().toPoint().y()
            + self.mapToGlobal(QPoint(0, 0)).y())
        self.signal_card_move.emit(card_id, self._lst.id, index)
        event.acceptProposedAction()


class AddCardButton(QPushButton):
    """带主题样式的添加卡片按钮"""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.reapply()

    def reapply(self) -> None:
        c = AppTheme.colors()
        self.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {c['text_secondary']};
                border: 1.5px dashed {c['border']};
                border-radius: 9px;
                padding: 7px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: {c['accent_soft']};
                color: {c['accent']};
                border: 1.5px dashed {c['accent']};
            }}
        """)


class BoardView(QWidget):
    """看板顶层视图：渐变背景 + 工具栏 + 列表横向滚动区"""

    signal_collapse_clicked = Signal()
    signal_theme_selected = Signal(str)         # light / dark
    signal_card_edit = Signal(str, str)         # list_id, card_id
    signal_card_done = Signal(str, str, bool)   # list_id, card_id, done
    signal_card_delete = Signal(str, str)       # list_id, card_id
    signal_card_move = Signal(str, str, int)    # card_id, target_list_id, index
    signal_card_add = Signal(str)               # list_id
    signal_list_add = Signal()
    signal_list_title_changed = Signal(str, str)
    signal_list_delete = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lists: list[BoardList] = []
        self._columns: list[ListColumn] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 工具栏 ────────────────────────────────────────
        self._toolbar = QWidget()
        self._toolbar.setFixedHeight(56)
        self._toolbar_layout = QHBoxLayout(self._toolbar)
        self._toolbar_layout.setContentsMargins(18, 8, 14, 8)
        self._toolbar_layout.setSpacing(10)

        self._title_label = QLabel("🗂 我的看板")
        self._toolbar_layout.addWidget(self._title_label)

        self._stats_label = QLabel()
        self._toolbar_layout.addWidget(self._stats_label)
        self._toolbar_layout.addStretch(1)

        self._add_list_btn = AddCardButton("+ 添加列表")
        self._add_list_btn.setFixedWidth(96)
        self._add_list_btn.clicked.connect(self.signal_list_add.emit)
        self._toolbar_layout.addWidget(self._add_list_btn)

        self._theme_btn = QPushButton()
        self._theme_btn.setCursor(Qt.PointingHandCursor)
        self._theme_btn.setFixedSize(34, 34)
        self._theme_btn.setToolTip("切换浅色 / 深色主题")
        self._theme_btn.clicked.connect(self._on_theme_clicked)
        self._toolbar_layout.addWidget(self._theme_btn)

        self._collapse_btn = QPushButton("－")
        self._collapse_btn.setCursor(Qt.PointingHandCursor)
        self._collapse_btn.setFixedSize(34, 34)
        self._collapse_btn.setToolTip("折叠为桌宠")
        self._collapse_btn.clicked.connect(self.signal_collapse_clicked.emit)
        self._toolbar_layout.addWidget(self._collapse_btn)

        root.addWidget(self._toolbar)

        # ── 列表区（横向滚动） ────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.viewport().setAutoFillBackground(False)
        self._lists_host = QWidget()
        self._lists_host.setStyleSheet("background: transparent;")
        self._lists_layout = QHBoxLayout(self._lists_host)
        self._lists_layout.setContentsMargins(16, 4, 16, 12)
        self._lists_layout.setSpacing(12)
        self._lists_layout.addStretch(1)
        self._scroll.setWidget(self._lists_host)
        root.addWidget(self._scroll, 1)

        self.reapply_theme()
        AppTheme.register(self.reapply_theme)

    # ── 主题 ──────────────────────────────────────────────

    def reapply_theme(self) -> None:
        c = AppTheme.colors()
        self._title_label.setStyleSheet(f"""
            QLabel {{
                font-size: 17px;
                font-weight: bold;
                color: {c['text_primary']};
                background: transparent;
            }}
        """)
        self._stats_label.setStyleSheet(f"""
            QLabel {{
                font-size: 12px;
                color: {c['text_primary']};
                background: rgba(128, 128, 128, 0.18);
                border-radius: 9px;
                padding: 3px 10px;
            }}
        """)
        icon_color = c["text_primary"]
        self._theme_btn.setText("🌙" if AppTheme.mode() == "light" else "☀️")
        self._theme_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(128, 128, 128, 0.15);
                border: none;
                border-radius: 17px;
                font-size: 15px;
                color: {icon_color};
            }}
            QPushButton:hover {{ background: rgba(128, 128, 128, 0.30); }}
        """)
        self._collapse_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(128, 128, 128, 0.15);
                border: none;
                border-radius: 17px;
                font-size: 16px;
                font-weight: bold;
                color: {icon_color};
            }}
            QPushButton:hover {{ background: rgba(128, 128, 128, 0.30); }}
        """)
        self._add_list_btn.reapply()
        for col in self._columns:
            col.reapply_frame_style()
            col._header.reapply_theme()
            for cw in col._card_widgets:
                cw.reapply_style()

    def _on_theme_clicked(self) -> None:
        self.signal_theme_selected.emit(
            "dark" if AppTheme.mode() == "light" else "light")

    # ── paintEvent：渐变背景 ──────────────────────────────

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        c = AppTheme.colors()
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0.0, QColor(c["board_bg_start"]))
        gradient.setColorAt(0.55, QColor(c["board_bg_mid"]))
        gradient.setColorAt(1.0, QColor(c["board_bg_end"]))
        painter.fillRect(self.rect(), gradient)
        painter.end()

    # ── 数据刷新 ──────────────────────────────────────────

    def refresh(self, lists: list[BoardList]) -> None:
        """按看板数据重建列"""
        # 记录看板横向滚动位置，重建后恢复
        sb = self._scroll.horizontalScrollBar()
        scroll_pos = sb.value()

        self._lists = lists

        for col in self._columns:
            col.setParent(None)
            col.deleteLater()
        self._columns = []

        while self._lists_layout.count() > 0:
            item = self._lists_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        for lst in lists:
            col = ListColumn(lst)
            col.signal_card_edit.connect(self._on_card_edit)
            col.signal_card_done.connect(self._on_card_done)
            col.signal_card_delete.connect(self._on_card_delete)
            col.signal_card_move.connect(self.signal_card_move)
            col.signal_add_card.connect(self.signal_card_add)
            col.signal_title_changed.connect(self.signal_list_title_changed)
            col.signal_delete_list.connect(self.signal_list_delete)
            self._lists_layout.addWidget(col)
            self._columns.append(col)

        self._lists_layout.addStretch(1)
        self.update_stats(lists)
        self.reapply_theme()
        sb.setValue(scroll_pos)

    def update_stats(self, lists: list[BoardList]) -> None:
        total = sum(len(l.cards) for l in lists)
        done = sum(1 for l in lists for card in l.cards if card.done)
        self._stats_label.setText(f"{total} 张卡片 · 完成 {done}")

    # ── 卡片信号 → 带 list_id 转发 ────────────────────────

    def _on_card_edit(self, card: Card) -> None:
        list_id = self._find_list_of_card(card.id)
        if list_id:
            self.signal_card_edit.emit(list_id, card.id)

    def _on_card_done(self, card_id: str, done: bool) -> None:
        list_id = self._find_list_of_card(card_id)
        if list_id:
            self.signal_card_done.emit(list_id, card_id, done)

    def _on_card_delete(self, card_id: str) -> None:
        list_id = self._find_list_of_card(card_id)
        if list_id:
            self.signal_card_delete.emit(list_id, card_id)

    def _find_list_of_card(self, card_id: str) -> str | None:
        for lst in self._lists:
            if any(c.id == card_id for c in lst.cards):
                return lst.id
        return None

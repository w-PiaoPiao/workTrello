"""
看板视图（展开态）：Trello 风格

- 渐变背景 + 顶部工具栏（标题、统计、主题切换、折叠按钮）
- 横向滚动的列表区，每个列表是半透明圆角卡片容器
- 卡片支持跨列表拖拽（内部 QDrag）与列表内重排
- 单击卡片编辑（标题/备注/标签/截止日期/完成），悬停右上角删除
- 列表头部：双击标题重命名、悬停显示"…"菜单（重命名/删除列表）
"""

from __future__ import annotations

import math
from datetime import date

import shiboken6
from PySide6.QtCore import (
    QEvent,
    QMimeData,
    QPoint,
    QPointF,
    QRect,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QDrag,
    QLinearGradient,
    QMouseEvent,
    QPen,
    QPainter,
    QPainterPath,
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
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.config import AppConfig
from app.models.board import BoardList, Card
from app.views.notes_popover import (
    hide_notes_popover,
    notes_pinned_for,
    notes_popover,
    notes_popover_hovering,
)
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
    signal_card_pomo = Signal(str)              # card_id
    signal_card_archive = Signal(str)           # card_id

    def __init__(self, card: Card, parent=None):
        super().__init__(parent)
        self._card = card
        self._drag_start = QPoint()
        self._pressing = False
        self._hovered = False
        self._delete_btn: QPushButton | None = None
        self._check_btn: QPushButton | None = None
        self._title_label: QLabel | None = None
        self._meta_badges: list[tuple[QLabel, str]] = []
        self._notes_badge: QLabel | None = None    # "≡ 有备注"徽章（悬停弹备注预览）
        self._fingerprint: tuple = ()
        self._focusing_id: str | None = None    # 当前正在专注的卡片 id
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_card_menu)
        self.setCursor(Qt.PointingHandCursor)
        self.rebuild()

    def set_focusing(self, card_id: str | None) -> None:
        self._focusing_id = card_id

    def _show_card_menu(self, pos) -> None:
        menu = QMenu(self)
        if self._card.id == self._focusing_id:
            act_pomo = menu.addAction("⏹ 停止专注")
        else:
            act_pomo = menu.addAction("▶ 开始专注 25 分钟")
        menu.addSeparator()
        act_archive = menu.addAction("归档")
        chosen = menu.exec(self.mapToGlobal(pos))
        if chosen is act_pomo:
            self.signal_card_pomo.emit(self._card.id)
        elif chosen is act_archive:
            self.signal_card_archive.emit(self._card.id)

    def card(self) -> Card:
        return self._card

    def update_from_model(self, card: Card) -> None:
        """增量刷新：重指向模型对象；内容指纹未变则跳过重建"""
        self._card = card
        # 备注正文不进指纹（仅 bool 参与），编辑保存后固定预览会残留旧文本：
        # 本卡任一模型刷新即收起其固定预览
        if notes_pinned_for(card.id):
            notes_popover().hide_now()
        if self._fingerprint != self._content_fingerprint():
            self.rebuild()

    def _content_fingerprint(self) -> tuple:
        """卡片内容指纹，用于跳过未变化卡片的重建"""
        c = self._card
        return (c.title, c.done, c.due_date, bool(c.notes), tuple(c.labels))

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
        self._style_check()
        self._style_title()
        self._style_meta_badges()

    def _style_check(self) -> None:
        """勾选框样式（颜色随主题切换）"""
        if self._check_btn is None:
            return
        c = AppTheme.colors()
        self._check_btn.setStyleSheet(f"""
            QPushButton {{
                color: {c['success'] if self._card.done else c['text_disabled']};
                font-size: 15px;
                background: transparent;
                border: none;
                padding: 0;
            }}
            QPushButton:hover {{ color: {c['accent']}; }}
        """)

    def _style_title(self) -> None:
        """标题样式（颜色随主题切换）"""
        if self._title_label is None:
            return
        c = AppTheme.colors()
        self._title_label.setStyleSheet(f"""
            QLabel {{
                font-size: 13px;
                font-weight: 500;
                color: {c['text_disabled'] if self._card.done else c['text_primary']};
                text-decoration: {'line-through;' if self._card.done else 'none;'}
                background: transparent;
                border: none;
            }}
        """)

    def _style_meta_badges(self) -> None:
        """底部日期/备注徽章样式（颜色随主题切换）"""
        c = AppTheme.colors()
        for badge, key in self._meta_badges:
            badge.setStyleSheet(f"""
                QLabel {{
                    background: {c['accent_soft']};
                    color: {c[key]};
                    border-radius: 5px;
                    padding: 2px 7px;
                    font-size: 11px;
                }}
            """)

    # ── 构建 UI ───────────────────────────────────────────

    def rebuild(self) -> None:
        # 清空旧布局：递归清空并让旧子件立刻脱离卡片，
        # 否则子布局里的控件会变成孤儿继续叠加渲染
        old = self.layout()
        if old is not None:
            _clear_layout_recursive(old)
            QWidget().setLayout(old)  # type: ignore[arg-type]

        # 绝对定位的删除按钮不在布局里，需显式销毁，避免重复 rebuild 时叠加残留
        if self._delete_btn is not None:
            self._delete_btn.setParent(None)
            self._delete_btn.deleteLater()

        self._check_btn = None
        self._title_label = None
        self._meta_badges = []
        self._notes_badge = None

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
        self._check_btn = check
        self._style_check()
        check.clicked.connect(
            lambda: self.signal_done_toggled.emit(self._card.id,
                                                  not self._card.done))
        title_row.addWidget(check)

        title = QLabel(card.title)
        title.setWordWrap(True)
        self._title_label = title
        self._style_title()
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
        meta_items: list[tuple[str, str, bool]] = []    # (文本, 主题色键, 是否备注徽章)
        if card.due_date:
            text, overdue = _fmt_due(card.due_date)
            meta_items.append((text, "danger" if overdue else "accent", False))
        if card.notes:
            meta_items.append(("≡ 有备注", "text_secondary", True))
        if card.pomodoros:
            meta_items.append((f"🍅 ×{card.pomodoros}", "text_secondary", False))

        if meta_items:
            meta_row = QHBoxLayout()
            meta_row.setSpacing(8)
            for text, key, is_notes in meta_items:
                badge = QLabel(text)
                self._meta_badges.append((badge, key))
                if is_notes:
                    # 备注徽章：悬停弹备注全文预览，点击固定展示（本卡事件过滤处理）
                    self._notes_badge = badge
                    badge.setCursor(Qt.PointingHandCursor)
                    badge.setToolTip("悬停预览 · 点击固定")
                    badge.installEventFilter(self)
                meta_row.addWidget(badge)
            self._style_meta_badges()
            meta_row.addStretch(1)
            root.addLayout(meta_row)

        # 底部弹性：防止上面的控件（如徽章）被布局纵向拉伸满整个卡片
        root.addStretch(1)

        self._fingerprint = self._content_fingerprint()
        # 重建后删除按钮默认隐藏；悬停中则恢复显示
        if self._hovered and self._delete_btn is not None:
            self._delete_btn.show()
            self._delete_btn.raise_()

    def resizeEvent(self, event) -> None:
        """跟随卡片把删除按钮钉在右上角"""
        super().resizeEvent(event)
        if self._delete_btn is not None:
            self._delete_btn.move(
                self.width() - self._delete_btn.width() - 6, 6)

    # ── 备注悬浮预览 ──────────────────────────────────────

    def eventFilter(self, obj, event):
        """备注徽章：Enter 弹预览浮层；Leave 延迟关闭；左键点击固定/收起"""
        if obj is self._notes_badge:
            if event.type() == QEvent.Enter:
                pop = notes_popover()
                if pop.is_pinned():
                    return False   # 已有固定展示：悬停不抢占内容
                badge = self._notes_badge
                global_rect = QRect(badge.mapToGlobal(QPoint(0, 0)),
                                    badge.size())
                pop.show_for(self._card.notes, global_rect)
                return False
            if event.type() == QEvent.Leave:
                notes_popover().schedule_hide()
                return False
            if (event.type() == QEvent.MouseButtonPress
                    and event.button() == Qt.LeftButton):
                self._toggle_notes_pin()
                return True   # 拦截冒泡，避免误开卡片编辑框
        return super().eventFilter(obj, event)

    def _toggle_notes_pin(self) -> None:
        """点击备注徽章：固定展示 ⇄ 收起（另一卡固定中则切换归属）"""
        pop = notes_popover()
        if self._notes_badge is None:
            return
        rect = QRect(self._notes_badge.mapToGlobal(QPoint(0, 0)),
                     self._notes_badge.size())
        if pop.is_pinned() and pop.pinned_for(self._card.id):
            pop.hide_now()                       # 同卡再点一次 → 收起
        else:
            pop.show_pinned(self._card.id, self._card.notes, rect)

    def hideEvent(self, event) -> None:
        """卡片隐藏时收起备注浮层：悬停预览/本卡固定预览关闭，
        固定于其他卡的预览不受牵连"""
        if notes_pinned_for(self._card.id):
            notes_popover().hide_now()
        elif notes_popover_hovering():
            hide_notes_popover()
        super().hideEvent(event)

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
            if ((event.position().toPoint() - self._drag_start)
                    .manhattanLength() > AppConfig.CARD_DRAG_THRESHOLD):
                self._start_drag()
                self._pressing = False
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self._pressing:
            self._pressing = False
            # 单击（无明显位移）→ 打开编辑对话框
            if ((event.position().toPoint() - self._drag_start)
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
                font-size: 14px;
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

    def set_list(self, board_list: BoardList) -> None:
        """增量刷新：重指向模型对象并同步标题文本"""
        self._lst = board_list
        self._title_label.setText(board_list.title)

    def reapply_theme(self) -> None:
        """主题切换后刷新头部颜色（标题/计数随主题变化）"""
        c = AppTheme.colors()
        self._title_label.setStyleSheet(f"""
            QLabel {{
                font-size: 14px;
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
        finish_active_rename(cancel=True)   # 全局同时只有一个重命名编辑器
        edit = _RenameEdit(self)
        edit.setGeometry(self._title_label.rect())
        edit.selectAll()
        edit.installEventFilter(self)
        global _ACTIVE_RENAME
        _ACTIVE_RENAME = edit
        edit.show()
        edit.setFocus()

    def eventFilter(self, obj, event) -> bool:
        # Esc 取消重命名（窗口级 QShortcut 命中前的兜底路径）
        if (event.type() == QEvent.KeyPress and obj is _ACTIVE_RENAME
                and event.key() == Qt.Key_Escape):
            finish_active_rename(cancel=True)
            return True
        return super().eventFilter(obj, event)


class _RenameEdit(QLineEdit):
    """列表标题重命名编辑器（全局同时只存在一个，见 _ACTIVE_RENAME）"""

    def __init__(self, header: "ListHeader"):
        super().__init__(header._lst.title, header._title_label)
        self._header = header
        c = AppTheme.colors()
        self.setStyleSheet(f"""
            QLineEdit {{
                background: {c['bg_card']};
                font-size: 14px;
                font-weight: bold;
                color: {c['text_primary']};
                padding: 0 4px;
                border-radius: 6px;
            }}
        """)
        self.returnPressed.connect(self.commit)
        # 注意：不挂 editingFinished（失焦提交）——Qt.Tool 窗口焦点链不可靠，
        # 关闭时机统一由点击过滤器 / Esc / 折叠 / 隐藏等显式路径驱动

    def commit(self) -> None:
        global _ACTIVE_RENAME
        if _ACTIVE_RENAME is not self:
            return
        _ACTIVE_RENAME = None
        new_title = self.text().strip()
        self.deleteLater()
        if new_title and new_title != self._header._lst.title:
            self._header.signal_title_changed.emit(
                self._header._lst.id, new_title)

    def cancel(self) -> None:
        global _ACTIVE_RENAME
        if _ACTIVE_RENAME is not self:
            return
        _ACTIVE_RENAME = None
        self.blockSignals(True)
        self.deleteLater()


_ACTIVE_RENAME: "_RenameEdit | None" = None


def finish_active_rename(cancel: bool = False) -> bool:
    """关闭当前重命名编辑器（默认提交，cancel=True 丢弃）；返回是否有关闭

    macOS 上 Qt.Tool 窗口不参与常规焦点链，"失焦提交"可能不触发，
    编辑器会残留堆叠（表现为列标题重影）。因此编辑器全局唯一，
    并在折叠 / 隐藏 / Esc / 点击其他位置时显式关闭。
    """
    global _ACTIVE_RENAME
    edit = _ACTIVE_RENAME
    if edit is None:
        return False
    if not shiboken6.isValid(edit):
        # 编辑器随旧列被 deleteLater 销毁（refresh 未先经过提交路径时），
        # 只清理悬空引用，不再触碰底层对象
        _ACTIVE_RENAME = None
        return False
    if cancel:
        edit.cancel()
    else:
        edit.commit()
    return True


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


class _ThemeToggleButton(QPushButton):
    """自绘 日/月 图标的主题切换按钮（🌙/☀️ emoji 在部分平台缺字形，改矢量绘制）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode = "light"

    def set_mode(self, mode: str) -> None:
        if mode != self._mode:
            self._mode = mode
            self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        c = AppTheme.colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        glyph = QColor(c["text_primary"])
        if self._mode == "light":
            # 浅色态显示月亮（点击切深色）
            full = QPainterPath()
            full.addEllipse(center.x() - 5.5, center.y() - 5.5, 11.0, 11.0)
            cut = QPainterPath()
            cut.addEllipse(center.x() - 1.5, center.y() - 8.0, 11.0, 11.0)
            painter.setPen(Qt.NoPen)
            painter.setBrush(glyph)
            painter.drawPath(full.subtracted(cut))
        else:
            # 深色态显示太阳（点击切浅色）
            painter.setPen(Qt.NoPen)
            painter.setBrush(glyph)
            painter.drawEllipse(center, 4.0, 4.0)
            pen = QPen(glyph, 1.4)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            for i in range(8):
                angle = math.pi * i / 4
                cos_a, sin_a = math.cos(angle), math.sin(angle)
                painter.drawLine(
                    center + QPointF(cos_a * 6.2, sin_a * 6.2),
                    center + QPointF(cos_a * 8.4, sin_a * 8.4))
        painter.end()


class ListColumn(QFrame):
    """看板列表列（头部 + 卡片区 + 添加按钮）"""

    signal_card_edit = Signal(object)
    signal_card_done = Signal(str, bool)
    signal_card_delete = Signal(str)
    signal_card_move = Signal(str, str, int)   # card_id, target_list_id, index
    signal_add_card = Signal(str)              # list_id
    signal_title_changed = Signal(str, str)
    signal_delete_list = Signal(str)
    signal_card_pomo = Signal(str)             # card_id
    signal_card_archive = Signal(str)          # card_id

    def __init__(self, board_list: BoardList, parent=None):
        super().__init__(parent)
        self._lst = board_list
        self._card_widgets: list[CardWidget] = []
        self._hint: QLabel | None = None
        self._visible_cards: list[Card] | None = None   # None=显示全部（过滤态为子集）
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
        self._cards_host.setObjectName("cardsHost")
        # 无选择器规则会级联到所有后代（曾把重命名编辑器的背景压成透明，
        # 表现为列标题文字透过编辑器显示），必须用 #objectName 限定自身
        self._cards_host.setStyleSheet(
            "QWidget#cardsHost { background: transparent; }")
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

        self.refresh_cards()

    # ── 数据刷新 ──────────────────────────────────────────

    def list_id(self) -> str:
        return self._lst.id

    def set_list(self, board_list: BoardList,
                 visible_cards: list[Card] | None = None) -> None:
        """增量刷新：重指向模型对象并同步整列内容（可带过滤子集）"""
        self._lst = board_list
        self._visible_cards = visible_cards
        self.setAcceptDrops(visible_cards is None)   # 过滤态拖放落点不可靠，禁用
        self._header.set_list(board_list)
        self.refresh_cards()

    def set_visible_cards(self, visible_cards: list[Card] | None) -> None:
        """搜索过滤：只更新可见卡片子集（内容相同则跳过，避免逐键刷新）"""
        if self._visible_cards == visible_cards:
            return
        self._visible_cards = visible_cards
        self.setAcceptDrops(visible_cards is None)
        self.refresh_cards()

    def _make_card_widget(self, card: Card) -> CardWidget:
        cw = CardWidget(card)
        cw.signal_edit_requested.connect(self.signal_card_edit)
        cw.signal_done_toggled.connect(self.signal_card_done)
        cw.signal_delete_requested.connect(self.signal_card_delete)
        cw.signal_card_pomo.connect(self.signal_card_pomo)
        cw.signal_card_archive.connect(self.signal_card_archive)
        return cw

    def set_focusing_card(self, card_id: str | None) -> None:
        for cw in self._card_widgets:
            cw.set_focusing(card_id)

    def refresh_cards(self) -> None:
        """按可见卡片增量同步卡片控件（按 card.id 复用，滚动位置自然保留）"""
        source = (self._lst.cards if self._visible_cards is None
                  else self._visible_cards)
        cards = [c for c in source if not c.archived]   # 归档卡片不出现在看板
        reusable: dict[str, CardWidget] = {
            cw.card().id: cw for cw in self._card_widgets}
        ordered: list[CardWidget] = []
        for i, card in enumerate(cards):
            cw = reusable.pop(card.id, None)
            if cw is None:
                cw = self._make_card_widget(card)
            else:
                cw.update_from_model(card)
            ordered.append(cw)
            self._cards_layout.removeWidget(cw)
            self._cards_layout.insertWidget(i, cw)
        for gone in reusable.values():
            gone.setParent(None)
            gone.deleteLater()
        self._card_widgets = ordered

        # 空列提示
        if not cards:
            if self._hint is None:
                hint_text = ("没有匹配的卡片" if self._visible_cards is not None
                             else "还没有卡片，点击下方添加")
                hint = QLabel(hint_text)
                hint.setAlignment(Qt.AlignCenter)
                hint.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                hint.setStyleSheet(
                    f"color: {AppTheme.colors()['text_disabled']};"
                    "font-size: 12px; background: transparent;")
                self._hint = hint
                self._cards_layout.insertWidget(0, hint)
        elif self._hint is not None:
            self._cards_layout.removeWidget(self._hint)
            self._hint.deleteLater()
            self._hint = None

        self._header.update_count(len(cards))

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
    signal_quit_requested = Signal()
    signal_zoom_requested = Signal()
    signal_card_pomo = Signal(str)              # card_id
    signal_card_archive = Signal(str)           # card_id
    signal_archive_open = Signal()
    signal_export = Signal(str)                 # "md" | "csv"
    signal_today_toggled = Signal(bool)         # 今日聚焦开关变化（菜单栏同步）

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
        # 右 margin：Windows 分支会让窗口控制键贴右缘，此处改为 0 由控件区补
        right_margin = 0 if AppConfig.IS_WINDOWS else 14
        self._toolbar_layout = QHBoxLayout(self._toolbar)
        self._toolbar_layout.setContentsMargins(18, 8, right_margin, 8)
        self._toolbar_layout.setSpacing(10)

        self._title_label = QLabel("🗂 我的看板")
        self._toolbar_layout.addWidget(self._title_label)

        self._stats_label = QLabel()
        self._toolbar_layout.addWidget(self._stats_label)
        self._toolbar_layout.addStretch(1)

        self._today_btn = QPushButton("⭐ 今日")
        self._today_btn.setCheckable(True)
        self._today_btn.setCursor(Qt.PointingHandCursor)
        self._today_btn.setToolTip("只显示未完成的：星标 / 已逾期 / 今天截止")
        self._today_btn.toggled.connect(self._apply_filter)
        self._today_btn.toggled.connect(self.signal_today_toggled.emit)
        self._toolbar_layout.addWidget(self._today_btn)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("搜索卡片…")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setFixedWidth(190)
        self._search_edit.setAccessibleName("搜索卡片")
        # 逐键输入只重启防抖计时器，停顿后才过滤（避免大板每键全树刷新）
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(AppConfig.SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._apply_filter)
        self._search_edit.textChanged.connect(self._on_search_edited)
        self._toolbar_layout.addWidget(self._search_edit)

        self._add_list_btn = AddCardButton("+ 添加列表")
        self._add_list_btn.setFixedWidth(96)
        self._add_list_btn.clicked.connect(self.signal_list_add.emit)
        self._toolbar_layout.addWidget(self._add_list_btn)

        self._archive_btn = QPushButton("归档")
        self._archive_btn.setCursor(Qt.PointingHandCursor)
        self._archive_btn.setToolTip("查看已归档卡片并恢复")
        self._archive_btn.clicked.connect(self.signal_archive_open.emit)
        self._toolbar_layout.addWidget(self._archive_btn)

        self._export_btn = QPushButton("导出")
        self._export_btn.setCursor(Qt.PointingHandCursor)
        self._export_btn.setToolTip("导出为 Markdown / CSV")
        self._export_btn.clicked.connect(self._show_export_menu)
        self._toolbar_layout.addWidget(self._export_btn)

        self._theme_btn = _ThemeToggleButton()
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

        # macOS 红绿灯（对齐 macOS 窗口范式）：红=退出 黄=折叠桌宠 绿=最大化/还原
        if AppConfig.IS_MACOS:
            from app.views.traffic_lights import TrafficLights
            self._traffic_lights = TrafficLights()
            self._traffic_lights.signal_close.connect(
                self.signal_quit_requested.emit)
            self._traffic_lights.signal_minimize.connect(
                self.signal_collapse_clicked.emit)
            self._traffic_lights.signal_zoom.connect(
                self.signal_zoom_requested.emit)
            self._toolbar_layout.insertWidget(0, self._traffic_lights)
            self._collapse_btn.hide()   # 黄灯已承担折叠，避免重复控件

        # Windows 窗口控制键（贴右缘）：─ 折叠桌宠  □ 最大化/还原  ✕ 退出
        self._window_controls = None
        if AppConfig.IS_WINDOWS:
            from app.views.window_controls import WindowControls
            self._window_controls = WindowControls()
            self._window_controls.signal_minimize.connect(
                self.signal_collapse_clicked.emit)
            self._window_controls.signal_zoom.connect(
                self.signal_zoom_requested.emit)
            self._window_controls.signal_close.connect(
                self.signal_quit_requested.emit)
            self._toolbar_layout.addWidget(self._window_controls)
            self._collapse_btn.hide()   # 最小化键已承担折叠，避免重复控件

        root.addWidget(self._toolbar)

        # ── 列表区（横向滚动） ────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.viewport().setAutoFillBackground(False)
        self._lists_host = QWidget()
        self._lists_host.setObjectName("listsHost")
        self._lists_host.setStyleSheet(
            "QWidget#listsHost { background: transparent; }")
        self._lists_layout = QHBoxLayout(self._lists_host)
        self._lists_layout.setContentsMargins(16, 4, 16, 12)
        self._lists_layout.setSpacing(12)
        self._lists_layout.addStretch(1)
        self._scroll.setWidget(self._lists_host)
        root.addWidget(self._scroll, 1)

        self.reapply_theme()
        AppTheme.register(self.reapply_theme)
        # 点击看板任意非编辑器位置 → 提交并关闭重命名编辑器
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        if (event.type() == QEvent.MouseButtonPress
                and _ACTIVE_RENAME is not None
                and isinstance(obj, QWidget)
                and obj is not _ACTIVE_RENAME
                and not _ACTIVE_RENAME.isAncestorOf(obj)):
            finish_active_rename()
        return super().eventFilter(obj, event)

    def finish_rename(self, cancel: bool = False) -> bool:
        """关闭当前列表重命名编辑器（cancel=True 丢弃修改）；返回是否有关闭"""
        return finish_active_rename(cancel)

    def set_zoom_state(self, zoomed: bool) -> None:
        """同步最大化/还原图标状态（macOS 无此控件，空操作）"""
        if self._window_controls is not None:
            self._window_controls.set_zoomed(zoomed)

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
        self._theme_btn.set_mode(AppTheme.mode())
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
        if self._window_controls is not None:
            self._window_controls.reapply()
        self._today_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(128, 128, 128, 0.15);
                border: none;
                border-radius: 9px;
                padding: 5px 10px;
                color: {c['text_primary']};
            }}
            QPushButton:hover {{ background: rgba(128, 128, 128, 0.30); }}
            QPushButton:checked {{ background: {c['accent']}; color: white; }}
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
        """按看板数据增量同步列（按 list.id 复用列与卡片控件）"""
        # 记录看板横向滚动位置，增删列后恢复
        sb = self._scroll.horizontalScrollBar()
        scroll_pos = sb.value()

        self._lists = lists

        visibles = {lst.id: self._visible_cards_for(lst) for lst in lists}

        by_id = {col.list_id(): col for col in self._columns}
        kept: set[str] = set()
        for i, lst in enumerate(lists):
            col = by_id.get(lst.id)
            if col is None:
                col = self._make_column(lst)
            else:
                col.set_list(lst, visibles[lst.id])
                kept.add(lst.id)
            self._lists_layout.removeWidget(col)
            self._lists_layout.insertWidget(i, col)
        for list_id, col in by_id.items():
            if list_id not in kept:
                self._columns.remove(col)
                col.setParent(None)
                col.deleteLater()

        self.update_stats(lists)
        self._set_today_count(sum(len(v or []) for v in visibles.values()))
        sb.setValue(scroll_pos)

    def _search_query(self) -> str:
        return self._search_edit.text().strip().lower()

    @staticmethod
    def _filter_cards(lst: BoardList, q: str) -> list[Card] | None:
        """按关键词过滤卡片（标题/备注，不区分大小写）；空关键词返回 None=全部"""
        if not q:
            return None
        return [c for c in lst.cards
                if q in c.title.lower() or q in c.notes.lower()]

    @staticmethod
    def _is_focus_card(c: Card, today) -> bool:
        """今日聚焦：未完成且（星标 或 截止日<=today）"""
        if c.done or c.archived:
            return False
        if c.starred:
            return True
        if c.due_date:
            try:
                return date.fromisoformat(c.due_date) <= today
            except ValueError:
                return False
        return False

    def _visible_cards_for(self, lst: BoardList) -> list[Card] | None:
        """列的可见卡片：今日聚焦模式与搜索过滤组合；无任何过滤返回 None"""
        q = self._search_query()
        if self._today_btn.isChecked():
            today = date.today()
            cards = [c for c in lst.cards if self._is_focus_card(c, today)]
            if q:
                cards = [c for c in cards
                         if q in c.title.lower() or q in c.notes.lower()]
            return cards
        return self._filter_cards(lst, q)

    def _on_search_edited(self, _text: str) -> None:
        """重启搜索防抖计时器：输入停顿后才真正过滤"""
        self._search_timer.start()

    def _apply_filter(self, *_args) -> None:
        """今日开关 / 搜索防抖到点：单遍扫描刷新各列可见卡片与角标统计"""
        total = 0
        for lst, col in zip(self._lists, self._columns):
            visible = self._visible_cards_for(lst)
            col.set_visible_cards(visible)
            total += len(visible or [])
        self._set_today_count(total)

    def _set_today_count(self, n: int) -> None:
        self._today_btn.setText(f"⭐ 今日 {n}")

    def set_today_mode(self, on: bool) -> None:
        """供菜单栏同步：切换今日聚焦模式（toggled 会触发过滤与信号）"""
        if self._today_btn.isChecked() != on:
            self._today_btn.setChecked(on)

    def is_today_mode(self) -> bool:
        return self._today_btn.isChecked()

    def set_focusing_card(self, card_id: str | None) -> None:
        """同步"正在专注"的卡片 id 到各列卡片控件（右键菜单文案）"""
        for col in self._columns:
            col.set_focusing_card(card_id)

    def _show_export_menu(self) -> None:
        menu = QMenu(self)
        act_md = menu.addAction("Markdown（.md）")
        act_csv = menu.addAction("CSV（.csv）")
        chosen = menu.exec(self.mapToGlobal(
            QPoint(self._export_btn.x(), self._export_btn.height())))
        if chosen is act_md:
            self.signal_export.emit("md")
        elif chosen is act_csv:
            self.signal_export.emit("csv")

    def clear_search_if_active(self) -> bool:
        """清空搜索框（有内容时）；返回是否清空了搜索"""
        if self._search_edit.text():
            self._search_edit.clear()
            return True
        return False

    def search_has_focus(self) -> bool:
        """搜索框是否持有焦点（Esc 折叠链中仅此状态先清空搜索）"""
        return self._search_edit.hasFocus()

    def focus_search(self) -> None:
        """Cmd+F 聚焦搜索框并全选"""
        self._search_edit.setFocus()
        self._search_edit.selectAll()

    def _make_column(self, board_list: BoardList) -> ListColumn:
        """创建列表列并连接信号（每个列生命周期内只连一次）"""
        col = ListColumn(board_list)
        col.signal_card_edit.connect(self._on_card_edit)
        col.signal_card_done.connect(self._on_card_done)
        col.signal_card_delete.connect(self._on_card_delete)
        col.signal_card_move.connect(self.signal_card_move)
        col.signal_add_card.connect(self.signal_card_add)
        col.signal_title_changed.connect(self.signal_list_title_changed)
        col.signal_delete_list.connect(self.signal_list_delete)
        col.signal_card_pomo.connect(self.signal_card_pomo)
        col.signal_card_archive.connect(self.signal_card_archive)
        self._columns.append(col)
        return col

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

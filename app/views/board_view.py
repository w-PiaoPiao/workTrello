"""
看板视图（展开态）：Trello 风格

- 渐变背景 + 顶部工具栏（标题、统计、主题切换、折叠按钮）
- 横向滚动的列表区，每个列表是半透明圆角卡片容器
- 卡片支持跨列表拖拽（内部 QDrag）与列表内重排
- 单击卡片编辑（标题/备注/标签/截止日期/完成），悬停右上角删除
- 列表头部：双击标题重命名、悬停显示"…"菜单（重命名/删除列表）
"""

from __future__ import annotations

import bisect
import logging
import math
import zlib
from datetime import date
from functools import lru_cache

import shiboken6
from PySide6.QtCore import (
    QEvent,
    QEasingCurve,
    QMimeData,
    QPoint,
    QPointF,
    Property,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QCursor,
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
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.config import AppConfig
from app.models.board import BoardList, Card
from app.views import motion
from app.views.notes_popover import (
    hide_notes_popover,
    notes_pinned_for,
    notes_popover,
    notes_popover_hovering,
)
from app.views.theme import AppTheme
from app.views.toast import Toast

logger = logging.getLogger(__name__)

MIME_LIST = "application/x-petboard-list"
MIME_CARD = "application/x-petboard-card"

_MAX_WIDGET_H = 16777215    # QWIDGETSIZE_MAX：清除动画期固定高度用


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


def _list_accent(list_id: str) -> str:
    """按列 id 取点缀色

    不能用内置 hash()：Python 字符串 hash 每进程随机化（同一 id 三次运行
    可得到不同结果），取模后列表色点每次重启都会换色。crc32 跨进程稳定。
    """
    accents = AppConfig.LIST_ACCENTS
    return accents[zlib.crc32(list_id.encode("utf-8")) % len(accents)]


def _set_prop(widget: QWidget, name: str, value) -> None:
    """设置动态属性并在值变化时重刷该控件样式

    QSS 属性选择器（[done="true"] 等）只在重新 polish 后生效；这里只
    polish 目标控件本身，不级联整棵子树，因此可以安全地按需调用。
    """
    if widget.property(name) == value:
        return
    widget.setProperty(name, value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


# 卡片徽章底色随主题色键取值（tone 动态属性 → QSS 属性选择器）
_BADGE_TONES = ("danger", "warning", "accent", "text_secondary")


@lru_cache(maxsize=4)
def _board_qss(mode: str) -> str:
    """看板整块样式表（按主题模式缓存）

    整块只设在 BoardView 一个控件上。此前逐列/逐卡各自 setStyleSheet，
    嵌套设置会让同一批子控件被反复 re-polish：三层级联实测 300 张卡片
    主题切换 285ms，合并为单次设置后只剩一遍 polish。
    配色随主题变，故按 mode 缓存字符串，避免每次切换重新拼接。
    """
    c = AppConfig.DARK_COLORS if mode == "dark" else AppConfig.COLORS
    badge_rules = "\n".join(
        f'QLabel#cardBadge[tone="{tone}"] {{\n'
        f'    background: {c["accent_soft"]};\n'
        f'    color: {c[tone]};\n'
        f'    border-radius: 6px;\n'
        f'    padding: 2px 6px;\n'
        f'    font-size: 11px;\n'
        f'}}'
        for tone in _BADGE_TONES)
    return f"""
        /* ── 工具条 ── */
        QLabel#boardTitle {{
            font-size: 17px;
            font-weight: bold;
            color: {c['text_primary']};
            background: transparent;
        }}
        QLabel#boardStats {{
            font-size: 12px;
            color: {c['text_primary']};
            background: {c['glass']};
            border-radius: 8px;
            padding: 3px 10px;
        }}
        QPushButton#boardThemeBtn {{
            background: {c['glass']};
            border: none;
            border-radius: 17px;
            color: {c['text_primary']};
        }}
        QPushButton#boardThemeBtn:hover {{
            background: {c['glass_hover']};
        }}
        QPushButton#boardCollapseBtn {{
            background: {c['glass']};
            border: none;
            border-radius: 17px;
            font-weight: bold;
            color: {c['text_primary']};
        }}
        QPushButton#boardCollapseBtn:hover {{
            background: {c['glass_hover']};
        }}
        QPushButton#boardToolBtn {{
            background: {c['glass']};
            border: none;
            border-radius: 9px;
            padding: 5px 10px;
            font-size: 12px;
            color: {c['text_primary']};
        }}
        QPushButton#boardToolBtn:hover {{
            background: {c['glass_hover']};
        }}
        QPushButton#boardToolBtn:checked {{
            background: {c['accent']};
            color: white;
        }}
        /* 搜索框：与工具按钮同一玻璃面，不再是一整条纯白 */
        QLineEdit#boardSearch {{
            background: {c['glass']};
            border: 1px solid transparent;
            border-radius: 9px;
            padding: 6px 10px;
            color: {c['text_primary']};
        }}
        /* 聚焦态必须自设：id 选择器优先级高于全局 QLineEdit:focus，
           不写这条则聚焦边框被吃掉、看不出光标在框内 */
        QLineEdit#boardSearch:focus {{
            background: {c['glass_hover']};
            border: 1.5px solid {c['accent']};
        }}
        QPushButton#addBoardBtn {{
            background: transparent;
            color: {c['text_secondary']};
            border: 1.5px dashed {c['border']};
            border-radius: 9px;
            padding: 7px;
            font-size: 12px;
        }}
        QPushButton#addBoardBtn:hover {{
            background: {c['accent_soft']};
            color: {c['accent']};
            border: 1.5px dashed {c['accent']};
        }}
        QLabel#emptyBoardHint {{
            color: {c['text_secondary']};
            font-size: 15px;
            background: transparent;
        }}

        /* ── 滚动区承载体 ── */
        QWidget#listsHost, QWidget#cardsHost {{
            background: transparent;
        }}
        QScrollArea#boardScroll, QScrollArea#columnScroll {{
            background: transparent;
        }}

        /* ── 列表列 ── */
        QFrame#listColumn {{
            background: {c['bg_panel']};
            border: 1px solid {c['border']};
            border-radius: 14px;
        }}
        QFrame#listColumn[drop="true"] {{
            border: 2px solid {c['accent']};
        }}
        QFrame#dropIndicator {{
            background: {c['accent']};
            border-radius: 2px;
        }}
        QLabel#listTitle {{
            font-size: 14px;
            font-weight: bold;
            color: {c['text_primary']};
            background: transparent;
        }}
        QLabel#listCount {{
            color: {c['text_secondary']};
            font-size: 11px;
            background: {c['mask']};
            border-radius: 8px;
            padding: 1px 7px;
        }}
        QLabel#columnHint {{
            color: {c['text_disabled']};
            font-size: 12px;
            background: transparent;
        }}
        QPushButton#listCollapseBtn {{
            background: transparent;
            color: {c['text_secondary']};
            border: none;
            border-radius: 12px;
            font-size: 13px;
            padding: 0;
        }}
        QPushButton#listCollapseBtn[collapsed="true"] {{
            color: {c['text_primary']};
        }}
        QPushButton#listCollapseBtn:hover {{
            background: {c['mask_hover']};
            color: {c['text_primary']};
        }}
        QPushButton#headerMenuBtn {{
            background: transparent;
            border: none;
        }}
        QLineEdit#renameEdit {{
            background: {c['bg_card']};
            font-size: 14px;
            font-weight: bold;
            color: {c['text_primary']};
            padding: 0 4px;
            border-radius: 6px;
        }}

        /* ── 卡片 ── */
        QFrame#cardFrame {{
            background: {c['bg_card']};
            border: 1px solid {c['border']};
            border-radius: 10px;
        }}
        QFrame#cardFrame:hover {{
            border: 1px solid {c['accent']};
        }}
        QLabel#cardTitle {{
            font-size: 13px;
            font-weight: 500;
            color: {c['text_primary']};
            background: transparent;
            border: none;
        }}
        QLabel#cardTitle[done="true"] {{
            color: {c['text_secondary']};
            text-decoration: line-through;
        }}
        QPushButton#cardDeleteBtn {{
            background: {c['mask']};
            color: {c['text_primary']};
            border: none;
            border-radius: {AppConfig.CARD_DELETE_BTN_H // 2}px;
            font-size: 10px;
            font-weight: bold;
            padding: 0;
        }}
        QPushButton#cardDeleteBtn:hover {{
            background: {c['danger']};
            color: white;
        }}
        {badge_rules}
    """


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


class _CardCheckButton(QPushButton):
    """自绘勾选框（替代 ☐/☑ 字形，跨平台渲染一致）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFlat(True)
        self.setFixedSize(20, 20)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("点击切换完成状态")
        self._done = False

    def set_done(self, done: bool) -> None:
        if self._done != done:
            self._done = done
            self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        c = AppTheme.colors()
        box = QRect((self.width() - 16) // 2, (self.height() - 16) // 2, 16, 16)
        radius = 4.5
        if self._done:
            # 完成：主题色填充 + 白色对勾
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(c["success"]))
            painter.drawRoundedRect(box, radius, radius)
            pen = QPen(QColor("#FFFFFF"), 1.8)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            path = QPainterPath()
            path.moveTo(box.left() + 3.6, box.top() + 8.2)
            path.lineTo(box.left() + 7.0, box.top() + 11.4)
            path.lineTo(box.left() + 12.6, box.top() + 4.6)
            painter.drawPath(path)
        else:
            # 未完成：圆角方框，悬停变主题色
            color = QColor(c["accent"] if self.underMouse()
                           else c["text_disabled"])
            painter.setPen(QPen(color, 1.5))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(box, radius, radius)
        painter.end()


class CardWidget(QFrame):
    """看板卡片"""

    signal_edit_requested = Signal(object)      # card
    signal_done_toggled = Signal(str, bool)     # card_id, done
    signal_delete_requested = Signal(str)       # card_id
    signal_card_pomo = Signal(str)              # card_id
    signal_card_archive = Signal(str)           # card_id
    signal_card_star = Signal(str)              # card_id（星标 toggle）
    signal_label_clicked = Signal(str)          # label key（点色条过滤）

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
        self._more_badge: QLabel | None = None     # 装不下的徽章用 "…" 提示
        self._fit_state: tuple = ()                # 徽章显示组合缓存（避免重复重排）
        self._fit_width: int | None = None         # 上次徽章取舍时的卡片宽度
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
        # 今日聚焦开关放首位：星标是"加入今日"的唯一入口，此前必须打开
        # 编辑对话框才能勾选，是规划链路上最贵的操作
        act_star = menu.addAction(
            "☆ 移出今日" if self._card.starred else "⭐ 加入今日")
        menu.addSeparator()
        if self._card.id == self._focusing_id:
            act_pomo = menu.addAction("⏹ 停止专注")
        else:
            act_pomo = menu.addAction("▶ 开始专注 25 分钟")
        menu.addSeparator()
        act_archive = menu.addAction("归档")
        chosen = menu.exec(self.mapToGlobal(pos))
        menu.deleteLater()   # exec 返回即弃用：挂在卡片控件上会随卡片累积
        if chosen is act_star:
            self.signal_card_star.emit(self._card.id)
        elif chosen is act_pomo:
            self.signal_card_pomo.emit(self._card.id)
        elif chosen is act_archive:
            self.signal_card_archive.emit(self._card.id)

    def card(self) -> Card:
        return self._card

    def minimumSizeHint(self):
        """最小宽度不吃徽章行：QLabel 的最小宽=文本宽，若把徽章算进来，
        "P1+逾期+重复+备注+番茄"这类组合会把卡片撑到 316px，而列内可视宽
        只有 258px——列横向滚动条是关闭的，超出部分直接被裁掉。
        宽度由列视图给足，显示哪几个徽章交给 _fit_meta_badges 按实宽取舍。
        """
        base = super().minimumSizeHint()
        return QSize(0, base.height())

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
        """卡片内容指纹，用于跳过未变化卡片的重建

        必须覆盖 rebuild() 实际渲染的每个字段：遗漏字段曾让优先级/重复/
        番茄数变化时不重建，卡片一直显示旧徽章，直到其他字段变化才连带
        刷新。tests/test_board_view.py 有字段覆盖断言兜底。
        """
        c = self._card
        return (c.title, c.done, c.due_date, bool(c.notes), tuple(c.labels),
                c.priority, c.repeat, c.pomodoros)

    def reapply_style(self) -> None:
        """主题切换后同步卡片内部状态，不重建子控件（保留悬停状态）

        配色由看板级样式表统一下发（见 _board_qss），这里只处理与配色无关
        的状态：删除按钮显隐、勾选框与标题完成态。
        """
        btn = self._delete_btn
        if btn is not None and not self._hovered and not btn.underMouse():
            btn.hide()
        self._style_check()
        self._style_title()

    def _style_check(self) -> None:
        """勾选框状态同步（自绘控件，done 变化时重绘）"""
        if self._check_btn is None:
            return
        self._check_btn.set_done(self._card.done)

    def _style_title(self) -> None:
        """标题完成态：动态属性驱动 QSS 选择器（[done="true"]）

        完成态用 text_secondary（而非更浅的 disabled），保证白卡上
        删除线文字仍可读（对比度 ≥ 4.5:1）。
        """
        if self._title_label is None:
            return
        _set_prop(self._title_label, "done", bool(self._card.done))

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
        self._more_badge = None
        self._fit_state = ()
        self._fit_width = None   # 徽章重建后宽度取舍必须重算

        card = self._card
        # 配色统一由 BoardView 的整块样式表下发（#cardFrame 等选择器），
        # 此处只登记 objectName / 动态属性，不再设局部样式表
        self.setObjectName("cardFrame")

        root = QVBoxLayout(self)
        # 左缘标签色条（paintEvent 绘制）：每条 4px，最多 4 条；留出横向空间不与标题重叠
        stripe_n = min(len(card.labels), 4)
        root.setContentsMargins(max(10, stripe_n * 4 + 7), 8, 10, 8)
        root.setSpacing(6)
        if card.labels:
            names = [AppConfig.LABEL_NAMES.get(k, k) for k in card.labels]
            self.setToolTip("标签：" + "、".join(names))

        # 标题行（勾选 + 文本）
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        check = _CardCheckButton()
        check.set_done(card.done)
        self._check_btn = check
        self._style_check()
        check.clicked.connect(
            lambda: self.signal_done_toggled.emit(self._card.id,
                                                  not self._card.done))
        title_row.addWidget(check)

        title = QLabel(card.title)
        title.setObjectName("cardTitle")
        title.setWordWrap(True)
        self._title_label = title
        self._style_title()
        title_row.addWidget(title, 1)
        root.addLayout(title_row)

        # 右上角删除按钮：仅作 child 绝对定位（不占布局，不挤压标题），悬停卡片才出现
        self._delete_btn = QPushButton("✕", self)
        self._delete_btn.setObjectName("cardDeleteBtn")
        self._delete_btn.setFixedSize(AppConfig.CARD_DELETE_BTN_H,
                                      AppConfig.CARD_DELETE_BTN_H)
        self._delete_btn.setCursor(Qt.PointingHandCursor)
        self._delete_btn.setToolTip("删除卡片")
        self._delete_btn.clicked.connect(
            lambda: self.signal_delete_requested.emit(self._card.id))
        self._delete_btn.hide()

        # 底部信息行（截止日期 / 备注图标）
        meta_items: list[tuple[str, str, bool]] = []    # (文本, 主题色键, 是否备注徽章)
        if card.priority:
            mark = AppConfig.PRIORITY_MARKS.get(card.priority, "")
            if mark:
                meta_items.append(
                    (mark, {1: "danger", 2: "warning",
                            3: "accent"}.get(card.priority, "accent"), False))
        if card.due_date:
            text, overdue = _fmt_due(card.due_date)
            meta_items.append((text, "danger" if overdue else "accent", False))
        if card.repeat != "never":
            meta_items.append((f"🔁 {AppConfig.REPEAT_NAMES.get(card.repeat, '')}",
                               "text_secondary", False))
        if card.notes:
            meta_items.append(("≡ 有备注", "text_secondary", True))
        if card.pomodoros:
            meta_items.append((f"🍅 ×{card.pomodoros}", "text_secondary", False))

        if meta_items:
            meta_row = QHBoxLayout()
            meta_row.setSpacing(6)

            def make_badge(text: str, tone: str) -> QLabel:
                # tone 动态属性 → QSS 属性选择器（配色随主题，见 _board_qss）
                badge = QLabel(text)
                badge.setObjectName("cardBadge")
                badge.setProperty("tone", tone)
                self._meta_badges.append((badge, tone))
                return badge

            # 全部徽章先建出来，实际显示几个由 _fit_meta_badges() 按卡片
            # 真实宽度决定（"P1+逾期+重复+备注"最坏组合约 281px，已超出
            # 列内可用宽约 229px，写死数量仍会溢出裁切）
            for text, key, is_notes in meta_items:
                badge = make_badge(text, key)
                if is_notes:
                    # 备注徽章：悬停弹备注全文预览，点击固定展示（本卡事件过滤处理）
                    # 不设原生 tooltip：悬停约 700ms 后系统会再弹一个提示窗压在
                    # 自绘浮层上造成双重叠字；「点击固定」提示在浮层内呈现
                    self._notes_badge = badge
                    badge.setCursor(Qt.PointingHandCursor)
                    badge.installEventFilter(self)
                meta_row.addWidget(badge)
            # 折叠指示：装不下的徽章数用 "…" 提示。不进 _meta_badges
            # （它是"真实徽章"清单，备注预览/语义色断言都按它遍历）
            self._more_badge = QLabel("…")
            self._more_badge.setObjectName("cardBadge")
            self._more_badge.setProperty("tone", "text_secondary")
            self._more_badge.setVisible(False)
            meta_row.addWidget(self._more_badge)
            meta_row.addStretch(1)
            root.addLayout(meta_row)
        self._fit_meta_badges()

        # 底部弹性：防止上面的控件（如徽章）被布局纵向拉伸满整个卡片
        root.addStretch(1)

        self._fingerprint = self._content_fingerprint()
        # 重建后删除按钮默认隐藏；悬停中则恢复显示
        if self._hovered and self._delete_btn is not None:
            self._delete_btn.show()
            self._delete_btn.raise_()

    def paintEvent(self, event) -> None:
        """左缘标签色条：贴卡片左边缘的竖向色条，明显且不占布局行"""
        super().paintEvent(event)
        labels = self._card.labels[:4]
        if not labels:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # 裁剪到圆角轮廓内，色条端头跟随卡片圆角（与 #cardFrame 的 10px 对齐）
        clip = QPainterPath()
        clip.addRoundedRect(1, 1, self.width() - 2, self.height() - 2, 10, 10)
        painter.setClipPath(clip)
        x = 1.0
        for key in labels:
            # 用标签的饱和前景色（fg）而非浅底色，保证色条醒目；
            # 深浅主题下同一饱和色都清晰
            _, fg = AppConfig.LABEL_COLORS.get(key, ("#E5E7EB", "#374151"))
            painter.fillRect(QRectF(x, 0, 4, self.height()), QColor(fg))
            x += 4.0
        painter.end()

    def _fit_meta_badges(self) -> None:
        """按卡片真实宽度决定显示哪几个徽章，装不下折成 "…"

        写死"最多 4 个"不够：最坏组合（P1 + 逾期 + 重复 + 备注）实测约
        281px，超出列内可用宽（272 列宽下卡片约 254px，去掉色条/内边距
        约 229px），徽章会顶破卡片右缘被裁。这里按实际宽度取舍，
        窗口/列宽变化后由 resizeEvent 重新计算。
        """
        if self._more_badge is None:
            return
        # 宽度未变直接跳过：窗口缩放时每帧每卡都进来（resizeEvent 驱动），
        # 最贵的 sizeHint 计算必须挡在短路之前，否则徽章文本不变也每帧
        # 对全部徽章做 fontMetrics 查询；rebuild 会重置 _fit_width
        if self.width() == self._fit_width:
            return
        m = self.layout().contentsMargins() if self.layout() is not None else None
        avail = self.width() - (m.left() + m.right() if m is not None else 0) \
            - self.frameWidth() * 2
        if avail <= 0:      # 尚未布局（新建卡片），宽度确定后由 resizeEvent 补算
            return
        spacing = 6
        widths = [b.sizeHint().width() for b, _ in self._meta_badges]
        n = len(widths)

        def total(k: int) -> int:
            return sum(widths[:k]) + spacing * max(0, k - 1)

        cap = AppConfig.CARD_META_BADGE_MAX
        if n <= cap and total(n) <= avail:
            visible, more = n, False
        else:
            ellipsis_w = self._more_badge.sizeHint().width()
            visible = 0
            for k in range(min(n, cap), 0, -1):
                if total(k) + spacing + ellipsis_w <= avail:
                    visible = k
                    break
            more = visible < n

        state = (visible, more)
        self._fit_width = self.width()
        if state == self._fit_state:
            return
        self._fit_state = state
        for i, (badge, _tone) in enumerate(self._meta_badges):
            badge.setVisible(i < visible)
        self._more_badge.setVisible(more)

    def resizeEvent(self, event) -> None:
        """跟随卡片把删除按钮钉在右上角；顺带按新宽度重排徽章"""
        super().resizeEvent(event)
        if self._delete_btn is not None:
            self._delete_btn.move(
                self.width() - self._delete_btn.width() - 6, 6)
        self._fit_meta_badges()

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
            self._show_delete_btn()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        if self._delete_btn is not None and not self._delete_btn.underMouse():
            self._delete_btn.hide()
        super().leaveEvent(event)

    def _show_delete_btn(self) -> None:
        """显示删除按钮（淡入；已有淡入在播或动画开关关闭则直接显示）

        只在"新出现"时起播：enterEvent 在快速划过多张卡时会反复触发，
        每次都重启动画会让按钮一直停在半透明。
        """
        btn = self._delete_btn
        if not btn.isVisible():
            btn.show()
            motion.fade_in(btn, AppConfig.HOVER_ANIM_MS)
        btn.raise_()

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
                key = self._label_key_at(event.position().toPoint())
                if key is not None:
                    # 点左缘色条 = 按该标签过滤看板（而非打开编辑）
                    self.signal_label_clicked.emit(key)
                else:
                    self.signal_edit_requested.emit(self._card)
        super().mouseReleaseEvent(event)

    def _label_key_at(self, pos: QPoint) -> str | None:
        """点击位置对应的标签色 key（左缘色条区；不在则 None）

        与 paintEvent 的色条几何保持一致：从 x=1 起每条 4px，最多 4 条。
        """
        labels = self._card.labels[:4]
        if not labels:
            return None
        x = pos.x()
        if x < 1 or x >= 1 + 4 * len(labels):
            return None
        return labels[int((x - 1) // 4)]

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


class _TitleLabel(QLabel):
    """列表标题标签：双击进入重命名（子类覆写，替代实例 monkeypatch）"""

    def __init__(self, text: str, header: "ListHeader", parent=None):
        super().__init__(text, parent)
        self._header = header

    def mouseDoubleClickEvent(self, event) -> None:
        self._header._start_rename(event)


class ListHeader(QWidget):
    """列表头部：标题（双击重命名）+ 计数 + "⋯"菜单（重命名/删除）"""

    signal_title_changed = Signal(str, str)   # list_id, new_title
    signal_delete_requested = Signal(str)     # list_id

    def __init__(self, board_list: BoardList, parent=None):
        super().__init__(parent)
        self._lst = board_list
        self._drag_press_pos: QPoint | None = None   # 整列拖拽起点（None=未按下）

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 10, 6)
        layout.setSpacing(6)

        accent = _list_accent(board_list.id)

        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background: {accent}; border-radius: 4px;")
        layout.addWidget(dot)

        self._title_label = _TitleLabel(board_list.title, self)
        self._title_label.setObjectName("listTitle")
        layout.addWidget(self._title_label, 1)

        self._count_label = QLabel()
        self._count_label.setObjectName("listCount")
        layout.addWidget(self._count_label)

        self._collapse_btn = QPushButton("▾")
        self._collapse_btn.setObjectName("listCollapseBtn")
        self._collapse_btn.setFixedSize(24, 24)
        self._collapse_btn.setCursor(Qt.PointingHandCursor)
        self._collapse_btn.setToolTip("折叠 / 展开列表")
        self._collapse_btn.clicked.connect(self._on_collapse_clicked)
        layout.addWidget(self._collapse_btn)

        self._menu_btn = _HeaderMenuButton(self)
        self._menu_btn.setToolTip("列表操作")
        self._menu_btn.clicked.connect(self._show_menu)
        layout.addWidget(self._menu_btn)
        # 不隐藏、只"幽灵化"：布局空间常驻，悬停才点亮，避免列头高度闪动

        # 列表操作菜单
        self._menu = QMenu(self)
        self._act_rename = QAction("重命名", self._menu)
        self._act_delete = QAction("删除列表", self._menu)
        self._menu.addAction(self._act_rename)
        self._menu.addAction(self._act_delete)
        self._act_rename.triggered.connect(self._start_rename)
        self._act_delete.triggered.connect(
            lambda: self.signal_delete_requested.emit(self._lst.id))
        # 菜单关闭后按光标实际位置决定是否保持点亮（避免菜单开着时误熄灭）
        self._menu.aboutToHide.connect(
            lambda: self._menu_btn.set_active(self.underMouse()))

    def _show_menu(self) -> None:
        self._menu.exec(self._menu_btn.mapToGlobal(
            QPoint(0, self._menu_btn.height() + 2)))

    def _on_collapse_clicked(self) -> None:
        col = self.parent()
        if isinstance(col, ListColumn):
            col.toggle_collapsed()

    def enterEvent(self, event) -> None:
        """悬停列头点亮"⋯"菜单按钮"""
        self._menu_btn.set_active(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        # 菜单用 exec() 弹出（阻塞）期间光标已离开列头，此时不能熄灭"⋯"，
        # 否则菜单还开着按钮却灭了。QMenu 无 isOpen()（Qt6 只此一处判据是
        # isVisible），调用不存在的 API 会抛 AttributeError——而展开列时列头
        # 会因位置变化收到 Leave，异常直接从 setVisible 逸出并截断状态同步，
        # 表现为"展开后添加卡片按钮不见了"。
        if not self._menu.isVisible():
            self._menu_btn.set_active(False)
        super().leaveEvent(event)

    def set_collapsed_mark(self, collapsed: bool) -> None:
        """折叠态箭头：▸ 折叠 / ▾ 展开；折叠态加深颜色便于发现展开入口

        颜色由 QSS 的动态属性选择器 [collapsed="true"] 决定，不再逐次
        重设样式表（列头在展开/折叠时会同步收到 Leave，样式表重设会拖长
        这条同步路径）。
        """
        self._collapse_btn.setText("▸" if collapsed else "▾")
        _set_prop(self._collapse_btn, "collapsed", bool(collapsed))

    def update_count(self, n: int) -> None:
        self._count_label.setText(str(n))

    def set_list(self, board_list: BoardList) -> None:
        """增量刷新：重指向模型对象并同步标题文本"""
        self._lst = board_list
        self._title_label.setText(board_list.title)

    def reapply_theme(self) -> None:
        """主题切换后同步头部状态（配色由看板级样式表统一下发）"""
        col = self.parent()
        self.set_collapsed_mark(
            col.is_collapsed() if isinstance(col, ListColumn) else False)
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
        _sync_rename_filter(self)   # 编辑器已存在 → 装上点击守卫
        edit.show()
        edit.setFocus()

    def eventFilter(self, obj, event) -> bool:
        # Esc 取消重命名（窗口级 QShortcut 命中前的兜底路径）
        if (event.type() == QEvent.KeyPress and obj is _ACTIVE_RENAME
                and event.key() == Qt.Key_Escape):
            finish_active_rename(cancel=True)
            return True
        return super().eventFilter(obj, event)

    # ── 整列拖拽（按住列表头拖动重排；位移超阈值才生效，单击/双击不受影响）──

    def mousePressEvent(self, event) -> None:
        if (event.button() == Qt.LeftButton and _ACTIVE_RENAME is None):
            self._drag_press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        col = self.parent()
        if (self._drag_press_pos is not None
                and event.buttons() & Qt.LeftButton
                and isinstance(col, ListColumn)
                and not col.is_filtered()
                and (event.position().toPoint() - self._drag_press_pos)
                .manhattanLength() > AppConfig.LIST_DRAG_THRESHOLD):
            self._drag_press_pos = None      # 只触发一次
            self._start_list_drag()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        # 折叠列：单击头部任意处（按住未拖动）即展开，让"▸"有够大的命中区
        if (event.button() == Qt.LeftButton
                and self._drag_press_pos is not None
                and _ACTIVE_RENAME is None):
            col = self.parent()
            if (isinstance(col, ListColumn) and col.is_collapsed()
                    and self.childAt(event.position().toPoint())
                    is not self._title_label):
                col.toggle_collapsed()
        self._drag_press_pos = None
        super().mouseReleaseEvent(event)

    def _start_list_drag(self) -> None:
        """启动整列拖拽：列头截图作拖影，MIME_LIST 携带列表 id"""
        col = self.parent()
        if not isinstance(col, ListColumn):
            return
        mime = QMimeData()
        mime.setData(MIME_LIST, self._lst.id.encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        pixmap = self.grab()
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))
        drag.exec(Qt.MoveAction)


class _RenameEdit(QLineEdit):
    """列表标题重命名编辑器（全局同时只存在一个，见 _ACTIVE_RENAME）"""

    def __init__(self, header: "ListHeader"):
        super().__init__(header._lst.title, header._title_label)
        self._header = header
        self.setObjectName("renameEdit")
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
        _sync_rename_filter(self)   # 编辑器已关闭 → 卸掉点击守卫
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
        _sync_rename_filter(self)


_ACTIVE_RENAME: "_RenameEdit | None" = None


def _sync_rename_filter(widget) -> None:
    """把"是否有重命名编辑器"的状态同步给所在 BoardView（安装/卸载过滤器）"""
    p = widget.parent()
    while p is not None:
        if isinstance(p, BoardView):
            p._attach_rename_filter()
            return
        p = p.parent()


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
    """列表头部自绘"⋯"按钮（无文本，避免样式表被全局 QPushButton 规则改写）

    幽灵模式：布局位置常驻（不改变列头高度），仅在列头悬停时绘制圆点，
    未点亮时对鼠标穿透（点击落到列头自身）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = False
        self.setFixedSize(24, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("headerMenuBtn")
        self.reapply()

    def set_active(self, on: bool) -> None:
        if on == self._active:
            return
        self._active = on
        # 未点亮时穿透鼠标，避免"看不见却挡住列头拖拽/点击"
        self.setAttribute(Qt.WA_TransparentForMouseEvents, not on)
        self.update()

    def paintEvent(self, event) -> None:
        if not self._active:
            return
        c = AppTheme.colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        hovered = self.underMouse() or self.isDown()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(c["text_secondary"])
                         if not hovered else QColor(c["text_primary"]))
        y = self.height() // 2
        x = self.width() // 2 - 1
        for i in (-1, 0, 1):
            painter.drawEllipse(x, y - 1 + i * 4, 3, 3)
        painter.end()

    def reapply(self) -> None:
        """配色由看板级样式表下发（#headerMenuBtn），此处仅重绘"""
        self.update()

    def mouseDoubleClickEvent(self, event) -> None:
        header = self.parent()
        if isinstance(header, ListHeader):
            header._start_rename(event)
            return
        super().mouseDoubleClickEvent(event)


class _ThemeToggleButton(QPushButton):
    """自绘 日/月 图标的主题切换按钮（🌙/☀️ emoji 在部分平台缺字形，改矢量绘制）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode = "light"

    def set_mode(self, mode: str) -> None:
        if mode != self._mode:
            self._mode = mode
            self.update()

    # 月亮两段路径按控件尺寸缓存：paintEvent 每次重建 QPainterPath 纯浪费
    _moon_path_cache: dict[tuple[int, int], tuple[QPainterPath, QPainterPath]] = {}

    def _moon_paths(self) -> tuple[QPainterPath, QPainterPath]:
        key = (self.width(), self.height())
        paths = self._moon_path_cache.get(key)
        if paths is None:
            cx, cy = key[0] / 2, key[1] / 2
            full = QPainterPath()
            full.addEllipse(cx - 5.5, cy - 5.5, 11.0, 11.0)
            cut = QPainterPath()
            cut.addEllipse(cx - 1.5, cy - 8.0, 11.0, 11.0)
            paths = (full, cut)
            self._moon_path_cache[key] = paths
        return paths

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        c = AppTheme.colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        glyph = QColor(c["text_primary"])
        if self._mode == "light":
            # 浅色态显示月亮（点击切深色）
            full, cut = self._moon_paths()
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
    signal_card_star = Signal(str)             # card_id（星标 toggle）
    signal_label_clicked = Signal(str)         # label key（点色条过滤）
    signal_list_move = Signal(str, str, bool)  # moved_list_id, target_list_id, insert_before
    signal_collapsed_changed = Signal(str, bool)  # list_id, collapsed

    COLLAPSED_HEIGHT = 52        # 折叠态高度（仅剩标题栏）

    def __init__(self, board_list: BoardList, parent=None):
        super().__init__(parent)
        self._lst = board_list
        self._card_widgets: list[CardWidget] = []
        self._hint: QLabel | None = None
        self._collapsed = False
        self._visible_cards: list[Card] | None = None   # None=显示全部（过滤态为子集）
        self._collapse_anim: QPropertyAnimation | None = None
        self._anim_height = float(self.COLLAPSED_HEIGHT)  # 折叠过渡的高度插值目标
        # 列几何指纹：(卡片 id 序, 各卡内容指纹, 折叠态)。未变化时
        # refresh_cards 跳过 updateGeometry 与双重列高同步
        self._geom_key: tuple | None = None
        # 拖拽落点快照（host 内 y 中点，升序）：dragEnter 时重建
        self._drop_mids: list[int] | None = None
        # 父链上的看板横向滚动区缓存（构造后父链稳定）
        self._board_scroll_cache: QScrollArea | None = None
        # 以下三者在构造后半段才建立；sizeHint 可能在构造途中被查询，
        # 先占位避免 _content_height 取到未定义属性
        self._header: ListHeader | None = None
        self._scroll: QScrollArea | None = None
        self._add_btn: AddCardButton | None = None
        self.setAcceptDrops(True)

        self.setObjectName("listColumn")

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 4, 6, 8)
        root.setSpacing(6)

        self._header = ListHeader(board_list, self)
        self._header.signal_title_changed.connect(self.signal_title_changed)
        self._header.signal_delete_requested.connect(self.signal_delete_list)
        root.addWidget(self._header)

        # 卡片滚动区
        self._scroll = QScrollArea()
        self._scroll.setObjectName("columnScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.viewport().setAutoFillBackground(False)
        self._cards_host = QWidget()
        self._cards_host.setObjectName("cardsHost")
        self._cards_layout = QVBoxLayout(self._cards_host)
        self._cards_layout.setContentsMargins(2, 2, 2, 2)
        self._cards_layout.setSpacing(8)
        self._cards_layout.addStretch(1)
        self._scroll.setWidget(self._cards_host)
        root.addWidget(self._scroll, 1)

        # 卡片拖拽插入指示线（拖拽期间临时插入布局对应间隙显示）
        self._drop_indicator = QFrame()
        self._drop_indicator.setObjectName("dropIndicator")
        self._drop_indicator.setFixedHeight(3)
        self._drop_indicator.hide()

        # 拖拽到视口边缘的自动滚动（横向看板区 + 本列纵向）
        self._drag_hovering = False
        self._auto_scroll_timer = QTimer(self)
        self._auto_scroll_timer.setInterval(AppConfig.DRAG_AUTO_SCROLL_MS)
        self._auto_scroll_timer.timeout.connect(self._auto_scroll_tick)

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
        cw.signal_card_star.connect(self.signal_card_star)
        cw.signal_label_clicked.connect(self.signal_label_clicked)
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
        appeared: list[CardWidget] = []
        for card in cards:
            cw = reusable.pop(card.id, None)
            if cw is None:
                cw = self._make_card_widget(card)
                appeared.append(cw)
            else:
                cw.update_from_model(card)
            ordered.append(cw)
        self._dispose_card_widgets(list(reusable.values()))
        # 顺序未变（勾选完成/编辑保存等单卡变更）→ 布局无需重插；
        # 仅增删/移动造成顺序变化时才 remove+insert 保序
        if [cw.card().id for cw in self._card_widgets] != [c.id for c in cards]:
            for i, cw in enumerate(ordered):
                self._cards_layout.removeWidget(cw)
                self._cards_layout.insertWidget(i, cw)
        self._card_widgets = ordered

        # 新增卡片淡入：只在数量可控时播放，批量出现（搜索/过滤/导入）直接显示
        if 0 < len(appeared) <= AppConfig.ANIM_BATCH_LIMIT:
            for cw in appeared:
                motion.fade_in(cw, AppConfig.CARD_ANIM_MS)

        # 空列提示
        if not cards:
            if self._hint is None:
                hint_text = ("没有匹配的卡片" if self._visible_cards is not None
                             else "还没有卡片，点击下方添加")
                hint = QLabel(hint_text)
                hint.setObjectName("columnHint")
                hint.setAlignment(Qt.AlignCenter)
                hint.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                self._hint = hint
                self._cards_layout.insertWidget(0, hint)
        elif self._hint is not None:
            self._cards_layout.removeWidget(self._hint)
            self._hint.deleteLater()
            self._hint = None

        self._header.update_count(len(cards))
        # 列高随内容收缩：卡片增删都要重算 sizeHint，否则列高停在旧值。
        # 但仅当几何指纹变化时才需要——单卡勾选/编辑不改 id 序与内容指纹
        # （指纹覆盖 rebuild 渲染的全部字段），此时跳过 updateGeometry 与
        # 双重列高同步，否则一次变更全板每列白算两遍 sizeHint
        geom_key = (tuple(cw.card().id for cw in ordered),
                    tuple(cw._fingerprint for cw in ordered),
                    self._collapsed)
        if geom_key != self._geom_key:
            self._geom_key = geom_key
            self.updateGeometry()
            self._sync_height_to_content()
            self._sync_height_deferred()

    def _dispose_card_widgets(self, widgets: list[CardWidget]) -> None:
        """移除卡片控件：少量走淡出（延后销毁），批量直接销毁

        退场动画必须延后 deleteLater——立刻销毁会让动画还没播控件就没了。
        先脱离布局再原地淡出：脱离布局的控件保持最后几何位置继续绘制，
        因此呈现为"原地消失"而不是跳位。
        """
        animate = (motion.enabled() and 0 < len(widgets)
                   <= AppConfig.ANIM_BATCH_LIMIT and self.isVisible())
        for cw in widgets:
            self._cards_layout.removeWidget(cw)
            if animate:
                cw.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                cw.setParent(self._cards_host)   # 同父调用不改几何，仅确保归属
                motion.fade_out(cw, AppConfig.CARD_EXIT_ANIM_MS,
                                on_finished=cw.deleteLater)
            else:
                cw.setParent(None)
                cw.deleteLater()

    # ── 样式 ──────────────────────────────────────────────

    def reapply_frame_style(self) -> None:
        """配色由看板级样式表下发；此处只同步拖放高亮属性"""
        self._set_drop_highlight(self.property("drop") is True)

    def minimumSizeHint(self):
        """下界统一为折叠高度：展开态若钉死 200，短列就会留出空白"""
        return QSize(AppConfig.LIST_WIDTH, self.COLLAPSED_HEIGHT)

    def sizeHint(self):
        """展开态返回内容自然高度，折叠态仅剩标题栏

        配合布局项的对齐（Qt.AlignTop）让列"多高就多高"：卡片少时列下方
        直接露出看板背景，不再是拉满整屏的空面板；内容超过可视高度时由
        Qt 按可用空间自然钳住（列内滚动接管溢出）。

        这里刻意只报内容高度、不按可用高度预先截断：sizeHint 一旦依赖
        父级高度，父布局在窗口展开动画中会缓存旧值，长列会停在动画期的
        矮高度上再也不长（实测停在 408px 而非 568px）。
        """
        return QSize(AppConfig.LIST_WIDTH,
                     self.COLLAPSED_HEIGHT if self._collapsed
                     else self._content_height())

    def _content_height(self) -> int:
        """列内容自然高度（列头 + 卡片区 + 添加按钮 + 布局间距 + 边框）

        不能直接用 layout().sizeHint()：它以 QScrollArea 的 minimumSizeHint
        (88px) 为地板，对"卡片区实际只需 42px"的短列会多算出一大截空白。
        故按各部件显式求和，卡片区取 _cards_host 的自然高度；实测把列压到
        该高度后 scroll 仍能正常渲染（88 只是软下限，布局可下压）。

        边框必须计入：QSS 的 1px 边框占掉上下各 1px，不算进来则"恰好装下
        内容"的列会短 2px，触发一根多余的纵向滚动条——滚动条又把卡片挤窄，
        徽章行随之被裁。
        """
        lay = self.layout()
        if (lay is None or self._cards_host is None or self._header is None
                or self._add_btn is None):
            return self.COLLAPSED_HEIGHT
        m = lay.contentsMargins()
        return (self.frameWidth() * 2
                + m.top() + m.bottom()
                + self._header.sizeHint().height()
                + self._cards_host.sizeHint().height()
                + self._add_btn.sizeHint().height()
                + lay.spacing() * 2)

    def _expanded_target_height(self) -> int:
        """展开态目标高度 = min(内容自然高度, 列表区可用高度)"""
        return max(self.COLLAPSED_HEIGHT,
                   min(self._expanded_height(), self._content_height()))

    def _expanded_height(self) -> int:
        """展开态可用上限：列表区可视高度（折叠动画的展开终点也用它）"""
        host = self.parentWidget()
        lay = host.layout() if host is not None else None
        if host is None or lay is None:
            return max(self.height(), self.COLLAPSED_HEIGHT)
        m = lay.contentsMargins()
        return max(self.COLLAPSED_HEIGHT,
                   host.height() - m.top() - m.bottom())

    def _sync_height_to_content(self) -> None:
        """把列高同步到内容应有的高度（卡内容变化后补救）

        Qt 在 sizeHint 变小时不会主动重排父布局中的本列项：实测内容已降到
        209 而父布局项几何仍停在 233，列尾卡片被下方「+ 添加卡片」压住并冒
        出纵向滚动条，且给本列 updateGeometry / 父布局 invalidate / activate
        / 重投 LayoutRequest 都无效——只有下一次结构性变化（加入新卡）才连带
        修正。resize() 是实测最小可靠原语，本列无 resizeEvent 副作用。

        目标值取 _expanded_target_height()：与布局对长列的钳制一致（实测长列
        两者同为 558），故长列不会反复触发；折叠态/过渡动画中由各自的状态机
        接管高度，不在此干预。
        """
        if self._collapsed or self._collapse_anim is not None:
            return
        if not self.isVisible():
            return
        target = self._expanded_target_height()
        if self.height() != target:
            self.resize(self.width(), target)

    def _sync_height_deferred(self) -> None:
        """延后一拍再同步列高

        refresh_cards 内同步读取内容高度拿到的是**旧值**：卡片的 sizeHint
        要等它自己的布局跑完才更新（实测同一帧内先是 233、稍后才是 209），
        此时 target == 当前高度，同步不做任何事，等于没修。故延后到下一轮
        事件循环再读一次。上下文传 self：列被销毁时自动取消，不会打到悬空
        对象上。
        """
        QTimer.singleShot(0, self, self._sync_height_to_content)

    # ── 列折叠（隐藏卡片区，仅剩标题栏） ────────────────────

    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, collapsed: bool, save: bool = True,
                      animate: bool = True) -> None:
        """切换列折叠；save=True 时通知控制器持久化状态

        animate=False 供初始状态恢复（_make_column）使用，避免建列时播动画。
        """
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        if animate and motion.enabled() and self.isVisible():
            self._start_collapse_anim(collapsed)
        else:
            self._stop_collapse_anim(finalize=True)
        if save:
            self.signal_collapsed_changed.emit(self._lst.id, collapsed)

    # ── 折叠过渡（只动高度，状态机在终点一次刷齐）───────────────
    #
    # 0.1.1 修复的"嵌合态"事故根因是"状态同步执行到一半被打断"。因此这里
    # 的动画只是纯视觉层：中间帧只改高度，其余状态一律在终点由
    # _apply_collapsed_ui() 一次性刷齐，不产生任何新的中间状态组合。

    def _get_column_height(self) -> float:
        return float(self._anim_height)

    def _set_column_height(self, value: float) -> None:
        self._anim_height = value
        self.setFixedHeight(max(1, int(round(value))))

    columnHeight = Property(float, _get_column_height, _set_column_height)

    def _start_collapse_anim(self, collapsed: bool) -> None:
        """起播折叠/展开过渡：只插值高度，终点才同步状态机"""
        self._stop_collapse_anim(finalize=False)
        start_h = self.height() or self.COLLAPSED_HEIGHT
        end_h = (self.COLLAPSED_HEIGHT if collapsed
                 else self._expanded_target_height())

        # 方向性状态立即生效（箭头/拖放/内容可见性），几何交给动画终点。
        # setVisible 会同步派发 Enter/Leave，事件处理器可能抛异常，逐项收敛。
        def guarded(fn) -> None:
            try:
                fn()
            except BaseException:
                logger.exception("列 %s 折叠过渡的事件处理器抛异常", self._lst.id)

        guarded(lambda: self._header.set_collapsed_mark(collapsed))
        guarded(lambda: self.setAcceptDrops(
            not collapsed and self._visible_cards is None))
        if not collapsed:
            # 展开：先恢复内容可见，随高度增长逐层露出（超出部分被裁剪）
            guarded(lambda: self._scroll.setVisible(True))
            guarded(lambda: self._add_btn.setVisible(True))

        anim = QPropertyAnimation(self, b"columnHeight", self)
        anim.setDuration(AppConfig.COLLAPSE_ANIM_MS)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(float(start_h))
        anim.setEndValue(float(end_h))
        anim.finished.connect(self._on_collapse_anim_finished)
        self._collapse_anim = anim
        anim.start()

    def _on_collapse_anim_finished(self) -> None:
        self._collapse_anim = None
        self._finish_collapse_anim()

    def _finish_collapse_anim(self) -> None:
        """结束过渡：清掉动画期的固定高度，把状态机按最终态一次刷齐"""
        self.setMinimumHeight(0)
        self.setMaximumHeight(_MAX_WIDGET_H)
        self._apply_collapsed_ui()

    def _stop_collapse_anim(self, finalize: bool) -> None:
        """停掉进行中的折叠动画（finalize=True 时顺带刷齐最终态）"""
        anim = self._collapse_anim
        if anim is not None:
            self._collapse_anim = None
            anim.finished.disconnect(self._on_collapse_anim_finished)
            anim.stop()
            anim.deleteLater()
        if finalize:
            self._finish_collapse_anim()

    def hideEvent(self, event) -> None:
        """隐藏时收尾进行中的折叠动画，避免停在中间高度"""
        if self._collapse_anim is not None:
            self._stop_collapse_anim(finalize=True)
        super().hideEvent(event)

    def _apply_collapsed_ui(self) -> None:
        """把 _collapsed 对应的全部 UI 状态一次性刷齐（幂等）

        曾出现"执行到一半被打断"的嵌合态：滚动区已显示但尺寸策略/箭头/
        添加按钮仍停留在折叠态 → 展开后列卡在 400px 居中且没有添加按钮。
        策略先于可见性设置，末尾强制同步重排父布局，保证任何时刻落盘的
        布局都是一致的最终态。

        另一条截断路径：setVisible / 重排会**同步**派发 Enter/Leave 等事件
        （展开时列头因位置变化收到 Leave，折叠时卡片收到 Hide），事件处理器
        里抛出的异常会从 setVisible 逸出、跳过其后的全部同步。故逐项收敛
        异常，先把最终态刷齐，再把首个异常交给日志——不再让 UI 停在中间态。
        """
        collapsed = self._collapsed
        # 折叠列高度收窄为标题栏（固定策略），未折叠列拉伸填满
        policy = QSizePolicy(QSizePolicy.Preferred,
                             QSizePolicy.Fixed if collapsed
                             else QSizePolicy.Expanding)
        self.setSizePolicy(policy)

        first_error: BaseException | None = None

        def step(fn) -> None:
            nonlocal first_error
            try:
                fn()
            except BaseException as exc:   # 事件处理器异常：记下后继续刷状态
                if first_error is None:
                    first_error = exc

        step(lambda: self._scroll.setVisible(not collapsed))
        step(lambda: self._add_btn.setVisible(not collapsed))
        # 过滤态(搜索/今日)落点不可靠,拖放保持禁用,不能被折叠切换覆盖
        self.setAcceptDrops(not collapsed and self._visible_cards is None)
        step(lambda: self._header.set_collapsed_mark(collapsed))
        self.updateGeometry()
        host = self.parentWidget()
        lay = host.layout() if host is not None else None
        if lay is not None:
            step(lay.invalidate)
            step(lay.activate)   # 同步按最终态重排，不给中间态留渲染窗口
        if first_error is not None:
            logger.error("列 %s 折叠状态同步期间事件处理器抛异常"
                         "（UI 已按最终态刷齐）", self._lst.id,
                         exc_info=first_error)

    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self._collapsed)

    # ── 拖放 ──────────────────────────────────────────────

    def is_filtered(self) -> bool:
        """搜索/今日聚焦过滤态：整列拖拽不可用（卡片拖放由 acceptDrops 拦截）"""
        return self._visible_cards is not None

    def _set_drop_highlight(self, on: bool) -> None:
        """整列拖拽悬停时的落点高亮（accent 边框）

        用动态属性 [drop="true"] 驱动 QSS，不再整块重设样式表——拖拽过程中
        dragEnter/dragLeave 会频繁切换，重设样式表会让整列子树反复 re-polish。
        """
        _set_prop(self, "drop", bool(on))

    def _rebuild_drop_mids(self) -> None:
        """拖拽进入时缓存各卡插入判定点（host 内 y 中点，升序）

        用 host 相对坐标：拖拽期间纵向自动滚动只改视口偏移，host 内
        布局不变，快照始终有效；dragMove（60-125Hz）与自动滚动 tick
        （30ms）不再逐卡 mapToGlobal。
        """
        self._drop_mids = [
            cw.mapTo(self._cards_host, QPoint(0, 0)).y() + cw.height() // 2
            for cw in self._card_widgets
        ]

    def _drop_index_from_y(self, y_global: int) -> int:
        """根据全局 y 坐标计算插入位置（卡片序号；对快照二分）"""
        if self._drop_mids is None:
            self._rebuild_drop_mids()
        host_y = self._cards_host.mapFromGlobal(QPoint(0, y_global)).y()
        # 与原逐卡扫描等价：返回第一个「y < 中点」的卡片位置
        return bisect.bisect_right(self._drop_mids, host_y)

    # ── 拖拽落点指示线 + 视口边缘自动滚动 ───────────────────

    def _column_y_from_event(self, event) -> int:
        """把 dragEvent 的列内坐标换算为全局 y（落点判定用）"""
        return (event.position().toPoint().y()
                + self.mapToGlobal(QPoint(0, 0)).y())

    def _update_drop_indicator_at(self, global_y: int) -> None:
        """把插入指示线移到 global_y 对应的卡片间隙（位置不变则跳过）"""
        index = self._drop_index_from_y(global_y)
        current = self._cards_layout.indexOf(self._drop_indicator)
        if current == index:
            return
        if current >= 0:
            self._cards_layout.removeWidget(self._drop_indicator)
        self._cards_layout.insertWidget(index, self._drop_indicator)
        self._drop_indicator.show()

    def _hide_drop_indicator(self) -> None:
        current = self._cards_layout.indexOf(self._drop_indicator)
        if current >= 0:
            self._cards_layout.removeWidget(self._drop_indicator)
        self._drop_indicator.hide()

    def _board_scroll_area(self) -> QScrollArea | None:
        """父链上的看板横向滚动区（列自身的纵向滚动区不在父链上）

        构造完成后父链稳定，缓存结果供自动滚动 tick（30ms）复用。
        """
        if self._board_scroll_cache is None:
            p = self.parent()
            while p is not None:
                if isinstance(p, QScrollArea):
                    self._board_scroll_cache = p
                    break
                p = p.parent()
        return self._board_scroll_cache

    def _auto_scroll_tick(self) -> None:
        """按鼠标全局位置滚动：看板横向 + 本列纵向（贴视口边缘时）

        纵向滚动会改变卡片的全局 y，滚动后需同步刷新插入线位置。
        """
        if not self._drag_hovering:
            return
        gp = QCursor.pos()
        edge = AppConfig.DRAG_AUTO_SCROLL_EDGE_PX
        step = AppConfig.DRAG_AUTO_SCROLL_STEP

        board_scroll = self._board_scroll_area()
        if board_scroll is not None:
            vp = board_scroll.viewport()
            if vp.rect().contains(vp.mapFromGlobal(gp)):
                local = vp.mapFromGlobal(gp)
                sb = board_scroll.horizontalScrollBar()
                if local.x() < edge:
                    sb.setValue(sb.value() - step)
                elif local.x() > vp.width() - edge:
                    sb.setValue(sb.value() + step)

        vp2 = self._scroll.viewport()
        if vp2.rect().contains(vp2.mapFromGlobal(gp)):
            local2 = vp2.mapFromGlobal(gp)
            vb = self._scroll.verticalScrollBar()
            if local2.y() < edge:
                vb.setValue(vb.value() - step)
            elif local2.y() > vp2.height() - edge:
                vb.setValue(vb.value() + step)
        self._update_drop_indicator_at(gp.y())

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_CARD):
            self._drag_hovering = True
            self._rebuild_drop_mids()   # 拖拽期间布局不变，快照全程有效
            self._update_drop_indicator_at(self._column_y_from_event(event))
            self._auto_scroll_timer.start()
            event.acceptProposedAction()
        elif event.mimeData().hasFormat(MIME_LIST):
            self._set_drop_highlight(True)
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_CARD):
            self._drag_hovering = True
            self._update_drop_indicator_at(self._column_y_from_event(event))
            if not self._auto_scroll_timer.isActive():
                self._auto_scroll_timer.start()
            event.acceptProposedAction()
        elif event.mimeData().hasFormat(MIME_LIST):
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._set_drop_highlight(False)
        self._drag_hovering = False
        self._auto_scroll_timer.stop()
        self._drop_mids = None   # 快照随拖拽结束作废（下次进入时重建）
        self._hide_drop_indicator()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        mime = event.mimeData()
        if mime.hasFormat(MIME_CARD):
            self._drag_hovering = False
            self._auto_scroll_timer.stop()
            card_id = bytes(mime.data(MIME_CARD)).decode("utf-8")
            # 落点计算仍用现快照（布局此刻未变），算完再作废
            index = self._drop_index_from_y(
                event.position().toPoint().y()
                + self.mapToGlobal(QPoint(0, 0)).y())
            self._drop_mids = None
            self._hide_drop_indicator()
            self.signal_card_move.emit(card_id, self._lst.id, index)
            event.acceptProposedAction()
            return
        if mime.hasFormat(MIME_LIST):
            self._set_drop_highlight(False)
            moved_id = bytes(mime.data(MIME_LIST)).decode("utf-8")
            # 落点 x 相对本列中心：左半=插到本列前，右半=插到本列后
            insert_before = event.position().toPoint().x() < self.width() // 2
            self.signal_list_move.emit(moved_id, self._lst.id, insert_before)
            event.acceptProposedAction()


class AddCardButton(QPushButton):
    """添加卡片/列表按钮（样式由看板级样式表按 objectName 下发）"""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("addBoardBtn")
        self.setCursor(Qt.PointingHandCursor)

    def reapply(self) -> None:
        """配色由看板级样式表统一下发，此处仅触发重绘"""
        self.update()


class BoardView(QWidget):
    """看板顶层视图：渐变背景 + 工具栏 + 列表横向滚动区"""

    signal_collapse_clicked = Signal()
    signal_theme_selected = Signal(str)         # light / dark
    signal_card_edit = Signal(str, str)         # list_id, card_id
    signal_card_done = Signal(str, str, bool)   # list_id, card_id, done
    signal_card_delete = Signal(str, str)       # list_id, card_id
    signal_card_move = Signal(str, str, int)    # card_id, target_list_id, index
    signal_list_move = Signal(str, str, bool)   # moved_list_id, target_list_id, insert_before
    signal_card_add = Signal(str)               # list_id
    signal_list_add = Signal()
    signal_list_title_changed = Signal(str, str)
    signal_list_delete = Signal(str)
    signal_list_collapsed = Signal(str, bool)   # list_id, collapsed
    signal_quit_requested = Signal()
    signal_zoom_requested = Signal()
    signal_card_pomo = Signal(str)              # card_id
    signal_card_archive = Signal(str)           # card_id
    signal_card_star = Signal(str)              # card_id（星标 toggle）
    signal_archive_open = Signal()
    signal_export = Signal(str)                 # "md" | "csv"
    signal_export_backup = Signal()             # 导出完整备份 .json
    signal_import_backup = Signal()             # 从备份导入
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

        self._title_label = QLabel("我的看板")
        self._title_label.setObjectName("boardTitle")
        self._toolbar_layout.addWidget(self._title_label)

        self._stats_label = QLabel()
        self._stats_label.setObjectName("boardStats")
        self._toolbar_layout.addWidget(self._stats_label)
        # 注意：不加中间 stretch——弹性全部留给搜索框（右侧控件固定聚集）

        self._today_btn = QPushButton("今日")
        self._today_btn.setObjectName("boardToolBtn")
        self._today_btn.setCheckable(True)
        self._today_btn.setCursor(Qt.PointingHandCursor)
        self._today_btn.setToolTip("只显示未完成的：星标 / 已逾期 / 今天截止")
        self._today_btn.toggled.connect(self._apply_filter)
        self._today_btn.toggled.connect(self.signal_today_toggled.emit)
        self._toolbar_layout.addWidget(self._today_btn)

        self._search_edit = QLineEdit()
        self._search_edit.setObjectName("boardSearch")
        self._search_edit.setPlaceholderText("搜索卡片…")
        self._search_edit.setClearButtonEnabled(True)
        # 弹性宽度：空间富余时舒展、不足时收缩到最小宽，避免工具栏被挤出窗口
        self._search_edit.setMinimumWidth(120)
        self._search_edit.setMaximumWidth(300)
        self._search_edit.setSizePolicy(QSizePolicy.Policy.Expanding,
                                        QSizePolicy.Policy.Fixed)
        self._search_edit.setAccessibleName("搜索卡片")
        # 逐键输入只重启防抖计时器，停顿后才过滤（避免大板每键全树刷新）
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(AppConfig.SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._apply_filter)
        self._search_edit.textChanged.connect(self._on_search_edited)
        # 卡片小写副本缓存 card_id → (title, notes, title.lower(), notes.lower())：
        # 过滤热路径免对全部备注全文反复 lower()（原文比对自校验失效；
        # 删除卡片的残条目为两条小字符串，随卡量有界）
        self._lower_cache: dict[str, tuple[str, str, str, str]] = {}
        # 标签过滤态（点卡片左缘色条触发，与搜索/今日模式正交叠加）
        self._label_filter: str | None = None
        self._label_chip = QPushButton()
        self._label_chip.setObjectName("boardToolBtn")
        self._label_chip.setCursor(Qt.PointingHandCursor)
        self._label_chip.setToolTip("点击清除标签过滤")
        self._label_chip.clicked.connect(self._clear_label_filter)
        self._label_chip.hide()
        self._toolbar_layout.addWidget(self._label_chip)
        self._toolbar_layout.addWidget(self._search_edit)
        # 搜索框参与剩余空间分配（与 stats 之后的 stretch 平分）
        self._toolbar_layout.setStretchFactor(self._search_edit, 1)

        self._add_list_btn = AddCardButton("+ 添加列表")
        self._add_list_btn.setFixedWidth(96)
        self._add_list_btn.clicked.connect(self.signal_list_add.emit)
        self._toolbar_layout.addWidget(self._add_list_btn)

        self._archive_btn = QPushButton("归档")
        self._archive_btn.setObjectName("boardToolBtn")
        self._archive_btn.setCursor(Qt.PointingHandCursor)
        self._archive_btn.setToolTip("查看已归档卡片并恢复")
        self._archive_btn.clicked.connect(self.signal_archive_open.emit)
        self._toolbar_layout.addWidget(self._archive_btn)

        self._export_btn = QPushButton("导出")
        self._export_btn.setObjectName("boardToolBtn")
        self._export_btn.setCursor(Qt.PointingHandCursor)
        self._export_btn.setToolTip("导出为 Markdown / CSV")
        self._export_btn.clicked.connect(self._show_export_menu)
        self._toolbar_layout.addWidget(self._export_btn)

        self._theme_btn = _ThemeToggleButton()
        self._theme_btn.setObjectName("boardThemeBtn")
        self._theme_btn.setCursor(Qt.PointingHandCursor)
        self._theme_btn.setFixedSize(34, 34)
        self._theme_btn.setToolTip("切换浅色 / 深色主题")
        self._theme_btn.clicked.connect(self._on_theme_clicked)
        self._toolbar_layout.addWidget(self._theme_btn)

        self._collapse_btn = QPushButton("－")
        self._collapse_btn.setObjectName("boardCollapseBtn")
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
        self._scroll.setObjectName("boardScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.viewport().setAutoFillBackground(False)
        self._lists_host = QWidget()
        self._lists_host.setObjectName("listsHost")
        self._lists_layout = QHBoxLayout(self._lists_host)
        self._lists_layout.setContentsMargins(16, 4, 16, 12)
        self._lists_layout.setSpacing(12)
        self._lists_layout.addStretch(1)
        self._scroll.setWidget(self._lists_host)
        root.addWidget(self._scroll, 1)

        # 空看板引导：无任何列表时覆盖在列表区上方居中，可穿透鼠标
        self._empty_hint = QLabel(
            "看板还是空的\n点击右上角「+ 添加列表」创建第一列", self)
        self._empty_hint.setObjectName("emptyBoardHint")
        self._empty_hint.setAlignment(Qt.AlignCenter)
        self._empty_hint.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._empty_hint.hide()

        # 看板内轻提示（删除/撤销等操作反馈，见 toast.py）
        self._toast = Toast(self)

        # 全局过滤器只在重命名编辑器存在期间安装（见 _attach_rename_filter）：
        # 常态挂载会让全应用每个事件都过一遍 Python，实测 +23µs/事件
        self._rename_filter_installed = False
        self.reapply_theme()
        AppTheme.register(self.reapply_theme)

    # ── 重命名编辑器关闭守卫（按需安装的全局事件过滤器）────────

    def _attach_rename_filter(self) -> None:
        """按 _ACTIVE_RENAME 的有无安装/卸载全局事件过滤器"""
        need = _ACTIVE_RENAME is not None
        if need == self._rename_filter_installed:
            return
        app = QApplication.instance()
        if app is None:
            return
        if need:
            app.installEventFilter(self)
        else:
            app.removeEventFilter(self)
        self._rename_filter_installed = need

    def eventFilter(self, obj, event) -> bool:
        if (event.type() == QEvent.MouseButtonPress
                and _ACTIVE_RENAME is not None
                and isinstance(obj, QWidget)
                and obj is not _ACTIVE_RENAME
                and not _ACTIVE_RENAME.isAncestorOf(obj)):
            finish_active_rename()
            self._attach_rename_filter()   # 编辑器已关闭 → 卸掉过滤器
        return super().eventFilter(obj, event)

    def finish_rename(self, cancel: bool = False) -> bool:
        """关闭当前列表重命名编辑器（cancel=True 丢弃修改）；返回是否有关闭"""
        closed = finish_active_rename(cancel)
        if closed:
            self._attach_rename_filter()
        return closed

    def set_zoom_state(self, zoomed: bool) -> None:
        """同步最大化/还原图标状态（macOS 无此控件，空操作）"""
        if self._window_controls is not None:
            self._window_controls.set_zoomed(zoomed)

    # ── 主题 ──────────────────────────────────────────────

    def reapply_theme(self) -> None:
        """主题切换：整块样式表设一次，再同步各控件与配色无关的状态

        此前逐控件 setStyleSheet，嵌套设置会让同一批子控件反复 re-polish。
        实测 300 张卡片下 reapply_theme() 285ms，其中列/卡片各占一半；
        配色改为看板级统一下发后，整棵子树只 polish 一遍。
        """
        # 1) 整块配色一次设完（字符串按主题模式缓存）
        qss = _board_qss(AppTheme.mode())
        if self.styleSheet() != qss:
            self.setStyleSheet(qss)

        # 2) 与配色无关的状态
        self._theme_btn.set_mode(AppTheme.mode())
        if self._window_controls is not None:
            self._window_controls.reapply()
        self._attach_rename_filter()
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

    def refresh(self, lists: list[BoardList],
                stats: dict | None = None) -> None:
        """按看板数据增量同步列（按 list.id 复用列与卡片控件）

        stats 传入 board.today_stats() 结果时统计行直接取用，免重扫。
        """
        # 记录看板横向滚动位置，增删列后恢复
        sb = self._scroll.horizontalScrollBar()
        scroll_pos = sb.value()

        self._lists = lists

        visibles = {lst.id: self._visible_cards_for(lst) for lst in lists}

        by_id = {col.list_id(): col for col in self._columns}
        prev_order = [col.list_id() for col in self._columns]
        ordered_cols: list[ListColumn] = []
        kept: set[str] = set()
        for lst in lists:
            col = by_id.get(lst.id)
            if col is None:
                col = self._make_column(lst)
            else:
                col.set_list(lst, visibles[lst.id])
                kept.add(lst.id)
            ordered_cols.append(col)
        # 列顺序未变（单卡变更/编辑保存等）→ 布局无需重插；仅增删列或
        # 拖拽重排造成顺序变化时才 remove+insert 保序（与卡片同一策略）
        if prev_order != [col.list_id() for col in ordered_cols]:
            for i, col in enumerate(ordered_cols):
                self._lists_layout.removeWidget(col)
                # AlignTop 让布局项取 sizeHint（内容高度）而非拉伸填满整列区
                self._lists_layout.insertWidget(i, col, 0, Qt.AlignTop)
        for list_id, col in by_id.items():
            if list_id not in kept:
                self._columns.remove(col)
                col.setParent(None)
                col.deleteLater()

        # 列顺序与 lists 同步：列拖拽/撤销会改变 lists 顺序，布局已随上方
        # 循环重插，_columns 列表本身也必须跟随，否则 _apply_filter 的
        # zip(_lists, _columns) 在过滤模式下会与列配对错位
        self._columns = ordered_cols

        self.update_stats(lists, visibles=visibles, stats=stats)
        self._set_today_count(sum(len(v or []) for v in visibles.values()))
        self._update_empty_hint(lists)
        sb.setValue(scroll_pos)

    def _search_query(self) -> str:
        return self._search_edit.text().strip().lower()

    def _lower_texts(self, c: Card) -> tuple[str, str]:
        """标题/备注的小写副本（缓存命中免全文 lower）"""
        cached = self._lower_cache.get(c.id)
        if cached is not None and cached[0] == c.title and cached[1] == c.notes:
            return cached[2], cached[3]
        entry = (c.title, c.notes, c.title.lower(), c.notes.lower())
        self._lower_cache[c.id] = entry
        return entry[2], entry[3]

    def _filter_cards(self, lst: BoardList, q: str) -> list[Card] | None:
        """按关键词过滤卡片（标题/备注，不区分大小写）；空关键词返回 None=全部"""
        if not q:
            return None
        out: list[Card] = []
        for c in lst.cards:
            title_low, notes_low = self._lower_texts(c)
            if q in title_low or q in notes_low:
                out.append(c)
        return out

    def _visible_cards_for(self, lst: BoardList) -> list[Card] | None:
        """列的可见卡片：今日聚焦模式与搜索过滤组合；无任何过滤返回 None

        今日聚焦判定统一走 Card.in_today_focus（模型层单实现）；
        标签过滤（点色条触发）与搜索、今日模式正交叠加。
        """
        q = self._search_query()
        tag = self._label_filter
        if self._today_btn.isChecked():
            today = date.today()
            cards = [c for c in lst.cards if c.in_today_focus(today)]
            if q:
                cards = [c for c in cards
                         if q in self._lower_texts(c)[0]
                         or q in self._lower_texts(c)[1]]
            if tag:
                cards = [c for c in cards if tag in c.labels]
            # 今日聚焦内排序：高 > 中 > 低，无优先级垫底；同级星标提前，
            # 再按截止日升序（星标是主动标注，优先于被动"今天截止"）。
            # decorate：due_delta（含 ISO 解析）每卡只算一次
            def _today_sort_key(c: Card):
                delta = c.due_delta(today)
                return (c.priority == 0, c.priority, not c.starred,
                        delta if delta is not None else 999)
            cards.sort(key=_today_sort_key)
            return cards
        if not q and not tag:
            return None
        cards = self._filter_cards(lst, q) if q else list(lst.cards)
        if tag:
            cards = [c for c in cards if tag in c.labels]
        return cards

    def _on_label_clicked(self, key: str) -> None:
        """点卡片左缘色条按标签过滤全板；再点同色清除"""
        self._label_filter = None if self._label_filter == key else key
        self._update_label_chip()
        self._apply_filter()

    def _clear_label_filter(self) -> None:
        if self._label_filter is None:
            return
        self._label_filter = None
        self._update_label_chip()
        self._apply_filter()

    def _update_label_chip(self) -> None:
        """过滤 chip：过滤态显示「🏷 红 ✕」，清除后隐藏"""
        if self._label_filter:
            name = AppConfig.LABEL_NAMES.get(self._label_filter,
                                             self._label_filter)
            self._label_chip.setText(f"🏷 {name} ✕")
            self._label_chip.show()
        else:
            self._label_chip.hide()

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
        if self._search_query():
            self._stats_label.setText(f"匹配 {total} 张")
        else:
            self.update_stats(self._lists)

    def _set_today_count(self, n: int) -> None:
        self._today_btn.setText(f"今日 {n}")

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
        menu.addSeparator()
        act_backup = menu.addAction("导出备份（.json）")
        act_import = menu.addAction("从备份导入…")
        chosen = menu.exec(self.mapToGlobal(
            QPoint(self._export_btn.x(), self._export_btn.height())))
        menu.deleteLater()   # 常驻 BoardView 上的菜单不销毁会每次导出累积一个
        if chosen is act_md:
            self.signal_export.emit("md")
        elif chosen is act_csv:
            self.signal_export.emit("csv")
        elif chosen is act_backup:
            self.signal_export_backup.emit()
        elif chosen is act_import:
            self.signal_import_backup.emit()

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
        col.signal_list_move.connect(self.signal_list_move)
        col.signal_add_card.connect(self.signal_card_add)
        col.signal_title_changed.connect(self.signal_list_title_changed)
        col.signal_delete_list.connect(self.signal_list_delete)
        col.signal_card_pomo.connect(self.signal_card_pomo)
        col.signal_card_archive.connect(self.signal_card_archive)
        col.signal_card_star.connect(self.signal_card_star)
        # 标签过滤是纯视图态：内部消化，不冒泡控制器
        col.signal_label_clicked.connect(self._on_label_clicked)
        col.signal_collapsed_changed.connect(self.signal_list_collapsed)
        self._columns.append(col)
        # 恢复上次折叠状态（save=False 不触发持久化回调；建列时不播动画）
        if board_list.id in AppConfig.get_collapsed_lists():
            col.set_collapsed(True, save=False, animate=False)
        return col

    def update_stats(self, lists: list[BoardList],
                     visibles: dict | None = None,
                     stats: dict | None = None) -> None:
        q = self._search_query()
        if q:
            # 搜索进行中：统计改为匹配数（刷新也不会切回默认文案）；
            # visibles 传入时复用 refresh 已算结果，避免同一输入扫两遍文本
            if visibles is None:
                total = sum(len(self._visible_cards_for(l) or []) for l in lists)
            else:
                total = sum(len(v or []) for v in visibles.values())
            self._stats_label.setText(f"匹配 {total} 张")
            return
        if self._label_filter:
            # 标签过滤态：统计行持续反馈（数据变更经 refresh 刷新也不丢）
            if visibles is None:
                total = sum(len(self._visible_cards_for(l) or []) for l in lists)
            else:
                total = sum(len(v or []) for v in visibles.values())
            name = AppConfig.LABEL_NAMES.get(self._label_filter,
                                             self._label_filter)
            self._stats_label.setText(f"标签 {name} · {total} 张")
            return
        if stats is not None:
            self._stats_label.setText(
                f"{stats['total']} 张卡片 · 完成 {stats['done']}")
            return
        total = sum(len(l.cards) for l in lists)
        done = sum(1 for l in lists for card in l.cards if card.done)
        self._stats_label.setText(f"{total} 张卡片 · 完成 {done}")

    def _update_empty_hint(self, lists: list[BoardList]) -> None:
        """无任何列表时显示空看板引导（覆盖列表区，可穿透鼠标）"""
        if lists:
            self._empty_hint.hide()
        else:
            self._empty_hint.setGeometry(self.rect())
            self._empty_hint.show()
            self._empty_hint.raise_()

    def resizeEvent(self, event) -> None:
        """空状态引导与 toast 跟随窗口尺寸重定位"""
        super().resizeEvent(event)
        if self._empty_hint.isVisible():
            self._empty_hint.setGeometry(self.rect())
        if self._toast.isVisible():
            self._toast.move((self.width() - self._toast.width()) // 2,
                             self.height() - self._toast.height() - 20)

    def show_toast(self, text: str) -> None:
        """显示看板内轻提示（折叠态由控制器改走托盘通知）"""
        self._toast.show_message(text)

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

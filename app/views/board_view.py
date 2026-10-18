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
import json
import logging
import math
import zlib
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

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
    QVariantAnimation,
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
    QPixmap,
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
from app.i18n import label_display, priority_name, repeat_display, tr
from app.models.board import FUND_KIND_LABELS, BoardList, Card
from app.views import motion, tokens
from app.views.notes_popover import (
    hide_notes_popover,
    notes_pinned_for,
    notes_popover,
    notes_popover_anchored_to,
    notes_popover_hovering,
)
from app.views.theme import AppTheme, to_qcolor
from app.views.toast import Toast

logger = logging.getLogger(__name__)

MIME_LIST = "application/x-petboard-list"
MIME_CARD = "application/x-petboard-card"
# 成组拖拽：单卡走 MIME_CARD（日历等只认单卡的落点也要能接），
# 多选成组时另发这条，落点侧优先取它
MIME_CARDS = "application/x-petboard-cards"

_MAX_WIDGET_H = 16777215    # QWIDGETSIZE_MAX：清除动画期固定高度用


def _popup_menu(menu: QMenu, global_pos: QPoint):
    """弹出菜单并返回被选中的 QAction（未选中返回 None）

    单独抽一层是为了给测试留接缝：QMenu.exec 是 Shiboken 槽，patch 类属性
    对它无效（PySide6 从 C++ 方法表解析），测试无法在不起真菜单的情况下
    走一遍"选项 → 派发"的完整链路。
    """
    return menu.exec(global_pos)


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


# 卡片徽章 tone → (软底色键, 前景色键)
#
# 此前**所有 tone 共用一个 accent_soft 作底**、只用 `c[tone]` 换文字色，
# 于是红/橙/绿字全压在蓝色调底上：实测 warning 1.73:1、success 2.57:1、
# danger 3.34:1，11px 小字全线不达标；而且色相互相污染，截图里"已逾期"
# 和"有备注"看起来是同一枚灰蓝 chip——卡片信息行显得吵，根因就在这里。
# 现在每个 tone 一套底+前景，由 tests/test_contrast.py 逐对守住。
_BADGE_TONE_COLORS = {
    "danger": ("danger_soft", "danger_text"),
    "warning": ("warning_soft", "warning_text"),
    "success": ("success_soft", "success_text"),
    "accent": ("accent_soft", "accent_text"),
    "text_secondary": ("neutral_soft", "neutral_text"),
}
_BADGE_TONES = tuple(_BADGE_TONE_COLORS)

# 工作目录徽章的显示文本（真实路径放徽章 tooltip；收集/构建/事件过滤
# 三处靠它识别是目录徽章）
WORKDIR_BADGE_TEXT = "📂"


@lru_cache(maxsize=4)
def _board_qss(mode: str) -> str:
    """看板整块样式表（按主题模式缓存）

    整块只设在 BoardView 一个控件上。此前逐列/逐卡各自 setStyleSheet，
    嵌套设置会让同一批子控件被反复 re-polish：三层级联实测 300 张卡片
    主题切换 285ms，合并为单次设置后只剩一遍 polish。
    配色随主题变，故按 mode 缓存字符串，避免每次切换重新拼接。
    """
    c = AppConfig.DARK_COLORS if mode == "dark" else AppConfig.COLORS
    # 兜底规则先给"药丸"外形：meta_items 里若出现映射表之外的 tone
    # （此前 success 就是漏网的那个，徽章退化成一行裸文字、连内边距都没了），
    # 至少形状还在，不会让信息行错位。
    badge_rules = "\n".join([
        'QLabel#cardBadge {\n'
        f'    background: {c["neutral_soft"]};\n'
        f'    color: {c["neutral_text"]};\n'
        f'    {tokens.radius_css("chip")};\n'
        f'    {tokens.pad(2, 6)};\n'
        f'    {tokens.font_css(AppConfig.UI_FONT_CAPTION)};\n'
        '}',
        *(
            f'QLabel#cardBadge[tone="{tone}"] {{\n'
            f'    background: {c[soft]};\n'
            f'    color: {c[text]};\n'
            f'}}'
            for tone, (soft, text) in _BADGE_TONE_COLORS.items()
        ),
    ])
    return f"""
        /* ── 工具条 ── */
        QLabel#boardTitle {{
            {tokens.font_css(AppConfig.UI_FONT_DISPLAY)};
            font-weight: bold;
            color: {c['text_primary']};
            background: transparent;
        }}
        QPushButton#boardTitleBtn {{
            {tokens.font_css(AppConfig.UI_FONT_DISPLAY)};
            font-weight: bold;
            color: {c['text_primary']};
            background: transparent;
            border: none;
            {tokens.radius_css('control')};
            {tokens.pad(3, 8)};
        }}
        QPushButton#boardTitleBtn:hover {{
            background: {c['glass']};
        }}
        QFrame#selectionBar {{
            background: {c['glass']};
            border: 1px solid {c['border']};
            {tokens.radius_css('panel')};
        }}
        QLabel#selCount {{
            color: {c['text_primary']};
            {tokens.font_css(AppConfig.UI_FONT_LABEL)};
            font-weight: bold;
            background: transparent;
        }}
        QLabel#boardStats {{
            {tokens.font_css(AppConfig.UI_FONT_LABEL)};
            color: {c['text_primary']};
            background: {c['glass']};
            {tokens.radius_css('control')};
            {tokens.pad(3, 10)};
        }}
        QPushButton#boardThemeBtn {{
            background: {c['glass']};
            border: none;
            border-radius: {tokens.circle_radius(34)}px;
            color: {c['text_primary']};
        }}
        QPushButton#boardThemeBtn:hover {{
            background: {c['glass_hover']};
        }}
        QPushButton#boardThemeBtn:pressed {{
            background: {c['accent_soft']};
        }}
        QPushButton#boardThemeBtn:focus {{
            border: 1.5px solid {c['focus_ring']};
        }}
        QPushButton#boardCollapseBtn {{
            background: {c['glass']};
            border: none;
            border-radius: {tokens.circle_radius(34)}px;
            font-weight: bold;
            color: {c['text_primary']};
        }}
        QPushButton#boardCollapseBtn:hover {{
            background: {c['glass_hover']};
        }}
        QPushButton#boardCollapseBtn:pressed {{
            background: {c['accent_soft']};
        }}
        QPushButton#boardCollapseBtn:focus {{
            border: 1.5px solid {c['focus_ring']};
        }}
        QPushButton#boardToolBtn {{
            background: {c['glass']};
            /* transparent 而非 none：给 :focus 留出边框位，否则加焦点环时
               按钮会因多出 1.5px 边框而抖一下 */
            border: 1.5px solid transparent;
            border-radius: {AppConfig.UI_RADIUS_CONTROL}px;
            {tokens.pad(5, 10)};
            {tokens.font_css(AppConfig.UI_FONT_LABEL)};
            color: {c['text_primary']};
        }}
        QPushButton#boardToolBtn:hover {{
            background: {c['glass_hover']};
        }}
        /* 按下态：此前只有 :hover，点归档/导出/今日/主题都没有按压反馈，
           按钮要等面板弹出来才"证明"点到了 */
        QPushButton#boardToolBtn:pressed {{
            background: {c['accent_soft']};
        }}
        /* 焦点环：这些按钮的 border 原是 none，全局 QPushButton:focus 够不到，
           必须逐个补——否则键盘 Tab 过工具栏完全看不出焦点在哪 */
        QPushButton#boardToolBtn:focus {{
            border: 1.5px solid {c['focus_ring']};
        }}
        QPushButton#boardToolBtn:checked {{
            background: {c['accent']};
            color: {c['accent_on']};
        }}
        /* 卡片拖到工具按钮上（归档 / 今日）：松手就执行。
           此前与 :checked 像素完全一致——"筛选已开启"与"松手就落这里"
           是两件事却长得一样，同时成立时无法分辨。改用虚线边框区分。 */
        QPushButton#boardToolBtn[dropTarget="true"] {{
            background: {c['accent']};
            color: {c['accent_on']};
            border: 1.5px dashed {c['accent_soft']};
        }}
        /* 搜索框：与工具按钮同一玻璃面，不再是一整条纯白 */
        QLineEdit#boardSearch {{
            background: {c['glass']};
            border: 1px solid transparent;
            border-radius: {AppConfig.UI_RADIUS_CONTROL}px;
            {tokens.pad_preset(AppConfig.UI_PAD_INPUT)};
            color: {c['text_primary']};
        }}
        /* 聚焦态必须自设：id 选择器优先级高于全局 QLineEdit:focus，
           不写这条则聚焦边框被吃掉、看不出光标在框内 */
        QLineEdit#boardSearch:focus {{
            background: {c['glass_hover']};
            border: 1.5px solid {c['accent']};
        }}
        /* 列尾「+ 添加卡片」：虚线占位框（该位置本来就该"待填"） */
        QPushButton#addBoardBtn {{
            background: transparent;
            color: {c['text_secondary']};
            border: 1.5px dashed {c['border']};
            border-radius: {AppConfig.UI_RADIUS_CONTROL}px;
            {tokens.pad_all(7)};
            {tokens.font_css(AppConfig.UI_FONT_LABEL)};
        }}
        QPushButton#addBoardBtn:hover {{
            background: {c['accent_soft']};
            color: {c['accent']};
            border: 1.5px dashed {c['accent']};
        }}
        /* 工具栏「+ 添加列表」：同排全是实心玻璃按钮，虚线版在渐变背景上
           会被读成"禁用"（它正下方的空看板引导又指着它） */
        QPushButton#addBoardBtn[variant="toolbar"] {{
            background: {c['glass']};
            color: {c['text_primary']};
            border: none;
            border-radius: {AppConfig.UI_RADIUS_CONTROL}px;
            {tokens.pad(5, 10)};
        }}
        QPushButton#addBoardBtn[variant="toolbar"]:hover {{
            background: {c['glass_hover']};
            color: {c['accent']};
            border: none;
        }}
        /* 空看板引导叠在蓝紫渐变上：中性灰在渐变里几乎看不见 */
        QLabel#emptyBoardHint {{
            color: {c['hint_on_board']};
            {tokens.font_css(AppConfig.UI_FONT_TITLE)};
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
            {tokens.radius_css('shell')};
        }}
        QFrame#listColumn[drop="true"] {{
            border: 2px solid {c['accent']};
        }}
        /* 拖拽插入指示线：条高仅 3px，Qt 会把圆角夹到短边的一半
           （1.5px），所以写 2px 还是 6px 渲染完全一致，取最近的 chip 档 */
        QFrame#dropIndicator {{
            background: {c['accent']};
            {tokens.radius_css('chip')};
        }}
        QLabel#listTitle {{
            {tokens.font_css(AppConfig.UI_FONT_TITLE)};
            font-weight: bold;
            color: {c['text_primary']};
            background: transparent;
        }}
        QLabel#listCount {{
            color: {c['text_secondary']};
            {tokens.font_css(AppConfig.UI_FONT_CAPTION)};
            background: {c['mask']};
            border-radius: {AppConfig.UI_RADIUS_CHIP}px;
            {tokens.pad(1, 7)};
        }}
        QLabel#columnHint {{
            /* 空列里唯一的文字（"还没有卡片，点击下方添加" / "没有匹配的卡片"），
               此前取 text_disabled 实测只有 1.87:1（浅）/ 2.60:1（深）——
               一个**禁用态**的色被当说明性文字用。 */
            color: {c['text_muted']};
            {tokens.font_css(AppConfig.UI_FONT_LABEL)};
            background: transparent;
        }}
        QPushButton#listCollapseBtn {{
            background: transparent;
            color: {c['text_secondary']};
            border: none;
            {tokens.radius_css('panel')};
            {tokens.font_css(AppConfig.UI_FONT_BODY)};
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
            {tokens.font_css(AppConfig.UI_FONT_TITLE)};
            font-weight: bold;
            color: {c['text_primary']};
            padding: 0 {tokens.space(4)}px;
            {tokens.radius_css('chip')};
        }}

        /* ── 卡片 ── */
        QFrame#cardFrame {{
            background: {c['bg_card']};
            border: 1px solid {c['border']};
            {tokens.radius_css('card')};
        }}
        QFrame#cardFrame:hover {{
            border: 1px solid {c['accent']};
        }}
        QFrame#cardFrame[selected="true"] {{
            border: 2px solid {c['accent']};
            background: {c['bg_hover']};
        }}
        QLabel#cardTitle {{
            {tokens.font_css(AppConfig.UI_FONT_BODY)};
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
            border-radius: {tokens.circle_radius(AppConfig.CARD_DELETE_BTN_H)}px;
            {tokens.font_css(AppConfig.UI_FONT_CAPTION)};
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
        return f"{tr('已逾期')} {d.month}/{d.day}", True
    if diff == 0:
        return tr("今天截止"), True
    if diff == 1:
        return tr("明天截止"), False
    return f"{d.month}/{d.day}", False


def _fund_deadline_name(limit: int) -> str:
    """期限天数 → 徽标短名（14 天 = 2周期限，30 天 = 30日期限）"""
    return tr("2周期限") if limit == 14 else tr("30日期限")


def _fmt_fund_deadline(card: Card) -> tuple[str, str] | None:
    """资金卡期限徽标 → (文本, 主题色键)；未启用/未设启动日期返回 None

    取下一道未到期期限显示日期；当天红色；已逾期显示超期天数（红色），
    两道都过期时取最后一道（30日期限）。
    """
    nd = card.fund_next_deadline(date.today())
    if nd is None:
        return None
    d, delta, limit = nd
    name = _fund_deadline_name(limit)
    if delta < 0:
        return f"{name}{tr('超 {n} 天').format(n=-delta)}", "danger"
    if delta == 0:
        return f"{name}{tr('今天到期')}", "danger"
    return f"{name} {d.month}/{d.day}", "accent"


def _fmt_fund_role(card: Card) -> str:
    """资金卡角色徽标：牵头·常务会 / 牵头·分管 / 牵头·财政 / 配合"""
    fund = card.fund or {}
    if fund.get("role") == "lead":
        kind = FUND_KIND_LABELS.get(fund.get("kind"), "")
        return f"{tr('牵头')}·{tr(kind)}" if kind else tr("牵头")
    return tr("配合")


def _fmt_fund_steps_line(card: Card) -> str:
    """tooltip 用的一行流程摘要：进度 + 当前环节（全完显示已完成）"""
    if (card.fund or {}).get("role") != "lead":
        # 配合分配不监视环节（steps 可能残留，展示层按角色屏蔽）
        return tr("资金分配监视（配合）：仅 2 次期限提醒")
    done, total = card.fund_progress()
    if not total:
        return tr("资金分配监视：未配置环节")
    head = tr("资金分配流程 {done}/{total}").format(done=done, total=total)
    cur = card.fund_current_step()
    if cur is None:
        return f"{head} · {tr('全部环节已完成')}"
    return f"{head} · {tr('当前：{name}').format(name=cur['name'])}"


class _CardCheckButton(QPushButton):
    """自绘勾选框（替代 ☐/☑ 字形，跨平台渲染一致）

    完成瞬间对勾按路径描画（140ms）：勾选完成是最高频的正反馈动作，
    瞬时划线的满足感不足；描画进度由 _check_progress 驱动，可被
    "暂停动画"开关跳过。
    """

    _P0 = (3.6, 8.2)     # 对勾折点序列（相对勾选框左上角）
    _P1 = (7.0, 11.4)
    _P2 = (12.6, 4.6)
    # 两段折线长度为常量（P 序列恒定）：paintEvent 每帧 math.hypot 纯浪费
    _P01 = math.hypot(_P1[0] - _P0[0], _P1[1] - _P0[1])
    _P12 = math.hypot(_P2[0] - _P1[0], _P2[1] - _P1[1])

    # 完整对勾路径缓存（(0,0) 原点，绘制时 translate 到框左上角）：
    # 完成态 progress 恒为 1，逐帧新建 QPainterPath 纯重复
    _CHECK_PATH_FULL: QPainterPath | None = None

    # 对勾色：压在 success 填充上。深色主题的 success 是亮绿，白对勾只有
    # 1.99:1——勾选框的整个意义就是那个对勾，却在深色下几乎看不见。
    # 与主按钮同思路：填充亮时文字反相。
    @staticmethod
    def _check_color() -> QColor:
        return QColor(AppTheme.colors()["success_on"])

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFlat(True)
        # 命中区 24×24（可点击标准），视觉勾选框仍是 16px 居中绘制——
        # 这是全板最高频的操作，此前 20×20 是整个界面上最小的目标之一
        self.setFixedSize(24, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tr("点击切换完成状态"))
        # 可 check：QPushButton 不可 check 时读屏只报 "button"，
        # 永远不报勾选状态——完成与否只由删除线和绿底承载，非视觉用户
        # 拿不到这个信息。setCheckable 让 QAccessible 暴露 checked。
        self.setCheckable(True)
        self.setAccessibleName(tr("切换完成状态"))
        self._done = False
        self._check_anim: QVariantAnimation | None = None
        self._check_progress = 1.0   # 对勾描画进度 0..1（1=完整对勾）

    def enterEvent(self, event) -> None:
        """悬停变色必须自己请求重绘

        paintEvent 里按 `underMouse()` 取 accent 色，但没有 enter/leave
        时不会有人来触发重绘——悬停高亮要等别的控件恰好让本卡重画才出现，
        而这是全板最高频的操作，反馈不能是"看运气"。
        """
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.update()

    def set_done(self, done: bool) -> None:
        if self._done == done:
            return
        self._done = done
        self.setChecked(done)       # 同步给无障碍层
        self.update()
        if done and motion.enabled():
            self._start_check_anim()
        else:
            self._stop_check_anim()

    def _start_check_anim(self) -> None:
        self._stop_check_anim()
        anim = QVariantAnimation(self)
        anim.setDuration(140)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(self._on_check_tick)
        anim.finished.connect(lambda: self._stop_check_anim())
        self._check_anim = anim
        self._check_progress = 0.0
        anim.start()

    def _stop_check_anim(self) -> None:
        anim = self._check_anim
        if anim is not None:
            anim.stop()
            anim.deleteLater()
            self._check_anim = None
        self._check_progress = 1.0
        self.update()

    def _on_check_tick(self, value) -> None:
        self._check_progress = float(value)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        c = AppTheme.colors()
        box = QRect((self.width() - 16) // 2, (self.height() - 16) // 2, 16, 16)
        radius = 4.5
        if self._done:
            # 完成：主题色填充 + 对勾（按描画进度截取路径）
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(c["success"]))
            painter.drawRoundedRect(box, radius, radius)
            # 对勾色随主题：深色的 success 是亮绿，白对勾只有 1.99:1
            pen = _CHECK_PENS.get(c["success_on"])
            if pen is None:
                pen = QPen(self._check_color(), 1.8)
                pen.setCapStyle(Qt.RoundCap)
                pen.setJoinStyle(Qt.RoundJoin)
                _CHECK_PENS[c["success_on"]] = pen
            painter.setPen(pen)
            total = self._P01 + self._P12
            d = self._check_progress * total
            if d >= total:
                # 动画结束后的常态：直接用缓存完整路径
                if _CardCheckButton._CHECK_PATH_FULL is None:
                    path = QPainterPath(QPointF(*_CardCheckButton._P0))
                    path.lineTo(QPointF(*_CardCheckButton._P1))
                    path.lineTo(QPointF(*_CardCheckButton._P2))
                    _CardCheckButton._CHECK_PATH_FULL = path
                painter.save()
                painter.translate(box.topLeft())
                painter.drawPath(_CardCheckButton._CHECK_PATH_FULL)
                painter.restore()
            else:
                bx, by = box.left(), box.top()
                p0 = QPointF(bx + self._P0[0], by + self._P0[1])
                p1 = QPointF(bx + self._P1[0], by + self._P1[1])
                p2 = QPointF(bx + self._P2[0], by + self._P2[1])
                path = QPainterPath()
                path.moveTo(p0)
                if d <= self._P01:
                    t = (d / self._P01) if self._P01 > 0 else 1.0
                    path.lineTo(p0 + (p1 - p0) * t)
                else:
                    t = ((d - self._P01) / self._P12) if self._P12 > 0 else 1.0
                    path.lineTo(p1)
                    path.lineTo(p1 + (p2 - p1) * t)
                painter.drawPath(path)
        else:
            # 未完成：圆角方框，悬停变主题色
            # 未完成：圆角方框，悬停变主题色
            hex_color = c["accent"] if self.underMouse() else c["text_disabled"]
            pen = _CHECK_BOX_PENS.get(hex_color)
            if pen is None:
                pen = QPen(QColor(hex_color), 1.5)
                _CHECK_BOX_PENS[hex_color] = pen
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(box, radius, radius)
        painter.end()


# 完成态对勾画笔按颜色缓存：颜色随主题变（success_on），
# 每帧新建 QPen + 设 cap/join 是纯重复；QPen 值语义，多控件共享安全
_CHECK_PENS: dict = {}

# 未完成态勾选框画笔按颜色 hex 缓存：paintEvent 每帧新建 QPen 纯重复
# （hex 键随主题切换自动换 key，同 _CHECK_PEN 的类常量范式，但颜色取自
# 主题调色板，不能一次性定为类常量）
_CHECK_BOX_PENS: dict[str, QPen] = {}


class CardWidget(QFrame):
    """看板卡片"""

    signal_edit_requested = Signal(object)      # card
    signal_done_toggled = Signal(str, bool)     # card_id, done
    signal_delete_requested = Signal(str)       # card_id
    signal_card_pomo = Signal(str)              # card_id
    signal_card_archive = Signal(str)           # card_id
    signal_card_star = Signal(str)              # card_id（星标 toggle）
    signal_card_workdir_open = Signal(str)      # card_id（打开工作目录）
    signal_card_workdir_set = Signal(str)       # card_id（右键快捷设置目录）
    signal_card_duplicate = Signal(str)         # card_id（复制卡片）
    signal_card_attachment_open = Signal(str)   # card_id（打开首个附件）
    signal_label_clicked = Signal(str)          # label key（点色条过滤）
    signal_card_ctrl_clicked = Signal(str)      # card_id（Ctrl/Cmd+单击 多选）
    signal_card_shift_clicked = Signal(str)     # card_id（Shift+单击 范围多选）
    signal_drag_blocked = Signal()              # 过滤态下拖拽被拒（提示入口）
    signal_card_priority = Signal(str, int)     # card_id, priority（0=无 1=高 2=中 3=低）
    signal_card_due_set = Signal(str, str)      # card_id, ISO 日期（""=清除截止日）
    signal_card_move_to = Signal(str, str)      # card_id, target_list_id
    signal_card_save_template = Signal(str)     # card_id（另存为模板）

    # 标签色条前景色按 hex 字符串缓存：QColor(十六进制解析) 每帧每条
    # 纯重复，key 数量有限（LABEL_COLORS 的 fg 值域）；主题切换换 key 自动生效
    _label_fg_cache: dict[str, QColor] = {}

    def __init__(self, card: Card, parent=None):
        super().__init__(parent)
        self._card = card
        self._drag_start = QPoint()
        self._pressing = False
        self._hovered = False
        self._selected = False
        self._tooltip_backup: str | None = None   # 悬停备注徽章期间暂存卡片 tooltip
        self._meta_items_cache: list = []          # (文本, 色键, 是否备注) 供 tooltip 汇总
        self._delete_btn: QPushButton | None = None
        self._check_btn: QPushButton | None = None
        self._title_label: QLabel | None = None
        self._meta_badges: list[tuple[QLabel, str]] = []
        self._notes_badge: QLabel | None = None    # "≡ 有备注"徽章（悬停弹备注预览）
        self._more_badge: QLabel | None = None     # 装不下的徽章用 "…" 提示
        self._workdir_badge: QLabel | None = None  # "📂"徽章（点击打开工作目录）
        self._attach_badge: QLabel | None = None   # "📎"徽章（点击打开首个附件）
        self._fit_state: tuple = ()                # 徽章显示组合缓存（避免重复重排）
        self._fit_width: int | None = None         # 上次徽章取舍时的卡片宽度
        self._fingerprint: tuple = ()
        self._clip_path: QPainterPath | None = None   # 色条裁剪路径（按尺寸缓存）
        self._clip_size: tuple[int, int] = ()
        self._focusing_id: str | None = None    # 当前正在专注的卡片 id
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_card_menu)
        self.setCursor(Qt.PointingHandCursor)
        # 可聚焦：方向键导航 / Return 编辑 / 空格切换完成的入口（Tab 亦可遍历）
        self.setFocusPolicy(Qt.StrongFocus)
        self.rebuild()

    def set_focusing(self, card_id: str | None) -> None:
        self._focusing_id = card_id

    def set_selected(self, selected: bool) -> None:
        """批量多选高亮（动态属性驱动 QSS 边框，不重建子树）"""
        if self._selected == selected:
            return
        self._selected = selected
        _set_prop(self, "selected", bool(selected))

    # ── 右键快捷编辑子菜单（优先级 / 截止日期 / 移动到列表）──

    def _add_priority_menu(self, menu: QMenu, choices: dict) -> None:
        """优先级子菜单：当前值打勾；选中项由调用方按 action 查表派发"""
        sub = menu.addMenu(tr("优先级"))
        for value in (0, 1, 2, 3):
            name = (tr("无") if value == 0
                    else tr("{mark} {name}").format(
                        mark=AppConfig.PRIORITY_MARKS[value],
                        name=priority_name(value)))
            act = sub.addAction(name)
            act.setCheckable(True)
            act.setChecked(self._card.priority == value)
            choices[act] = ("priority", value)

    def _add_due_menu(self, menu: QMenu, choices: dict) -> None:
        """截止日期子菜单：常用相对日期 + 清除

        任意日期仍走编辑对话框（Enter/单击卡片）——快捷菜单只收编
        "今天/明天/下周"这类无需看日历的高频改期。
        """
        sub = menu.addMenu(tr("截止日期"))
        today = date.today()
        presets = [
            (tr("今天"), today),
            (tr("明天"), today + timedelta(days=1)),
            (tr("后天"), today + timedelta(days=2)),
            (tr("下周一"), today + timedelta(days=7 - today.weekday())),
            (tr("一周后"), today + timedelta(days=7)),
        ]
        current = self._card.due_date or ""
        for label, day in presets:
            iso = day.isoformat()
            act = sub.addAction(label)
            act.setCheckable(True)
            act.setChecked(current == iso)
            choices[act] = ("due", iso)
        if current:
            sub.addSeparator()
            act_clear = sub.addAction(tr("清除日期"))
            choices[act_clear] = ("due", "")

    def _add_move_menu(self, menu: QMenu, choices: dict) -> None:
        """移动到列表子菜单：列出本卡之外的全部列表（目标列顶部落位）"""
        bv = self._board_view()
        col = self._owning_column()
        if bv is None or col is None:
            return
        others = [(lid, title) for lid, title in bv.list_choices()
                  if lid != col.list_id()]
        if not others:
            return
        sub = menu.addMenu(tr("移动到列表"))
        fm = sub.fontMetrics()
        for lid, title in others:
            act = sub.addAction(fm.elidedText(title, Qt.ElideRight, 200))
            choices[act] = ("move_to", lid)

    def _show_card_menu(self, pos) -> None:
        menu = QMenu(self)
        # 子菜单选项按 action 查表派发：exec 返回的就是被触发的那个 QAction
        choices: dict = {}
        # 今日聚焦开关放首位：星标是"加入今日"的唯一入口，此前必须打开
        # 编辑对话框才能勾选，是规划链路上最贵的操作
        act_star = menu.addAction(
            tr("☆ 移出今日") if self._card.starred else tr("⭐ 加入今日"))
        menu.addSeparator()
        # 高频字段的快捷编辑：优先级/截止/换列此前一律"打开对话框→改→
        # 保存"三步走，是日常整理看板最费步数的路径
        self._add_priority_menu(menu, choices)
        self._add_due_menu(menu, choices)
        self._add_move_menu(menu, choices)
        menu.addSeparator()
        # 工作目录：有目录先给"打开"，设置项无目录也提供（含更改语义，
        # 清除走编辑对话框）
        act_workdir_open = (menu.addAction(tr("📂 打开工作目录"))
                            if self._card.workdir else None)
        act_workdir_set = menu.addAction(tr("📁 设置工作目录…"))
        act_attach_open = (menu.addAction(tr("📎 打开附件"))
                           if self._card.attachments else None)
        menu.addSeparator()
        if self._card.id == self._focusing_id:
            act_pomo = menu.addAction(tr("⏹ 停止专注"))
        else:
            act_pomo = menu.addAction(tr("▶ 开始专注 25 分钟"))
        menu.addSeparator()
        act_duplicate = menu.addAction(tr("复制卡片"))
        act_save_tpl = menu.addAction(tr("另存为模板…"))
        act_archive = menu.addAction(tr("归档"))
        # 删除也放进右键菜单：此前只有"归档"，而 ✕ 只在悬停时出现，
        # 于是"删掉这张卡"在鼠标路径上也得先悬停再瞄准一个小按钮。
        # 放在归档之后并靠分隔线隔开，避免误点（删除有撤销兜底）。
        menu.addSeparator()
        act_delete = menu.addAction(tr("删除卡片"))
        chosen = _popup_menu(menu, self.mapToGlobal(pos))
        menu.deleteLater()   # exec 返回即弃用：挂在卡片控件上会随卡片累积
        card_id = self._card.id
        kind_value = choices.get(chosen)
        if kind_value is not None:
            kind, value = kind_value
            if kind == "priority":
                self.signal_card_priority.emit(card_id, value)
            elif kind == "due":
                self.signal_card_due_set.emit(card_id, value)
            elif kind == "move_to":
                self.signal_card_move_to.emit(card_id, value)
            return
        if chosen is act_star:
            self.signal_card_star.emit(card_id)
        elif act_workdir_open is not None and chosen is act_workdir_open:
            self.signal_card_workdir_open.emit(card_id)
        elif chosen is act_workdir_set:
            self.signal_card_workdir_set.emit(card_id)
        elif act_attach_open is not None and chosen is act_attach_open:
            self.signal_card_attachment_open.emit(card_id)
        elif chosen is act_pomo:
            self.signal_card_pomo.emit(card_id)
        elif chosen is act_duplicate:
            self.signal_card_duplicate.emit(card_id)
        elif chosen is act_save_tpl:
            self.signal_card_save_template.emit(card_id)
        elif chosen is act_archive:
            self.signal_card_archive.emit(card_id)
        elif chosen is act_delete:
            self.signal_delete_requested.emit(card_id)

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
        changed = self._fingerprint != self._content_fingerprint()
        # 备注正文在指纹内：固定/悬停预览只在"本卡内容真的变了"时收起
        # ——全板 refresh 里别的卡的变更（勾选/拖动）不再把用户正在读
        # 的浮层闪掉；本卡备注被编辑时正文指纹变化，旧文本预览随之收起
        if changed and (notes_pinned_for(card.id)
                        or notes_popover_anchored_to(self._notes_badge)):
            notes_popover().hide_now()
        if changed:
            self.rebuild()

    def _content_fingerprint(self) -> tuple:
        """卡片内容指纹，用于跳过未变化卡片的重建

        必须覆盖 rebuild() 实际渲染的每个字段：遗漏字段曾让优先级/重复/
        番茄数变化时不重建，卡片一直显示旧徽章，直到其他字段变化才连带
        刷新。tests/test_board_view.py 有字段覆盖断言兜底。

        "今天"同样在渲染字段之列：截止徽标是「今天截止 / 明天截止 /
        已逾期 9/20」这类相对文案，只认 due_date 的话，跨天后任何一次
        refresh 都会被指纹短路，徽标停在昨天的文案，直到进程重启才纠正
        （本应用常驻托盘，开着过夜是常态）。无日期卡片用 0 占位，跨天
        不会连带重建全板。
        """
        c = self._card
        return (c.title, c.done, c.due_date,
                date.today().toordinal() if (c.due_date or c.fund) else 0,
                c.notes or "", bool(c.workdir),
                tuple(c.labels), c.priority, c.repeat, c.pomodoros,
                tuple((it.get("text"), bool(it.get("done")))
                      for it in c.checklist),
                len(c.attachments),
                # 资金分配监视：期限徽标同样是相对文案（超 N 天），跨天须重建
                (c.fund["role"], c.fund["kind"], c.fund.get("start_date"),
                 tuple((s.get("name"), bool(s.get("done")), s.get("date"))
                       for s in c.fund.get("steps", [])))
                if c.fund else None)

    def reapply_style(self) -> None:
        """主题切换后同步卡片内部状态，不重建子控件（保留悬停状态）

        配色由看板级样式表统一下发（见 _board_qss），这里只处理与配色无关
        的状态：删除按钮显隐、勾选框与标题完成态。
        """
        self._revalidate_hover()   # 按光标实际位置复核，顺带清掉残留悬停态
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

    def _sync_accessible_name(self) -> None:
        """给卡片与勾选框登记可读名（屏幕阅读器）

        卡片是 StrongFocus 的键盘导航核心，但自绘控件对屏幕阅读器是
        不透明黑盒；accessibleName 用"标题+关键状态"一句话描述。
        rebuild 与 set_done（勾选不触发 rebuild）两个刷新点都会调。
        """
        c = self._card
        states = []
        if c.done:
            states.append(tr("已完成"))
        if c.due_date:
            states.append(c.due_date)
        if c.priority:
            states.append(AppConfig.PRIORITY_MARKS.get(c.priority, ""))
        name = c.title + (("（" + "，".join(states) + "）") if states else "")
        self.setAccessibleName(name)
        if self._check_btn is not None:
            self._check_btn.setAccessibleName(
                tr("完成 {title}").format(title=c.title))

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
        self._workdir_badge = None
        self._attach_badge = None
        self._more_badge = None
        self._fit_state = ()
        self._fit_width = None   # 徽章重建后宽度取舍必须重算

        self._sync_accessible_name()

        card = self._card
        # 配色统一由 BoardView 的整块样式表下发（#cardFrame 等选择器），
        # 此处只登记 objectName / 动态属性，不再设局部样式表
        self.setObjectName("cardFrame")

        root = QVBoxLayout(self)
        # 左缘标签色条（paintEvent 绘制）：每条 4px，最多 4 条；留出横向空间不与标题重叠
        stripe_n = min(len(card.labels), 4)
        root.setContentsMargins(max(10, stripe_n * 4 + 7), 8, 10, 8)
        root.setSpacing(6)

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
        self._delete_btn.setToolTip(tr("删除卡片"))
        self._delete_btn.clicked.connect(
            lambda: self.signal_delete_requested.emit(self._card.id))
        # ✕ 是子控件：光标停在它上面时父卡的 Leave 会故意留住按钮（见
        # leaveEvent），之后若没有父卡 Leave 再来收尾，按钮就永久留在卡上；
        # 让 ✕ 自己的 Leave 兜底复核（事件过滤器）
        self._delete_btn.installEventFilter(self)
        self._delete_btn.hide()

        # 底部信息行（截止日期 / 备注图标）
        meta_items: list[tuple[str, str, bool]] = []    # (文本, 主题色键, 是否备注徽章)
        self._meta_items_cache = meta_items
        if card.priority:
            mark = AppConfig.PRIORITY_MARKS.get(card.priority, "")
            if mark:
                meta_items.append(
                    (mark, {1: "danger", 2: "warning",
                            3: "accent"}.get(card.priority, "accent"), False))
        # 资金分配监视徽标（期限最关键，放在普通截止之前防 4 枚上限截断）
        if card.fund:
            fund_dl = _fmt_fund_deadline(card)
            if fund_dl is not None:
                meta_items.append((fund_dl[0], fund_dl[1], False))
            meta_items.append((_fmt_fund_role(card), "accent", False))
            # 进度徽标仅牵头卡展示（配合分配不监视环节，steps 残留不算数）
            if card.fund.get("role") == "lead":
                f_done, f_total = card.fund_progress()
                if f_total:
                    meta_items.append(
                        (f"🧭 {f_done}/{f_total}",
                         "success" if f_done == f_total else "accent", False))
        if card.due_date:
            text, overdue = _fmt_due(card.due_date)
            meta_items.append((text, "danger" if overdue else "accent", False))
        if card.repeat != "never":
            meta_items.append((f"🔁 {repeat_display(card.repeat)}",
                               "text_secondary", False))
        if card.checklist:
            done, total = card.checklist_progress()
            # 全部勾完转 success 色：一眼可辨"只差归档/收尾"
            meta_items.append((f"☑ {done}/{total}",
                               "success" if done == total else "accent",
                               False))
        if card.attachments:
            n = len(card.attachments)
            meta_items.append(("📎" if n == 1 else f"📎 ×{n}",
                               "text_secondary", False))
        if card.notes:
            meta_items.append((tr("≡ 有备注"), "text_secondary", True))
        if card.pomodoros:
            meta_items.append((f"🍅 ×{card.pomodoros}", "text_secondary", False))
        if card.workdir:
            # "📂"占位文本，真实路径放 tooltip；有目录才显示，点击打开
            meta_items.append((WORKDIR_BADGE_TEXT, "text_secondary", False))

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
                elif text == WORKDIR_BADGE_TEXT:
                    # 工作目录徽章：点击打开目录；完整路径走原生 tooltip
                    # （只拦截 press，Enter/Leave 放行，tooltip 正常弹出）
                    self._workdir_badge = badge
                    badge.setCursor(Qt.PointingHandCursor)
                    badge.setToolTip(card.workdir)
                    badge.installEventFilter(self)
                elif text.startswith("📎"):
                    # 附件徽章：点击打开首个附件；全部附件名走 tooltip
                    self._attach_badge = badge
                    badge.setCursor(Qt.PointingHandCursor)
                    badge.setToolTip(
                        "\n".join(a.get("name", "") for a in card.attachments))
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
        # 悬停卡片即可看到全部元信息（含被 "…" 折叠的徽章）
        self._sync_tooltip()

        # 底部弹性：防止上面的控件（如徽章）被布局纵向拉伸满整个卡片
        root.addStretch(1)

        self._fingerprint = self._content_fingerprint()
        # 重建后删除按钮默认隐藏；光标确实还压在本卡上才恢复显示。
        # 这里刻意不信 _hovered：漏投递的 Leave 会让它长期为 True，而每次
        # 重建（数据变更/过滤/语言切换）都把它当权威重新 show() 一次——
        # 一次漏投递就会被放大成"永远不消失"。underMouse() 是当下事实，
        # 且光标在 ✕ 上时祖先链同样标记为在鼠标下，按钮不会闪。
        if self._delete_btn is not None and self.underMouse():
            self._hovered = True
            self._delete_btn.show()
            self._delete_btn.raise_()
        else:
            self._clear_hover()   # 一并解除看板跟踪，保持"✕ 可见 ⟹ 被跟踪"

    def paintEvent(self, event) -> None:
        """左缘标签色条：贴卡片左边缘的竖向色条，明显且不占布局行"""
        super().paintEvent(event)
        labels = self._card.labels[:4]
        if not labels:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # 裁剪到圆角轮廓内，色条端头跟随卡片圆角（与 #cardFrame 的 10px 对齐）；
        # 路径按尺寸缓存：hover 边框变色触发整卡重绘时免逐次重建
        size = (self.width(), self.height())
        clip = self._clip_path
        if clip is None or self._clip_size != size:
            clip = QPainterPath()
            clip.addRoundedRect(1, 1, size[0] - 2, size[1] - 2, 10, 10)
            self._clip_path = clip
            self._clip_size = size
        painter.setClipPath(clip)
        x = 1.0
        for key in labels:
            # 用标签的饱和前景色（fg）而非浅底色，保证色条醒目；
            # 深浅主题下同一饱和色都清晰
            _, fg = AppConfig.LABEL_COLORS.get(key, ("#E5E7EB", "#374151"))
            color = self._label_fg_cache.get(fg)
            if color is None:
                color = QColor(fg)
                self._label_fg_cache[fg] = color
            painter.fillRect(QRectF(x, 0, 4, self.height()), color)
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

    def _sync_tooltip(self) -> None:
        """卡片 tooltip = 标签 + 全部元徽章逐行汇总

        徽章装不下时折成 "…"，被折叠的可能是逾期日期这类关键信息；
        悬停即可看全量，不再依赖卡片宽度是否装得下。
        """
        parts: list[str] = []
        if self._card.labels:
            names = [label_display(k) for k in self._card.labels]
            parts.append(tr("标签：") + tr("、").join(names))
        # 备注徽章的文案（"≡ 有备注"）不进 tooltip：它说的正是"这卡有备注"，
        # 而 tooltip 的职责是补徽章装不下的信息，复述一遍只会挤掉真正被
        # "…" 折叠掉的内容（逾期日期、优先级）。备注正文走悬停预览浮层。
        parts.extend(text for text, _key, is_notes in self._meta_items_cache
                     if not is_notes)
        # 资金分配监视：流程摘要 + 环节耗时明细（徽章行放不下的信息在这里看全）
        if self._card.fund:
            parts.append(_fmt_fund_steps_line(self._card))
            days = self._card.fund_step_days()
            spent = [f"{name} {n}{tr('天')}" for name, n in days if n is not None]
            if spent:
                parts.append(tr("环节耗时：") + tr(" → ").join(spent))
            total_days = self._card.fund_total_days()
            if total_days is not None:
                parts.append(tr("流程总用时：{n} 天").format(n=total_days))
        if self._card.labels:
            # 左缘色条是"按标签过滤"的命中区，此前零提示：点卡片最左端
            # 会触发全板过滤，而用户并不知道刚才发生了什么
            parts.append(tr("提示：点击左缘色条可按标签筛选"))
        self.setToolTip("\n".join(parts))

    def eventFilter(self, obj, event):
        """备注徽章：Enter 弹预览浮层；Leave 延迟关闭；左键点击固定/收起"""
        if obj is self._delete_btn:
            # 光标离开 ✕ 本身：父卡的 Leave 可能早已走完（当时光标还在 ✕
            # 上而被留下），这里补一次复核，避免按钮悬在卡上不消失
            if event.type() == QEvent.Leave:
                self._revalidate_hover()
            return False
        if obj is self._workdir_badge:
            if (event.type() == QEvent.MouseButtonPress
                    and event.button() == Qt.LeftButton):
                self.signal_card_workdir_open.emit(self._card.id)
                return True   # 拦截冒泡，避免误开卡片编辑框
            return False
        if obj is self._attach_badge:
            if (event.type() == QEvent.MouseButtonPress
                    and event.button() == Qt.LeftButton):
                self.signal_card_attachment_open.emit(self._card.id)
                return True   # 拦截冒泡，避免误开卡片编辑框
            return False
        if obj is self._notes_badge:
            if event.type() == QEvent.Enter:
                pop = notes_popover()
                if pop.is_pinned():
                    return False   # 已有固定展示：悬停不抢占内容
                badge = self._notes_badge
                global_rect = QRect(badge.mapToGlobal(QPoint(0, 0)),
                                    badge.size())
                # 卡片 tooltip 会压在自绘浮层上叠字：悬停徽章期间暂存清空
                self._tooltip_backup = self.toolTip()
                self.setToolTip("")
                # 徽章控件一并交给浮层：巡检按它的实时矩形判定"光标还在
                # 徽章上"，这是漏投递 Leave（重建/滚动/拖拽）时的收口依据
                pop.show_for(self._card.notes, global_rect, badge)
                return False
            if event.type() == QEvent.Leave:
                if self._tooltip_backup is not None:
                    self.setToolTip(self._tooltip_backup)
                    self._tooltip_backup = None
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
            pop.show_pinned(self._card.id, self._card.notes, rect,
                            self._notes_badge)

    def hideEvent(self, event) -> None:
        """卡片隐藏时收起备注浮层并复位悬停态

        悬停预览/本卡固定预览关闭，固定于其他卡的预览不受牵连。
        隐藏路径（列折叠、收起为桌宠、隐藏到托盘、最小化）只派发 Hide、
        不派发 Leave，不复位的话删除按钮会在重新展开时原样回来。
        """
        if notes_pinned_for(self._card.id):
            notes_popover().hide_now()
        elif notes_popover_hovering():
            hide_notes_popover()
        self._clear_hover()
        super().hideEvent(event)

    def enterEvent(self, event) -> None:
        self._hovered = True
        if self._delete_btn is not None:
            self._show_delete_btn()
        # 告诉看板"现在悬停的是我"：它会清掉上一张卡的残留悬停态
        # （同列内从 A 划到 B 不触发列级 Enter，靠这里兜住漏投递的 Leave）
        bv = self._board_view()
        if bv is not None:
            bv._on_card_hover(self)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        # 光标可能只是移到了 ✕ 上（✕ 是子控件，父卡同样收到 Leave）：
        # 此时留住按钮，等 ✕ 自己的 Leave（eventFilter）复核收尾
        if self._delete_btn is not None and self._delete_btn.underMouse():
            self._hovered = False
        else:
            self._clear_hover()
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

    # ── 悬停态收口（删除按钮显隐只经这两个方法）──────────

    def _clear_hover(self) -> None:
        """复位悬停态并收起删除按钮

        悬停态原先只在 leaveEvent 复位，而拖拽（原生模态循环）、隐藏/收起、
        滚动等路径可能压根收不到 Leave；残留的 _hovered 又会被 rebuild()
        当成权威重新 show()，于是一次漏投递就变成"永久不消失"（会话久了
        会累积成多张卡同时残留）。确定已不在本卡上的路径统一走这里，
        失败方向固定为"藏起来"——下次真实悬停自会再显示。
        """
        self._hovered = False
        if self._delete_btn is not None:
            self._delete_btn.hide()
        bv = self._board_view()
        if bv is not None:
            bv._forget_card_hover(self)

    def _revalidate_hover(self) -> None:
        """按光标实际位置复核：既不在本卡也不在 ✕ 上就复位

        用于"可能漏掉 Leave、但不能确定"的复核点（✕ 自身 Leave、主题
        切换、拖拽结束后）。注意 Qt 在原生拖拽循环期间不保证 underMouse()
        已更新，起拖前请用 _clear_hover() 而非这里。
        """
        btn = self._delete_btn
        if self.underMouse() or (btn is not None and btn.underMouse()):
            return
        self._clear_hover()

    # ── 拖拽 ──────────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._pressing = True
            self._drag_start = event.position().toPoint()
            # 接受按下：这张卡承担本次手势（拖卡片 / 单击编辑）。此前调
            # super() 会 ignore 掉事件，它一路冒泡到主窗口，窗口的"空白处
            # 拖动窗口"逻辑就成了鼠标抓取者——拖卡片同时把窗口也拖走，
            # QDrag 与窗口移动互相打架
            event.accept()
            return
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
            # 单击（无明显位移）→ 打开编辑对话框 / 多选
            if ((event.position().toPoint() - self._drag_start)
                    .manhattanLength() <= AppConfig.CARD_DRAG_THRESHOLD):
                if event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
                    # Ctrl/Cmd+单击：加入/移出多选（优先于色条判定——
                    # 多选手势不被左缘色条热区抢占）
                    self.signal_card_ctrl_clicked.emit(self._card.id)
                elif event.modifiers() & Qt.ShiftModifier:
                    # Shift+单击：锚点到本卡的范围多选
                    self.signal_card_shift_clicked.emit(self._card.id)
                elif (key := self._label_key_at(
                        event.position().toPoint())) is not None:
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

    def _owning_column(self) -> "ListColumn | None":
        """所在列表列（父链向上找；卡片构造后父链稳定）"""
        p = self.parent()
        while p is not None and not isinstance(p, ListColumn):
            p = p.parent()
        return p

    def _show_move_menu(self) -> None:
        """键盘 M：只弹"移动到列表"菜单（右键菜单的子集，免鼠标绕行）"""
        menu = QMenu(self)
        choices: dict = {}
        self._add_move_menu(menu, choices)
        if not choices:
            menu.deleteLater()
            return
        chosen = _popup_menu(menu, self.mapToGlobal(QPoint(0, self.height())))
        menu.deleteLater()
        value = choices.get(chosen)
        if value is not None:
            self.signal_card_move_to.emit(self._card.id, value[1])

    def keyPressEvent(self, event) -> None:
        """键盘操作卡片：Return 编辑 / 空格切换完成 / 方向键移动焦点

        1/2/3/0 直接设优先级、M 换列：这两件事在整理看板时最频繁，此前
        每次都要"打开对话框→改→保存"，键盘用户尤其吃亏。
        Delete/Backspace 删除：删除此前**完全没有键盘路径**——右上角的 ✕
        只在悬停时创建并显示，而隐藏控件连 Tab 都跳过；右键菜单里也只有
        "归档"没有"删除"。删除本身有撤销兜底，不需要二次确认。
        """
        key = event.key()
        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            self.signal_delete_requested.emit(self._card.id)
            event.accept()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self.signal_edit_requested.emit(self._card)
            event.accept()
            return
        if key == Qt.Key_Space:
            self.signal_done_toggled.emit(self._card.id, not self._card.done)
            event.accept()
            return
        if key in (Qt.Key_1, Qt.Key_2, Qt.Key_3):
            self.signal_card_priority.emit(self._card.id, key - Qt.Key_0)
            event.accept()
            return
        if key == Qt.Key_0:
            self.signal_card_priority.emit(self._card.id, 0)
            event.accept()
            return
        if key == Qt.Key_M:
            self._show_move_menu()
            event.accept()
            return
        col = self._owning_column()
        if col is not None:
            if key == Qt.Key_Up:
                col.move_card_focus(self, -1)
                event.accept()
                return
            if key == Qt.Key_Down:
                col.move_card_focus(self, 1)
                event.accept()
                return
            if key in (Qt.Key_Left, Qt.Key_Right):
                bv = self._board_view()
                if bv is not None and bv.move_focus_horizontally(
                        col, self, key == Qt.Key_Right):
                    event.accept()
                    return
        super().keyPressEvent(event)

    def _board_view(self):
        p = self.parent()
        while p is not None and not isinstance(p, BoardView):
            p = p.parent()
        return p

    def _start_drag(self) -> None:
        col = self._owning_column()
        if col is not None and col.is_filtered():
            # 过滤态落点不可靠（列已禁 drop），与其让用户拖一圈被禁止
            # 光标打回，不如起拖前直接说明原因
            self.signal_drag_blocked.emit()
            return
        mime = QMimeData()
        mime.setData(MIME_CARD, self._card.id.encode("utf-8"))
        # 多选成组：拖选中集合里的任一张 = 搬走整组。多选是"我对这几张
        # 一起做事"的宣言，此前拖拽却只搬手上那一张，只能逐张拖或改用
        # 批量栏的"→ 移动"（落点只能到列首）
        group = self._group_drag_ids()
        if group:
            mime.setData(MIME_CARDS,
                         json.dumps(group).encode("utf-8"))
        # 起拖前先复位悬停态：drag.exec() 是原生模态循环，期间与之后都不
        # 保证派发 Enter/Leave；同列重排还会复用同一控件（refresh_cards），
        # 残留的 _hovered 会让 ✕ 赖在卡上不走。顺带让拖拽预览截图不再带着
        # 红色 ✕（此前 grab() 会把正显示的删除按钮一起截进去）
        self._clear_hover()
        drag = QDrag(self)
        drag.setMimeData(mime)
        # 拖拽预览：卡片自身截图（成组时右上角叠一个张数角标）
        pixmap = self.grab()
        if group:
            pixmap = _with_group_badge(pixmap, len(group))
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(pixmap.width() // 2, 14))
        drag.exec(Qt.MoveAction)
        self._pressing = False
        # 原生循环结束后保守复核一次（此时 underMouse 可信，光标若仍在
        # 本卡上则保持隐藏也无妨——下一次真实 Enter 会重新显示）
        self._revalidate_hover()

    def _group_drag_ids(self) -> list[str]:
        """本卡属于当前多选、且选中不止一张时返回整组 id（否则空表）

        返回顺序按列内实际顺序（不是点击顺序），落点插入后与拖拽前的
        相对次序一致。
        """
        bv = self._board_view()
        col = self._owning_column()
        if bv is None or col is None:
            return []
        selected = bv.selection_ids()
        if len(selected) < 2 or self._card.id not in selected:
            return []
        sel = set(selected)
        return [c.id for c in col.board_list().cards if c.id in sel]


def _with_group_badge(pixmap: QPixmap, count: int) -> QPixmap:
    """在拖影右上角叠一个"N"角标（成组拖拽"手上拿着几张"的即时反馈）"""
    out = QPixmap(pixmap)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing)
    d = 22
    x = max(0, out.width() - d - 4)
    rect = QRectF(x, 4, d, d)
    painter.setPen(Qt.NoPen)
    painter.setBrush(to_qcolor(AppTheme.colors()["accent"]))
    painter.drawEllipse(rect)
    painter.setPen(QColor("#FFFFFF"))
    font = painter.font()
    font.setBold(True)
    font.setPointSizeF(max(7.0, d * 0.42))
    painter.setFont(font)
    painter.drawText(rect, Qt.AlignCenter, str(count))
    painter.end()
    return out


def _begin_list_drag(source: QWidget, board_list: BoardList) -> None:
    """启动整列拖拽：限高缩略拖影 + MIME_LIST 携带列表 id

    列头与列身（空白处）共用同一入口：两处都是"搬走整列"的手势，只是一处
    从标题抓、一处从列内空白抓。

    拖影不再 grab() 整列：几百卡的长列位图可达数 MB 且每次起拖都现截，
    低配机上起拖有顿挫感。改为"列头截图 + 限高灰底"的缩略 ghost——
    拖拽要传达的只是"这一列正在移动"，卡片明细本来就看不见（半透明）。
    """
    mime = QMimeData()
    mime.setData(MIME_LIST, board_list.id.encode("utf-8"))
    drag = QDrag(source)
    drag.setMimeData(mime)
    max_h = AppConfig.LIST_DRAG_GHOST_MAX_H
    pixmap = source.grab()
    if pixmap.height() > max_h:
        # 列头原样保留，其余以圆角灰底补足到 max_h：视觉上是"缩短的列"
        header = getattr(source, "_header", None)
        header_h = header.height() if header is not None else 0
        header_h = min(max(header_h, 0), max_h)
        ghost = QPixmap(pixmap.width(), max_h)
        ghost.fill(Qt.transparent)
        painter = QPainter(ghost)
        painter.setRenderHint(QPainter.Antialiasing)
        if header_h > 0:
            painter.drawPixmap(0, 0,
                               pixmap.copy(0, 0, pixmap.width(), header_h))
        body = QRect(0, header_h, pixmap.width(), max_h - header_h)
        painter.setPen(Qt.NoPen)
        painter.setBrush(to_qcolor(AppTheme.colors()["mask_hover"]))
        painter.drawRoundedRect(body.adjusted(4, 2, -4, -4), 8, 8)
        painter.end()
        pixmap = ghost
    drag.setPixmap(pixmap)
    drag.setHotSpot(QPoint(pixmap.width() // 2, min(20, pixmap.height() // 2)))
    drag.exec(Qt.MoveAction)


class _TitleLabel(QLabel):
    """列表标题标签：双击进入重命名（子类覆写，替代实例 monkeypatch）"""

    def __init__(self, text: str, header: "ListHeader", parent=None):
        super().__init__(text, parent)
        self._header = header

    def mouseDoubleClickEvent(self, event) -> None:
        col = self._header.parent()
        if isinstance(col, ListColumn) and col.is_collapsed():
            return   # 折叠态首次单击已展开，双击不再弹重命名编辑框
        self._header._start_rename(event)


class ListHeader(QWidget):
    """列表头部：标题（双击重命名）+ 计数 + "⋯"菜单（重命名/删除）"""

    signal_title_changed = Signal(str, str)   # list_id, new_title
    signal_delete_requested = Signal(str)     # list_id
    signal_fund_watch = Signal(str, bool)     # list_id, enabled 资金分配监视
    signal_fund_export = Signal(str)          # list_id 导出资金记录 Excel
    signal_duplicate_requested = Signal(str)  # list_id（复制列表含卡片）

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
        dot.setStyleSheet(
            f"background: {accent}; border-radius: {tokens.circle_radius(8)}px;")
        layout.addWidget(dot)

        # 资金分配监视泳道徽记（启用监视的列才显示）
        self._fund_mark = QLabel("💰")
        self._fund_mark.setToolTip(tr("资金分配监视泳道"))
        self._fund_mark.setVisible(board_list.fund_watch)
        layout.addWidget(self._fund_mark)

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
        self._collapse_btn.setToolTip(tr("折叠 / 展开列表"))
        self._collapse_btn.clicked.connect(self._on_collapse_clicked)
        layout.addWidget(self._collapse_btn)

        self._menu_btn = _HeaderMenuButton(self)
        self._menu_btn.setToolTip(tr("列表操作"))
        self._menu_btn.clicked.connect(self._show_menu)
        layout.addWidget(self._menu_btn)
        # 不隐藏、只"幽灵化"：布局空间常驻，悬停才点亮，避免列头高度闪动

        # 列表操作菜单
        self._menu = QMenu(self)
        self._act_rename = QAction(tr("重命名"), self._menu)
        self._act_duplicate = QAction(tr("复制列表（含卡片）"), self._menu)
        self._act_delete = QAction(tr("删除列表"), self._menu)
        self._act_fund_watch = QAction(tr("资金分配监视"), self._menu)
        self._act_fund_watch.setCheckable(True)
        self._act_fund_watch.setChecked(board_list.fund_watch)
        # 导出 Excel（仅资金监视泳道可见）
        self._act_fund_export = QAction(tr("导出 Excel…"), self._menu)
        self._act_fund_export.setVisible(board_list.fund_watch)
        self._menu.addAction(self._act_rename)
        self._menu.addAction(self._act_duplicate)
        self._menu.addSeparator()
        self._menu.addAction(self._act_fund_watch)
        self._menu.addAction(self._act_fund_export)
        self._menu.addAction(self._act_delete)
        self._act_rename.triggered.connect(self._start_rename)
        self._act_duplicate.triggered.connect(
            lambda: self.signal_duplicate_requested.emit(self._lst.id))
        self._act_delete.triggered.connect(
            lambda: self.signal_delete_requested.emit(self._lst.id))
        self._act_fund_watch.toggled.connect(self._on_fund_watch_toggled)
        self._act_fund_export.triggered.connect(
            lambda: self.signal_fund_export.emit(self._lst.id))
        # 菜单关闭后按光标实际位置决定是否保持点亮（避免菜单开着时误熄灭）
        self._menu.aboutToHide.connect(
            lambda: self._menu_btn.set_active(self.underMouse()))

    def _on_fund_watch_toggled(self, checked: bool) -> None:
        self._fund_mark.setVisible(checked)
        self.signal_fund_watch.emit(self._lst.id, checked)

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

    def update_count(self, n: int, total: int | None = None) -> None:
        """列头计数：过滤态显示「命中/总数」，避免被误读成"卡片丢了" """
        self._count_label.setText(f"{n}/{total}" if total is not None
                                  and total != n else str(n))

    def set_list(self, board_list: BoardList) -> None:
        """增量刷新：重指向模型对象并同步标题文本"""
        self._lst = board_list
        self._title_label.setText(board_list.title)
        # 资金监视标记与菜单勾选随模型同步（setChecked 同值不触发 toggled，
        # 不会与 _on_fund_watch_toggled 成环）
        self._fund_mark.setVisible(board_list.fund_watch)
        self._act_fund_watch.setChecked(board_list.fund_watch)
        self._act_fund_export.setVisible(board_list.fund_watch)

    def reapply_theme(self) -> None:
        """主题切换后同步头部状态（配色由看板级样式表统一下发）"""
        col = self.parent()
        self.set_collapsed_mark(
            col.is_collapsed() if isinstance(col, ListColumn) else False)
        self._menu_btn.reapply()

    def retexts(self) -> None:
        """语言切换：刷新列头菜单与提示文案"""
        self._act_rename.setText(tr("重命名"))
        self._act_delete.setText(tr("删除列表"))
        self._act_fund_watch.setText(tr("资金分配监视"))
        self._act_fund_export.setText(tr("导出 Excel…"))
        self._fund_mark.setToolTip(tr("资金分配监视泳道"))
        self._collapse_btn.setToolTip(tr("折叠 / 展开列表"))
        self._menu_btn.setToolTip(tr("列表操作"))

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
            # 接受按下：本次手势归列头（拖列 / 单击展开折叠列）。不接受就会
            # 冒泡到主窗口——窗口的"空白处拖动"逻辑会拿走鼠标抓取权，于是
            # 拖列变成拖窗，列头连后续的 move/release 都收不到
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        col = self.parent()
        if (self._drag_press_pos is not None
                and event.buttons() & Qt.LeftButton
                and isinstance(col, ListColumn)
                and (event.position().toPoint() - self._drag_press_pos)
                .manhattanLength() > AppConfig.LIST_DRAG_THRESHOLD):
            self._drag_press_pos = None      # 只触发一次
            if col.is_filtered():
                # 与卡片拖拽同款反馈：无声无息会让用户反复试
                col.signal_drag_blocked.emit()
                return
            self._start_list_drag()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        # 折叠列：单击头部任意处（按住未拖动）即展开——标题是头部最宽的
        # 命中区，排除它会让"点标题没反应"像列卡死了
        if (event.button() == Qt.LeftButton
                and self._drag_press_pos is not None
                and _ACTIVE_RENAME is None):
            col = self.parent()
            if isinstance(col, ListColumn) and col.is_collapsed():
                col.toggle_collapsed()
        self._drag_press_pos = None
        super().mouseReleaseEvent(event)

    def _start_list_drag(self) -> None:
        """启动整列拖拽（列头）"""
        col = self.parent()
        if isinstance(col, ListColumn):
            _begin_list_drag(self, self._lst)


class _RenameEdit(QLineEdit):
    """列表标题重命名编辑器（全局同时只存在一个，见 _ACTIVE_RENAME）"""

    def __init__(self, header: "ListHeader"):
        super().__init__(header._lst.title, header._title_label)
        self._header = header
        self.setObjectName("renameEdit")
        self.returnPressed.connect(self.commit)
        # 注意：不挂 editingFinished（失焦提交）——Qt.Tool 窗口焦点链不可靠，
        # 关闭时机统一由点击过滤器 / Esc / 折叠 / 隐藏等显式路径驱动

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Tab, Qt.Key_Backtab):
            # Tab 失焦也要收口：否则编辑框悬空盖在标题上（视觉重影）。
            # 先提交（此刻编辑器仍是焦点件），再按 Tab 方向移交焦点
            forward = event.key() != Qt.Key_Backtab
            self.commit()
            self._header.focusNextPrevChild(forward)
            event.accept()
            return
        super().keyPressEvent(event)

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


class _GearButton(QPushButton):
    """自绘齿轮图标按钮（⚙ 字形在部分平台缺字形，改矢量绘制）"""

    # 环−孔布尔运算结果按控件尺寸缓存：paintEvent 每帧 subtracted 纯浪费
    # （同文件 _ThemeToggleButton._moon_path_cache 同款范式）
    _ring_cache: dict[tuple[int, int], QPainterPath] = {}

    def __init__(self, parent=None):
        super().__init__(parent)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        center = QPointF(w / 2, h / 2)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(AppTheme.colors()["text_primary"]))
        ring = self._ring_cache.get((w, h))
        if ring is None:
            outer = QPainterPath()
            outer.addEllipse(center, 5.2, 5.2)
            hole = QPainterPath()
            hole.addEllipse(center, 2.2, 2.2)
            ring = outer.subtracted(hole)
            if len(self._ring_cache) > 16:
                self._ring_cache.clear()
            self._ring_cache[(w, h)] = ring
        painter.drawPath(ring)
        for i in range(8):
            painter.save()
            painter.translate(center)
            painter.rotate(i * 45)
            painter.drawRect(QRectF(-1.4, -8.4, 2.8, 3.4))
            painter.restore()
        painter.end()


class _ThemeToggleButton(QPushButton):
    """自绘 日/月 图标的主题切换按钮（🌙/☀️ emoji 在部分平台缺字形，改矢量绘制）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode = "light"

    def set_mode(self, mode: str) -> None:
        if mode != self._mode:
            self._mode = mode
            self.update()

    # 月牙路径（含 subtracted 结果）按控件尺寸缓存：paintEvent 每次重建
    # QPainterPath + 布尔运算纯浪费
    _moon_path_cache: dict[tuple[int, int], QPainterPath] = {}

    def _moon_path(self) -> QPainterPath:
        """月牙路径按尺寸缓存：连 subtracted 布尔运算结果一并缓存，
        paintEvent 每帧只取不建"""
        key = (self.width(), self.height())
        path = self._moon_path_cache.get(key)
        if path is None:
            cx, cy = key[0] / 2, key[1] / 2
            full = QPainterPath()
            full.addEllipse(cx - 5.5, cy - 5.5, 11.0, 11.0)
            cut = QPainterPath()
            cut.addEllipse(cx - 1.5, cy - 8.0, 11.0, 11.0)
            path = full.subtracted(cut)
            self._moon_path_cache[key] = path
        return path

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        c = AppTheme.colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        glyph = QColor(c["text_primary"])
        if self._mode == "light":
            # 浅色态显示月亮（点击切深色）
            moon = self._moon_path()
            painter.setPen(Qt.NoPen)
            painter.setBrush(glyph)
            painter.drawPath(moon)
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


class _ElidedButton(QPushButton):
    """宽度不足时省略号截断的按钮（看板名用）

    普通 QPushButton 的文字在宽度不足时会被硬裁（窄窗下看板名只剩
    "的看板"这种半截文本），故按实际宽度自行 elide。
    sizeHint 必须按**完整文本**算：若直接吃基类实现（基于已被 elide
    的短文本），文本一缩、sizeHint 跟着缩，会自我收缩成 "…"。
    """

    def __init__(self, text: str = "", parent=None, suffix: str = "",
                 max_width: int = 260):
        super().__init__(parent)
        self._full = text
        self._suffix = suffix
        self._max_width = max_width
        self.setSizePolicy(QSizePolicy.Policy.Maximum,
                           QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(88)

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        natural = fm.horizontalAdvance(self._full + self._suffix) + 26
        return QSize(min(natural, self._max_width),
                     super().sizeHint().height())

    def set_full_text(self, text: str) -> None:
        self._full = text
        self.updateGeometry()
        self._apply_elide()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        avail = self.width() - 24      # 减去左右内边距
        if avail <= 0:
            self.setText(self._full + self._suffix)
            return
        fm = self.fontMetrics()
        self.setText(fm.elidedText(self._full, Qt.ElideRight, avail)
                     + self._suffix)


class _CalendarIconButton(QPushButton):
    """自绘日历图标按钮（📅 是彩色 emoji，与同排的线性图标/文字按钮不同调）"""

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(AppTheme.colors()["text_primary"]), 1.4)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        cx, cy = self.width() / 2, self.height() / 2
        body = QRectF(cx - 6.0, cy - 4.8, 12.0, 10.6)
        painter.drawRoundedRect(body, 2.0, 2.0)
        # 头部分隔线 + 两个挂钩（一眼区别于齿轮/月亮）
        painter.drawLine(QPointF(body.left(), cy - 1.6),
                         QPointF(body.right(), cy - 1.6))
        painter.drawLine(QPointF(cx - 3.4, cy - 7.0),
                         QPointF(cx - 3.4, cy - 4.8))
        painter.drawLine(QPointF(cx + 3.4, cy - 7.0),
                         QPointF(cx + 3.4, cy - 4.8))
        painter.end()


class ListColumn(QFrame):
    """看板列表列（头部 + 卡片区 + 添加按钮）"""

    signal_card_edit = Signal(object)
    signal_card_done = Signal(str, bool)
    signal_card_delete = Signal(str)
    signal_card_move = Signal(str, str, int)   # card_id, target_list_id, index
    signal_cards_move = Signal(list, str, int)  # card_ids, target_list_id, index（成组）
    signal_add_card = Signal(str)              # list_id
    signal_add_from_template = Signal(str, str)  # list_id, 模板名
    signal_title_changed = Signal(str, str)
    signal_delete_list = Signal(str)
    signal_card_pomo = Signal(str)             # card_id
    signal_card_archive = Signal(str)          # card_id
    signal_card_star = Signal(str)             # card_id（星标 toggle）
    signal_card_workdir_open = Signal(str)     # card_id（打开工作目录）
    signal_card_workdir_set = Signal(str)      # card_id（右键快捷设置目录）
    signal_card_duplicate = Signal(str)        # card_id（复制卡片）
    signal_card_attachment_open = Signal(str)  # card_id（打开首个附件）
    signal_card_ctrl_clicked = Signal(str)     # card_id（Ctrl/Cmd+单击 多选）
    signal_card_shift_clicked = Signal(str)    # card_id（Shift+单击 范围多选）
    signal_card_priority = Signal(str, int)    # card_id, priority（右键/键盘快捷编辑）
    signal_card_due_set = Signal(str, str)     # card_id, ISO 日期（""=清除）
    signal_card_move_to = Signal(str, str)     # card_id, target_list_id
    signal_card_save_template = Signal(str)    # card_id（另存为模板）
    signal_label_clicked = Signal(str)         # label key（点色条过滤）
    signal_drag_blocked = Signal()             # 过滤态下拖拽被拒（→ 看板 toast）
    signal_list_move = Signal(str, str, bool)  # moved_list_id, target_list_id, insert_before
    signal_collapsed_changed = Signal(str, bool)  # list_id, collapsed
    signal_fund_watch = Signal(str, bool)      # list_id, enabled 资金分配监视
    signal_fund_export = Signal(str)           # list_id 导出资金记录 Excel
    signal_duplicate_requested = Signal(str)   # list_id（复制列表含卡片）
    signal_files_dropped = Signal(str, list)   # list_id, [本地文件路径]（拖文件建卡）

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
        # 落点指示线当前指向的插入位（overlay 不占布局，见 _update_drop_indicator_at）
        self._drop_indicator_index: int | None = None
        # 整列拖拽起点（列身空白处按下；None=未按下）
        self._drag_press_pos: QPoint | None = None
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
        self._header.signal_fund_watch.connect(self.signal_fund_watch)
        self._header.signal_fund_export.connect(self.signal_fund_export)
        self._header.signal_duplicate_requested.connect(
            self.signal_duplicate_requested)
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

        # 卡片拖拽插入指示线（overlay 定位，不占布局——见
        # _update_drop_indicator_at 顶部的说明）
        self._drop_indicator = QFrame(self._cards_host)
        self._drop_indicator.setObjectName("dropIndicator")
        self._drop_indicator.setFixedHeight(3)
        self._drop_indicator.hide()

        # 拖拽到视口边缘的自动滚动（横向看板区 + 本列纵向）
        self._drag_hovering = False
        self._auto_scroll_timer = QTimer(self)
        self._auto_scroll_timer.setInterval(AppConfig.DRAG_AUTO_SCROLL_MS)
        self._auto_scroll_timer.timeout.connect(self._auto_scroll_tick)

        # 添加按钮
        self._add_btn = AddCardButton(tr("+ 添加卡片"))
        self._add_btn.clicked.connect(self._on_add_clicked)
        root.addWidget(self._add_btn)

        self.refresh_cards()

    # ── 添加卡片：有模板时先给一个选择 ────────────────────

    def _on_add_clicked(self) -> None:
        """列尾「+ 添加卡片」：有模板时先弹"空白卡片 / 模板…"菜单

        模板的入口放在这里而不是工具栏：它是"新建一张卡"的一种方式，
        与"空白卡片"并列最自然；没有模板时退化为原来的直接新建，
        老用户的操作一步都不会多。
        """
        names = self._template_names()
        if not names:
            self.signal_add_card.emit(self._lst.id)
            return
        menu = QMenu(self)
        picked: dict = {}
        act_blank = menu.addAction(tr("空白卡片"))
        picked[act_blank] = ""
        menu.addSeparator()
        fm = menu.fontMetrics()
        for name in names:
            act = menu.addAction(tr("模板：{name}").format(name=name))
            picked[act] = name
        chosen = _popup_menu(
            menu, self._add_btn.mapToGlobal(
                QPoint(0, -menu.sizeHint().height() - 4)))
        menu.deleteLater()
        if chosen is None:
            return
        if chosen is act_blank:
            self.signal_add_card.emit(self._lst.id)
            return
        name = picked.get(chosen)
        if name:
            self.signal_add_from_template.emit(self._lst.id, name)

    def _template_names(self) -> list[str]:
        bv = self._board_view()
        return bv.template_names() if bv is not None else []

    # ── 整列拖拽：列身空白处按下并拖动即搬走整列 ──────────────

    def mousePressEvent(self, event) -> None:
        """列身空白处按下 → 记起点并**接受**事件

        卡片、"+ 添加卡片"、列头都有各自的按下处理并各自接受，冒泡到这里
        的只剩列内空白（卡片下方、列边距）。这里必须接受：不接受就会一路
        冒泡到主窗口，被"空白处拖动整个窗口"接手——列内空白拖动应当是搬列。
        """
        if event.button() == Qt.LeftButton and _ACTIVE_RENAME is None:
            self._drag_press_pos = event.position().toPoint()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (self._drag_press_pos is not None
                and event.buttons() & Qt.LeftButton
                and (event.position().toPoint() - self._drag_press_pos)
                .manhattanLength() > AppConfig.LIST_DRAG_THRESHOLD):
            self._drag_press_pos = None      # 只触发一次
            if self.is_filtered():
                self.signal_drag_blocked.emit()   # 同列头：给解释而非沉默
                return
            _begin_list_drag(self, self._lst)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_press_pos = None
        super().mouseReleaseEvent(event)

    # ── 数据刷新 ──────────────────────────────────────────

    def list_id(self) -> str:
        return self._lst.id

    def board_list(self) -> BoardList:
        """当前列表模型（卡片顺序查询用：成组拖拽按列内顺序取 id）"""
        return self._lst

    def set_list(self, board_list: BoardList,
                 visible_cards: list[Card] | None = None) -> None:
        """增量刷新：重指向模型对象并同步整列内容（可带过滤子集）"""
        self._lst = board_list
        self._visible_cards = visible_cards
        # 过滤态落点不可靠，禁用拖放。折叠列**保持可接收**：拖到折叠列
        # 会先自动展开再落点（见 dragEnterEvent），否则"看板列太多先折起来"
        # 的状态下就没法往这些列拖卡片
        self.setAcceptDrops(visible_cards is None)
        self._header.set_list(board_list)
        self.refresh_cards()

    def set_visible_cards(self, visible_cards: list[Card] | None) -> None:
        """搜索过滤：只更新可见卡片子集（可见集相同则跳过，避免逐键刷新）

        短路比较按 id 序而非 dataclass 全字段深比较：Card 的 checklist/
        attachments 是深列表，逐键输入时每列全量深比较比渲染还贵；
        这里只负责"哪些卡可见"，字段级变更由 refresh_cards 的内容指纹兜底。
        None（未过滤）与 []（过滤后为空）语义不同（拖放开关），不视为相同。
        """
        old = self._visible_cards
        if (old is None) == (visible_cards is None):
            if old is None or [c.id for c in old] == [c.id for c in visible_cards]:
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
        cw.signal_card_workdir_open.connect(self.signal_card_workdir_open)
        cw.signal_card_workdir_set.connect(self.signal_card_workdir_set)
        cw.signal_card_duplicate.connect(self.signal_card_duplicate)
        cw.signal_card_attachment_open.connect(self.signal_card_attachment_open)
        cw.signal_card_ctrl_clicked.connect(self.signal_card_ctrl_clicked)
        cw.signal_card_shift_clicked.connect(self.signal_card_shift_clicked)
        cw.signal_card_priority.connect(self.signal_card_priority)
        cw.signal_card_due_set.connect(self.signal_card_due_set)
        cw.signal_card_move_to.connect(self.signal_card_move_to)
        cw.signal_card_save_template.connect(self.signal_card_save_template)
        cw.signal_label_clicked.connect(self.signal_label_clicked)
        cw.signal_drag_blocked.connect(self.signal_drag_blocked)
        return cw

    def move_card_focus(self, from_card: "CardWidget", step: int) -> None:
        """列内方向键导航：移动键盘焦点到相邻卡片（越界停住）"""
        try:
            i = self._card_widgets.index(from_card)
        except ValueError:
            return
        j = i + step
        if 0 <= j < len(self._card_widgets):
            w = self._card_widgets[j]
            w.setFocus()
            # programmatic setFocus 不会触发 QScrollArea 自动滚动，
            # 长列里焦点会静默移出可视区，须显式滚到可见
            self._scroll.ensureWidgetVisible(w)

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
                hint_text = (tr("没有匹配的卡片") if self._visible_cards is not None
                             else tr("还没有卡片，点击下方添加"))
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

        self._header.update_count(
            len(cards),
            # "命中/总数"的总数排除归档卡（不渲染也不该进分母）
            total=(sum(1 for c in self._lst.cards if not c.archived)
                   if self._visible_cards is not None else None))
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
            # 被悬停跟踪的卡销毁前先解除跟踪：动画退场期间收不到 Leave，
            # 残留的死 wrapper 会让下一次悬停的 _clear_hover 抛 RuntimeError
            cw._clear_hover()
            if animate:
                cw.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                cw.setParent(self._cards_host)   # 同父调用不改几何，仅确保归属
                motion.fade_out(cw, AppConfig.CARD_EXIT_ANIM_MS,
                                on_finished=cw.deleteLater)
            else:
                cw.setParent(None)
                cw.deleteLater()

    # ── 样式 ──────────────────────────────────────────────

    def retexts(self) -> None:
        """语言切换：刷新列头、添加按钮与空态提示文案"""
        self._header.retexts()
        if self._add_btn is not None:
            self._add_btn.setText(tr("+ 添加卡片"))
        if self._hint is not None:
            self._hint.setText(
                tr("没有匹配的卡片") if self._visible_cards is not None
                else tr("还没有卡片，点击下方添加"))

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
        # 折叠列仍接收拖放：进入时先自动展开（见 dragEnterEvent）
        guarded(lambda: self.setAcceptDrops(self._visible_cards is None))
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

    # ── 悬停态清扫（列级兜底）─────────────────────────────

    def _clear_cards_hover(self) -> None:
        """清掉本列全部卡片的悬停态（删除按钮显隐的兜底收口）

        Qt 先派发列的 Enter、再派发卡的 Enter（实测），所以这里即使清掉
        光标正压着的那张卡，紧接着它自己的 Enter 会把按钮显示回来。
        "同列从 A 划到 B"不触发列级 Enter，那一段由看板的悬停跟踪兜住
        （CardWidget.enterEvent → BoardView._on_card_hover）。
        """
        for cw in self._card_widgets:
            cw._clear_hover()

    def enterEvent(self, event) -> None:
        self._clear_cards_hover()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        """光标离开整列：本列不可能再有悬停卡，一律复位

        "鼠标移开后 ✕ 不消失"最直接的兜底路径（列间移动是最频繁的动作）
        """
        self._clear_cards_hover()
        super().leaveEvent(event)

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
        self.setAcceptDrops(self._visible_cards is None)
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

    def _raw_index_from_visible(self, vis_index: int) -> int:
        """可见序插入位 → 卡片列表 raw 序（含归档卡）插入位

        列内可能含归档卡（仍在 lst.cards 里、不渲染）：控制器按 raw 序
        插入，直接用可见序会在插入点之前有归档卡时落点整体错位。
        """
        visible = self._visible_cards
        if visible is None:
            return vis_index
        cards = self._lst.cards
        if vis_index >= len(visible):
            return len(cards)
        try:
            return cards.index(visible[vis_index])
        except ValueError:
            return vis_index   # 理论不可达：可见集取自 cards

    # ── 拖拽落点指示线 + 视口边缘自动滚动 ───────────────────

    def _column_y_from_event(self, event) -> int:
        """把 dragEvent 的列内坐标换算为全局 y（落点判定用）"""
        return (event.position().toPoint().y()
                + self.mapToGlobal(QPoint(0, 0)).y())

    def _update_drop_indicator_at(self, global_y: int) -> None:
        """把插入指示线移到 global_y 对应的卡片间隙（位置不变则跳过）

        指示线不占布局（独立子控件按几何定位）：insertWidget 进卡片布局
        会把下方卡片整体下移十几像素，而 _drop_mids 快照是进入拖拽时的
        "无指示线"状态——光标落在受影响带内时二分判定会差一位。
        """
        index = self._drop_index_from_y(global_y)
        if index == self._drop_indicator_index:
            self._drop_indicator.show()
            return
        self._drop_indicator_index = index
        cards = self._card_widgets
        if not cards:
            y = 0
        elif index >= len(cards):
            y = cards[-1].geometry().bottom() + 1
        else:
            y = cards[index].geometry().top() - 2
        self._drop_indicator.setParent(self._cards_host)
        self._drop_indicator.setGeometry(
            0, y, self._cards_host.width(), self._drop_indicator.height())
        self._drop_indicator.show()
        self._drop_indicator.raise_()

    def _hide_drop_indicator(self) -> None:
        self._drop_indicator_index = None
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

    def _board_view(self) -> "BoardView | None":
        """父链上的看板视图（列表拖拽指示条宿主；构造后父链稳定）"""
        p = self.parent()
        while p is not None and not isinstance(p, BoardView):
            p = p.parent()
        return p

    def _update_list_drop_indicator(self, event) -> None:
        """整列拖拽：在目标列前/后的列间隙显示竖向插入指示条

        卡片拖拽有插入线而整列拖拽只有落点高亮时，插前/插后只能靠
        松手验证；补一根与卡片插入线同款的竖条（落点列左/右半判定
        与 dropEvent 的 insert_before 逻辑一致）。
        """
        bv = self._board_view()
        if bv is not None:
            bv.show_list_drop_indicator(
                self, event.position().toPoint().x() < self.width() // 2)

    def _hide_list_drop_indicator(self) -> None:
        bv = self._board_view()
        if bv is not None:
            bv.hide_list_drop_indicator()

    def dragEnterEvent(self, event) -> None:
        if self._is_card_drag(event):
            self._expand_for_drop()
        if self._file_paths_from(event) is not None:
            # 外部文件：拖到列上 = 在该列建卡并把文件作为附件
            self._set_drop_highlight(True)
            event.acceptProposedAction()
            return
        if event.mimeData().hasFormat(MIME_CARD) or event.mimeData().hasFormat(
                MIME_CARDS):
            self._drag_hovering = True
            self._rebuild_drop_mids()   # 拖拽期间布局不变，快照全程有效
            self._update_drop_indicator_at(self._column_y_from_event(event))
            self._auto_scroll_timer.start()
            event.acceptProposedAction()
        elif event.mimeData().hasFormat(MIME_LIST):
            self._set_drop_highlight(True)
            self._update_list_drop_indicator(event)
            event.acceptProposedAction()

    def _is_card_drag(self, event) -> bool:
        """会不会真的落在这列上（卡片拖拽 / 带本地文件的拖拽）

        不能只看 hasUrls()：拖一段文本或空 URL 列表同样会带着 urls 格式，
        但它落不下来——列会白白展开一次。
        """
        mime = event.mimeData()
        if mime.hasFormat(MIME_CARD) or mime.hasFormat(MIME_CARDS):
            return True
        return self._file_paths_from(event) is not None

    def _expand_for_drop(self) -> None:
        """卡片/文件拖到折叠列上：先展开再落点

        此前折叠列直接禁用拖放，理由是"拖进去落在隐藏卡之间会消失"。
        自动展开从根上消掉这个歧义：落点始终是用户看得见的卡片列表。
        """
        if self._collapsed:
            self.set_collapsed(False)

    @staticmethod
    def _file_paths_from(event) -> list | None:
        """拖拽数据里的本地文件路径（非文件拖拽返回 None）

        只认 file:// 的本地路径：拖进来的图片/文档/文件夹都算，但**过滤
        目录**——建一张挂着目录的卡没有意义（打开附件会变成打开资源管理器），
        且目录递归复制可能吞掉整棵子树。
        """
        mime = event.mimeData()
        if not mime.hasUrls():
            return None
        paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
        files = [p for p in paths if p and Path(p).is_file()]
        return files or None

    def dragMoveEvent(self, event) -> None:
        if self._file_paths_from(event) is not None:
            event.acceptProposedAction()
            return
        if (event.mimeData().hasFormat(MIME_CARD)
                or event.mimeData().hasFormat(MIME_CARDS)):
            self._drag_hovering = True
            self._update_drop_indicator_at(self._column_y_from_event(event))
            if not self._auto_scroll_timer.isActive():
                self._auto_scroll_timer.start()
            event.acceptProposedAction()
        elif event.mimeData().hasFormat(MIME_LIST):
            self._update_list_drop_indicator(event)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._set_drop_highlight(False)
        self._drag_hovering = False
        self._auto_scroll_timer.stop()
        self._drop_mids = None   # 快照随拖拽结束作废（下次进入时重建）
        self._hide_drop_indicator()
        self._hide_list_drop_indicator()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        mime = event.mimeData()
        paths = self._file_paths_from(event)
        if paths is not None:
            self._set_drop_highlight(False)
            self.signal_files_dropped.emit(self._lst.id, paths)
            event.acceptProposedAction()
            return
        if mime.hasFormat(MIME_CARDS):
            # 多选成组拖动：一次搬走整组（顺序按看板顺序，落点为一个整体）
            self._drag_hovering = False
            self._auto_scroll_timer.stop()
            try:
                ids = json.loads(bytes(mime.data(MIME_CARDS)).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                ids = []
            index = self._drop_index_from_y(
                event.position().toPoint().y()
                + self.mapToGlobal(QPoint(0, 0)).y())
            self._drop_mids = None
            self._hide_drop_indicator()
            index = self._raw_index_from_visible(index)
            if ids:
                self.signal_cards_move.emit(ids, self._lst.id, index)
            event.acceptProposedAction()
            return
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
            # 可见序 → raw 序：归档卡在插入点之前时保持落点不错位
            index = self._raw_index_from_visible(index)
            self.signal_card_move.emit(card_id, self._lst.id, index)
            event.acceptProposedAction()
            return
        if mime.hasFormat(MIME_LIST):
            self._set_drop_highlight(False)
            self._hide_list_drop_indicator()
            moved_id = bytes(mime.data(MIME_LIST)).decode("utf-8")
            # 落点 x 相对本列中心：左半=插到本列前，右半=插到本列后
            insert_before = event.position().toPoint().x() < self.width() // 2
            self.signal_list_move.emit(moved_id, self._lst.id, insert_before)
            event.acceptProposedAction()


class AddCardButton(QPushButton):
    """添加卡片/列表按钮（样式由看板级样式表按 objectName 下发）

    variant="toolbar" 时用实心玻璃底（工具栏语境），默认是列尾的虚线占位框。
    """

    def __init__(self, text: str, parent=None, variant: str = ""):
        super().__init__(text, parent)
        self.setObjectName("addBoardBtn")
        self.setCursor(Qt.PointingHandCursor)
        if variant:
            self.setProperty("variant", variant)

    def reapply(self) -> None:
        """配色由看板级样式表统一下发，此处仅触发重绘"""
        self.update()


class _CardDropButton(QPushButton):
    """可接收卡片拖放的工具栏按钮（拖到按钮上 = 对该卡执行按钮的动作）

    "拖卡片到「归档」= 归档、拖到「今日」= 加入今日"把最常用的两个归类
    动作变成一次拖拽；此前必须先拖回列表、再右键、再选菜单项。
    落点高亮用动态属性驱动 QSS（同一按钮在拖拽中会反复进出，重设样式表
    会让按钮与其文字反复 re-polish）。
    """

    signal_cards_dropped = Signal(list)   # card_ids

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setAcceptDrops(True)

    @staticmethod
    def _ids_from(event) -> list:
        mime = event.mimeData()
        if mime.hasFormat(MIME_CARDS):
            try:
                ids = json.loads(bytes(mime.data(MIME_CARDS)).decode("utf-8"))
                return [str(i) for i in ids] if isinstance(ids, list) else []
            except (ValueError, UnicodeDecodeError):
                return []
        if mime.hasFormat(MIME_CARD):
            return [bytes(mime.data(MIME_CARD)).decode("utf-8")]
        return []

    def dragEnterEvent(self, event) -> None:
        if self._ids_from(event):
            _set_prop(self, "dropTarget", True)
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if self._ids_from(event):
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        _set_prop(self, "dropTarget", False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        ids = self._ids_from(event)
        _set_prop(self, "dropTarget", False)
        if not ids:
            return
        event.acceptProposedAction()
        self.signal_cards_dropped.emit(ids)


class _ListsHost(QWidget):
    """看板列表区承载：整列拖到列尾空白 = 移到最末

    此前列拖拽只能落在某个目标列上（左/右半判定前后），想移到最后一列
    必须精确拖到末列右半；承载区接受 MIME_LIST 后，尾部空白即"追加到
    末尾"落点，悬停时由 BoardView 在末列右侧显示插入指示条。
    """

    signal_drop_after_last = Signal(str)   # moved_list_id
    signal_tail_hover = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_LIST):
            self.signal_tail_hover.emit()
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_LIST):
            self.signal_tail_hover.emit()
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        p = self.parent()
        while p is not None and not isinstance(p, BoardView):
            p = p.parent()
        if isinstance(p, BoardView):
            p.hide_list_drop_indicator()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_LIST):
            moved = bytes(event.mimeData().data(MIME_LIST)).decode("utf-8")
            self.signal_drop_after_last.emit(moved)
            event.acceptProposedAction()


class BoardView(QWidget):
    """看板顶层视图：渐变背景 + 工具栏 + 列表横向滚动区"""

    signal_collapse_clicked = Signal()
    signal_theme_selected = Signal(str)         # light / dark
    signal_card_edit = Signal(str, str)         # list_id, card_id
    signal_card_done = Signal(str, str, bool)   # list_id, card_id, done
    signal_card_delete = Signal(str, str)       # list_id, card_id
    signal_card_move = Signal(str, str, int)    # card_id, target_list_id, index
    signal_cards_move = Signal(list, str, int)  # card_ids, 目标列, index（成组拖拽）
    signal_list_move = Signal(str, str, bool)   # moved_list_id, target_list_id, insert_before
    signal_card_add = Signal(str)               # list_id
    signal_add_from_template = Signal(str, str)  # list_id, 模板名
    signal_list_add = Signal()
    signal_bulk_add = Signal()                  # 批量添加（每行一张）
    signal_list_title_changed = Signal(str, str)
    signal_list_delete = Signal(str)
    signal_list_collapsed = Signal(str, bool)   # list_id, collapsed
    signal_list_fund_watch = Signal(str, bool)  # list_id, enabled 资金分配监视
    signal_list_fund_export = Signal(str)       # list_id 导出资金记录 Excel
    signal_list_duplicate = Signal(str)         # list_id（复制列表含卡片）
    signal_close_requested = Signal()           # 窗口 ✕：最小化到托盘（不退出）
    signal_zoom_requested = Signal()
    signal_card_pomo = Signal(str)              # card_id
    signal_card_archive = Signal(str)           # card_id
    signal_card_star = Signal(str)              # card_id（星标 toggle）
    signal_card_workdir_open = Signal(str)      # card_id（打开工作目录）
    signal_card_workdir_set = Signal(str)       # card_id（右键快捷设置目录）
    signal_archive_open = Signal()
    signal_settings_clicked = Signal()          # 打开设置界面
    signal_export = Signal(str)                 # "md" | "csv"
    signal_export_backup = Signal()             # 导出完整备份 .json
    signal_import_backup = Signal()             # 从备份导入（生成新看板）
    signal_import_trello = Signal()             # 导入 Trello 看板 JSON
    signal_import_markdown = Signal()           # 导入 Markdown 任务清单
    signal_today_toggled = Signal(bool)         # 今日聚焦开关变化（菜单栏同步）
    signal_calendar_open = Signal()             # 打开日历视图
    signal_shortcuts_open = Signal()            # 打开快捷键速查
    signal_card_duplicate = Signal(str)         # card_id（复制卡片）
    signal_card_attachment_open = Signal(str)   # card_id（打开首个附件）
    signal_card_priority = Signal(str, int)     # card_id, priority（快捷编辑）
    signal_card_due_set = Signal(str, str)      # card_id, ISO 日期（""=清除）
    signal_card_save_template = Signal(str)     # card_id（另存为模板）
    signal_files_dropped = Signal(str, list)    # list_id, [文件路径]（拖文件建卡）
    # 多看板
    signal_board_switch = Signal(str)           # board_id
    signal_board_create = Signal()
    signal_board_rename = Signal()              # 当前看板
    signal_board_delete = Signal(str)           # board_id
    # 批量操作（多选）
    signal_batch_done = Signal(list, bool)      # card_ids, done
    signal_batch_move = Signal(list, str)       # card_ids, target_list_id
    signal_batch_label = Signal(list, str)      # card_ids, label_key
    signal_batch_delete = Signal(list)          # card_ids
    signal_batch_archive = Signal(list)         # card_ids
    signal_batch_edit = Signal(list)            # card_ids（批量编辑对话框）
    signal_cards_to_today = Signal(list)        # card_ids（拖到「今日」按钮）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lists: list[BoardList] = []
        self._columns: list[ListColumn] = []
        # 当前被悬停的卡片（单光标 → 全局最多一张）。用于零成本自愈：
        # 任一次新的卡片悬停都顺手清掉上一张的残留悬停态（见 _on_card_hover）
        self._hovered_card: "CardWidget | None" = None

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

        # 看板切换器：标题即按钮（点开多看板菜单），名字空串显示默认名
        self._board_name = ""
        self._board_meta: list[tuple[str, str]] = []   # (id, name) 有序
        self._current_board_id = ""
        self._title_btn = _ElidedButton(suffix="  ▾")
        self._title_btn.setObjectName("boardTitleBtn")
        self._title_btn.setCursor(Qt.PointingHandCursor)
        self._title_btn.setToolTip(tr("切换 / 管理看板"))
        self._title_btn.clicked.connect(self._show_board_menu)
        self._toolbar_layout.addWidget(self._title_btn)
        self._set_board_name("")

        self._stats_label = QLabel()
        self._stats_label.setObjectName("boardStats")
        self._toolbar_layout.addWidget(self._stats_label)
        # 注意：不加中间 stretch——弹性全部留给搜索框（右侧控件固定聚集）

        self._today_btn = _CardDropButton(tr("今日 {n}").format(n=0))
        self._today_btn.setObjectName("boardToolBtn")
        self._today_btn.setCheckable(True)
        self._today_btn.setCursor(Qt.PointingHandCursor)
        self._today_btn.setToolTip(
            tr("只显示未完成的：星标 / 已逾期 / 今天截止\n"
               "（拖卡片到这里 = 加入今日）"))
        self._today_btn.toggled.connect(self._apply_filter)
        self._today_btn.toggled.connect(self.signal_today_toggled.emit)
        self._today_btn.signal_cards_dropped.connect(self._on_drop_to_today)
        self._toolbar_layout.addWidget(self._today_btn)

        self._search_edit = QLineEdit()
        self._search_edit.setObjectName("boardSearch")
        self._search_edit.setPlaceholderText(tr("搜索卡片…"))
        self._search_edit.setClearButtonEnabled(True)
        # 弹性宽度：空间富余时舒展、不足时收缩到最小宽，避免工具栏被挤出窗口
        self._search_edit.setMinimumWidth(120)
        self._search_edit.setMaximumWidth(300)
        self._search_edit.setSizePolicy(QSizePolicy.Policy.Expanding,
                                        QSizePolicy.Policy.Fixed)
        self._search_edit.setAccessibleName(tr("搜索卡片"))
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
        # 过滤态下新建卡的临时豁免集：否则"已添加卡片"的提示刚落音，
        # 卡片因不匹配过滤条件凭空消失
        self._pinned_ids: set[str] = set()
        self._today_count = 0   # 今日角标数（reapply_texts 重建文案用）
        self._label_chip = QPushButton()
        self._label_chip.setObjectName("boardToolBtn")
        self._label_chip.setCursor(Qt.PointingHandCursor)
        self._label_chip.setToolTip(tr("点击清除标签过滤"))
        self._label_chip.clicked.connect(self._clear_label_filter)
        self._label_chip.hide()
        self._toolbar_layout.addWidget(self._label_chip)
        self._toolbar_layout.addWidget(self._search_edit)
        # 搜索框参与剩余空间分配（与 stats 之后的 stretch 平分）
        self._toolbar_layout.setStretchFactor(self._search_edit, 1)

        self._add_list_btn = AddCardButton(tr("+ 添加列表"),
                                           variant="toolbar")
        self._add_list_btn.setFixedWidth(96)
        self._add_list_btn.clicked.connect(self.signal_list_add.emit)
        self._toolbar_layout.addWidget(self._add_list_btn)

        # 批量添加（每行一张，支持速记）：此前仅 macOS 菜单栏可达
        self._bulk_add_btn = AddCardButton(tr("+ 批量"), variant="toolbar")
        self._bulk_add_btn.setFixedWidth(72)
        self._bulk_add_btn.setCursor(Qt.PointingHandCursor)
        self._bulk_add_btn.setToolTip(
            tr("批量添加卡片：每行一张，支持速记语法"))
        self._bulk_add_btn.clicked.connect(self.signal_bulk_add.emit)
        self._toolbar_layout.addWidget(self._bulk_add_btn)

        self._archive_btn = _CardDropButton(tr("归档"))
        self._archive_btn.setObjectName("boardToolBtn")
        self._archive_btn.setCursor(Qt.PointingHandCursor)
        self._archive_btn.setToolTip(
            tr("查看已归档卡片并恢复\n（拖卡片到这里 = 直接归档）"))
        self._archive_btn.clicked.connect(self.signal_archive_open.emit)
        self._archive_btn.signal_cards_dropped.connect(self._on_drop_archive)
        self._toolbar_layout.addWidget(self._archive_btn)

        self._calendar_btn = _CalendarIconButton()
        self._calendar_btn.setObjectName("boardThemeBtn")   # 同款圆形玻璃底
        self._calendar_btn.setFixedSize(34, 34)
        self._calendar_btn.setCursor(Qt.PointingHandCursor)
        self._calendar_btn.setToolTip(tr("按截止日期在月历中查看与拖动卡片"))
        # 自绘图标按钮没有文字，读屏只会报 "button"——accessibleName 是
        # 它唯一能被念出来的名字
        self._calendar_btn.setAccessibleName(tr("日历视图"))
        self._calendar_btn.clicked.connect(self.signal_calendar_open.emit)
        self._toolbar_layout.addWidget(self._calendar_btn)

        self._export_btn = QPushButton(tr("导出"))
        self._export_btn.setObjectName("boardToolBtn")
        self._export_btn.setCursor(Qt.PointingHandCursor)
        self._export_btn.setToolTip(tr("导出为 Markdown / CSV"))
        self._export_btn.clicked.connect(self._show_export_menu)
        self._toolbar_layout.addWidget(self._export_btn)

        self._settings_btn = _GearButton()
        self._settings_btn.setObjectName("boardThemeBtn")   # 同款圆形玻璃底
        # 与日历/主题键同尺寸：不设的话 `border-radius: 17px` 画出来是圆角
        # 矩形而不是圆，且比左右邻居宽——工具栏上最扎眼的一处不一致
        self._settings_btn.setFixedSize(34, 34)
        self._settings_btn.setCursor(Qt.PointingHandCursor)
        self._settings_btn.setToolTip(tr("设置"))
        self._settings_btn.setAccessibleName(tr("设置"))
        self._settings_btn.clicked.connect(self.signal_settings_clicked.emit)
        self._toolbar_layout.addWidget(self._settings_btn)

        self._theme_btn = _ThemeToggleButton()
        self._theme_btn.setObjectName("boardThemeBtn")
        self._theme_btn.setCursor(Qt.PointingHandCursor)
        self._theme_btn.setFixedSize(34, 34)
        self._theme_btn.setToolTip(tr("切换浅色 / 深色主题"))
        self._theme_btn.setAccessibleName(tr("切换浅色 / 深色主题"))
        self._theme_btn.clicked.connect(self._on_theme_clicked)
        self._toolbar_layout.addWidget(self._theme_btn)

        self._collapse_btn = QPushButton("－")
        self._collapse_btn.setObjectName("boardCollapseBtn")
        self._collapse_btn.setCursor(Qt.PointingHandCursor)
        self._collapse_btn.setFixedSize(34, 34)
        self._collapse_btn.setToolTip(tr("折叠为桌宠"))
        self._collapse_btn.setAccessibleName(tr("折叠为桌宠"))
        self._collapse_btn.clicked.connect(self.signal_collapse_clicked.emit)
        self._toolbar_layout.addWidget(self._collapse_btn)

        # macOS 红绿灯（对齐 macOS 窗口范式）：红=最小化到托盘 黄=折叠桌宠 绿=最大化/还原
        if AppConfig.IS_MACOS:
            from app.views.traffic_lights import TrafficLights
            self._traffic_lights = TrafficLights()
            self._traffic_lights.signal_close.connect(
                self.signal_close_requested.emit)
            self._traffic_lights.signal_minimize.connect(
                self.signal_collapse_clicked.emit)
            self._traffic_lights.signal_zoom.connect(
                self.signal_zoom_requested.emit)
            self._toolbar_layout.insertWidget(0, self._traffic_lights)
            self._collapse_btn.hide()   # 黄灯已承担折叠，避免重复控件

        # Windows 窗口控制键（贴右缘）：─ 折叠桌宠  □ 最大化/还原  ✕ 最小化到托盘
        self._window_controls = None
        if AppConfig.IS_WINDOWS:
            from app.views.window_controls import WindowControls
            self._window_controls = WindowControls()
            self._window_controls.signal_minimize.connect(
                self.signal_collapse_clicked.emit)
            self._window_controls.signal_zoom.connect(
                self.signal_zoom_requested.emit)
            self._window_controls.signal_close.connect(
                self.signal_close_requested.emit)
            self._toolbar_layout.addWidget(self._window_controls)
            self._collapse_btn.hide()   # 最小化键已承担折叠，避免重复控件

        # 窄窗收纳：放不下的次要按钮折进「»」菜单（见 _apply_toolbar_overflow）
        self._overflow_btn = QPushButton("»")
        self._overflow_btn.setObjectName("boardToolBtn")
        self._overflow_btn.setCursor(Qt.PointingHandCursor)
        self._overflow_btn.setToolTip(tr("更多操作"))
        self._overflow_btn.setAccessibleName(tr("更多操作"))
        self._overflow_menu = QMenu(self)
        self._overflow_btn.setMenu(self._overflow_menu)
        self._overflow_btn.hide()
        self._toolbar_layout.addWidget(self._overflow_btn)

        # 收纳优先级（低 → 高）：空间不足时从前往后收进「»」菜单。
        # 顺序按"冗余度"排：统计数字在列头也有、日历在 macOS 菜单栏也有，
        # 而「+ 添加列表」是空看板引导指向的唯一入口，放最后。
        # 「今日」与看板标题排在最后两级：标题的 88px 是剩余项里最宽的一个，
        # 而它同时有 tooltip 与「»」菜单两条退路——极窄窗口下唯一还能让的。
        self._overflow_order = ("stats", "calendar", "bulk_add", "export",
                                "archive", "add_list", "today", "title")
        # 搜索框的弹性范围：原始最小宽 → 硬下限。窄到下限仍放不下时，
        # 「今日」开关已被收进「»」菜单，工具栏不可能再重叠。
        self._search_min_w = self._search_edit.minimumWidth()
        self._search_min_floor = 72
        self._toolbar_items: list[tuple[str, QWidget]] = [
            ("title", self._title_btn),
            ("stats", self._stats_label),
            ("today", self._today_btn),
            ("search", self._search_edit),
            ("add_list", self._add_list_btn),
            ("bulk_add", self._bulk_add_btn),
            ("archive", self._archive_btn),
            ("calendar", self._calendar_btn),
            ("export", self._export_btn),
            ("settings", self._settings_btn),
            ("theme", self._theme_btn),
            ("overflow", self._overflow_btn),
        ]
        self._overflow_hidden: list[str] = []

        root.addWidget(self._toolbar)

        # ── 列表区（横向滚动） ────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setObjectName("boardScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.viewport().setAutoFillBackground(False)
        self._lists_host = _ListsHost()
        self._lists_host.setObjectName("listsHost")
        # 整列拖到列尾空白 = 追加到末尾（含悬停指示条反馈）
        self._lists_host.signal_drop_after_last.connect(self._on_drop_after_last)
        self._lists_host.signal_tail_hover.connect(
            lambda: self.show_list_drop_indicator(self._columns[-1], False)
            if self._columns else None)
        self._lists_layout = QHBoxLayout(self._lists_host)
        self._lists_layout.setContentsMargins(16, 4, 16, 12)
        self._lists_layout.setSpacing(12)
        self._lists_layout.addStretch(1)
        self._scroll.setWidget(self._lists_host)
        root.addWidget(self._scroll, 1)

        # 列间插入指示条（整列拖拽用，挂在承载区上随横向滚动移动）
        self._list_drop_indicator = QFrame(self._lists_host)
        self._list_drop_indicator.setObjectName("dropIndicator")
        self._list_drop_indicator.hide()

        # 空看板引导：无任何列表时覆盖在列表区上方居中，可穿透鼠标
        self._empty_hint = QLabel(
            tr("看板还是空的\n点击右上角「+ 添加列表」创建第一列"), self)
        self._empty_hint.setObjectName("emptyBoardHint")
        self._empty_hint.setAlignment(Qt.AlignCenter)
        self._empty_hint.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._empty_hint.hide()

        # 看板内轻提示（删除/撤销等操作反馈，见 toast.py）
        self._toast = Toast(self)

        # ── 多选批量操作栏（有选中卡片时浮在看板底部居中） ──
        self._selected_ids: set[str] = set()
        self._sel_anchor: str | None = None
        self._selection_bar = QFrame(self)
        self._selection_bar.setObjectName("selectionBar")
        sel_lay = QHBoxLayout(self._selection_bar)
        sel_lay.setContentsMargins(12, 6, 12, 6)
        sel_lay.setSpacing(8)
        self._sel_count_label = QLabel()
        self._sel_count_label.setObjectName("selCount")
        sel_lay.addWidget(self._sel_count_label)
        self._sel_done_btn = QPushButton(tr("✓ 切换完成"))
        self._sel_done_btn.setObjectName("boardToolBtn")
        self._sel_done_btn.setCursor(Qt.PointingHandCursor)
        self._sel_done_btn.clicked.connect(self._on_batch_done)
        sel_lay.addWidget(self._sel_done_btn)
        self._sel_edit_btn = QPushButton(tr("✎ 编辑"))
        self._sel_edit_btn.setObjectName("boardToolBtn")
        self._sel_edit_btn.setCursor(Qt.PointingHandCursor)
        self._sel_edit_btn.setToolTip(tr("批量修改截止日期 / 优先级 / 标题"))
        self._sel_edit_btn.clicked.connect(self._on_batch_edit)
        sel_lay.addWidget(self._sel_edit_btn)
        self._sel_label_btn = QPushButton(tr("标签"))
        self._sel_label_btn.setObjectName("boardToolBtn")
        self._sel_label_btn.setCursor(Qt.PointingHandCursor)
        self._sel_label_btn.clicked.connect(self._show_batch_label_menu)
        sel_lay.addWidget(self._sel_label_btn)
        self._sel_move_btn = QPushButton(tr("→ 移动"))
        self._sel_move_btn.setObjectName("boardToolBtn")
        self._sel_move_btn.setCursor(Qt.PointingHandCursor)
        self._sel_move_btn.clicked.connect(self._show_batch_move_menu)
        sel_lay.addWidget(self._sel_move_btn)
        self._sel_archive_btn = QPushButton(tr("归档"))
        self._sel_archive_btn.setObjectName("boardToolBtn")
        self._sel_archive_btn.setCursor(Qt.PointingHandCursor)
        self._sel_archive_btn.clicked.connect(
            lambda: self._emit_batch(self.signal_batch_archive))
        sel_lay.addWidget(self._sel_archive_btn)
        self._sel_delete_btn = QPushButton(tr("删除"))
        self._sel_delete_btn.setObjectName("boardToolBtn")
        self._sel_delete_btn.setCursor(Qt.PointingHandCursor)
        self._sel_delete_btn.clicked.connect(
            lambda: self._emit_batch(self.signal_batch_delete))
        sel_lay.addWidget(self._sel_delete_btn)
        self._sel_clear_btn = QPushButton("✕")
        self._sel_clear_btn.setObjectName("boardToolBtn")
        self._sel_clear_btn.setCursor(Qt.PointingHandCursor)
        self._sel_clear_btn.setToolTip(tr("取消多选（Esc）"))
        self._sel_clear_btn.clicked.connect(self.clear_selection)
        sel_lay.addWidget(self._sel_clear_btn)
        self._selection_bar.hide()

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
                and isinstance(obj, QWidget)):
            if not shiboken6.isValid(_ACTIVE_RENAME):
                # 编辑器随宿主列被 deleteLater 销毁（撤销删列等未经提交
                # 路径）：清悬空引用并卸过滤器——对死 wrapper 调
                # isAncestorOf 会抛 RuntimeError，且每次点击都复现
                finish_active_rename()
                self._attach_rename_filter()
            elif (obj is not _ACTIVE_RENAME
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

    # ── 悬停态跟踪（零成本自愈，见 CardWidget._clear_hover）──

    def _on_card_hover(self, cw: "CardWidget") -> None:
        """记录当前悬停卡，并顺手清掉上一张的悬停态

        同一时刻只可能有一张卡被悬停（单光标），所以"上一次悬停的不是
        本卡"就等于它已经过期——无论它的 Leave 有没有被投递。任一次真实
        悬停都会收拾掉残留：漏投递不再是"永久残留"，最多活到鼠标落到
        下一张卡（或下一次列切换）。
        """
        prev = self._hovered_card
        if prev is not None and prev is not cw:
            if shiboken6.isValid(prev):
                prev._clear_hover()
            else:
                self._hovered_card = None
        self._hovered_card = cw

    def _forget_card_hover(self, cw: "CardWidget") -> None:
        """卡片自行复位悬停态时同步解除跟踪"""
        if self._hovered_card is cw:
            self._hovered_card = None

    def leaveEvent(self, event) -> None:
        """光标离开整块看板：连列级 Leave 都可能没派发，直接复位跟踪卡"""
        cw = self._hovered_card
        if cw is not None:
            if shiboken6.isValid(cw):
                cw._clear_hover()
            else:
                self._hovered_card = None
        super().leaveEvent(event)

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

    def reapply_texts(self) -> None:
        """语言切换：刷新静态文案；卡片指纹清空，待 refresh 重建徽章/tooltip"""
        self._set_board_name(self._board_name)
        self._search_edit.setPlaceholderText(tr("搜索卡片…"))
        self._search_edit.setAccessibleName(tr("搜索卡片"))
        self._today_btn.setToolTip(
            tr("只显示未完成的：星标 / 已逾期 / 今天截止"))
        self._set_today_count(self._today_count)
        if self._label_filter:
            self._update_label_chip()
        self._add_list_btn.setText(tr("+ 添加列表"))
        self._bulk_add_btn.setText(tr("+ 批量"))
        self._bulk_add_btn.setToolTip(
            tr("批量添加卡片：每行一张，支持速记语法"))
        self._archive_btn.setText(tr("归档"))
        self._archive_btn.setToolTip(tr("查看已归档卡片并恢复"))
        self._calendar_btn.setToolTip(tr("按截止日期在月历中查看与拖动卡片"))
        self._overflow_btn.setToolTip(tr("更多操作"))
        self._sel_done_btn.setText(tr("✓ 切换完成"))
        self._sel_edit_btn.setText(tr("✎ 编辑"))
        self._sel_edit_btn.setToolTip(tr("批量修改截止日期 / 优先级 / 标题"))
        self._sel_label_btn.setText(tr("标签"))
        self._sel_move_btn.setText(tr("→ 移动"))
        self._sel_archive_btn.setText(tr("归档"))
        self._sel_delete_btn.setText(tr("删除"))
        self._sel_clear_btn.setToolTip(tr("取消多选（Esc）"))
        self._update_selection_bar()
        self._export_btn.setText(tr("导出"))
        self._export_btn.setToolTip(tr("导出为 Markdown / CSV"))
        self._theme_btn.setToolTip(tr("切换浅色 / 深色主题"))
        self._collapse_btn.setToolTip(tr("折叠为桌宠"))
        self._settings_btn.setToolTip(tr("设置"))
        lights = getattr(self, "_traffic_lights", None)
        if lights is not None:
            lights.reapply_texts()
        self._empty_hint.setText(
            tr("看板还是空的\n点击右上角「+ 添加列表」创建第一列"))
        self._sync_overflow_menu()      # 「»」菜单项文案随语言刷新
        for col in self._columns:
            col.retexts()
            for cw in col._card_widgets:
                cw._fingerprint = ()   # 强制后续 refresh 时 rebuild
        self.update_stats(self._lists)

    def _on_theme_clicked(self) -> None:
        self.signal_theme_selected.emit(
            "dark" if AppTheme.mode() == "light" else "light")

    # ── 滚轮 / 键盘导航 ───────────────────────────────────

    def wheelEvent(self, event) -> None:
        """垂直滚轮驱动看板横移

        看板纵向没有可滚内容（纵向滚动条恒关），鼠标滚轮上下滚此前
        完全无效，只能拖底部滚动条；转为横向滚动（Trello 同款行为）。
        列内卡片区因列自身滚动区消费滚轮而不受影响：列内滚轮 = 列内
        纵向滚动，列间空白/工具栏滚轮 = 看板横移。
        """
        ad = event.angleDelta()
        if ad.y() == 0:
            super().wheelEvent(event)   # 纯水平分量：触摸板横扫走原生路径
            return
        sb = self._scroll.horizontalScrollBar()
        sb.setValue(sb.value()
                    - int(round(ad.y() / 120.0 * AppConfig.BOARD_WHEEL_STEP)))
        event.accept()

    def keyPressEvent(self, event) -> None:
        """无焦点卡片时按方向键：聚焦首列第一张卡，进入键盘导航"""
        if event.key() in (Qt.Key_Up, Qt.Key_Down,
                           Qt.Key_Left, Qt.Key_Right):
            for col in self._columns:
                if not col.is_collapsed() and col._card_widgets:
                    w = col._card_widgets[0]
                    w.setFocus()
                    self._scroll.ensureWidgetVisible(w)
                    event.accept()
                    return
        super().keyPressEvent(event)

    def move_focus_horizontally(self, col: "ListColumn", card: "CardWidget",
                                to_next: bool) -> bool:
        """左右方向键跳到相邻（跳过折叠列）列的同位置卡片；返回是否移动"""
        try:
            i = self._columns.index(col)
        except ValueError:
            return False
        # 同位置 = card 在源列内的行号（card 是源列控件，到目标列找
        # index 必然 ValueError，此前恒跳目标列第一张）
        try:
            k = col._card_widgets.index(card)
        except ValueError:
            k = 0
        j = i + (1 if to_next else -1)
        step = 1 if to_next else -1
        while 0 <= j < len(self._columns):
            target = self._columns[j]
            if not target.is_collapsed() and target._card_widgets:
                w = target._card_widgets[min(k, len(target._card_widgets) - 1)]
                w.setFocus()
                self._scroll.ensureWidgetVisible(w)
                return True
            j += step
        return False

    # ── 列拖拽指示条 / 新列可见性 ─────────────────────────

    def show_list_drop_indicator(self, col: "ListColumn",
                                 insert_before: bool) -> None:
        """整列拖拽：把竖向插入指示条摆在目标列前/后的列间隙中点"""
        ind = self._list_drop_indicator
        ind.setFixedSize(3, max(col.height(), 24))
        mid = (col.x() - 6) if insert_before else (col.x() + col.width() + 6)
        ind.move(mid - 1, col.y())
        ind.show()
        ind.raise_()

    def hide_list_drop_indicator(self) -> None:
        self._list_drop_indicator.hide()

    def reveal_list(self, list_id: str) -> None:
        """添加列表后把新列滚入视口（refresh 已同步跑完，延后一拍等布局）"""
        for col in self._columns:
            if col.list_id() == list_id:
                QTimer.singleShot(
                    0, self,
                    lambda c=col: self._scroll.ensureWidgetVisible(c, 12, 0))
                return

    def _on_drop_after_last(self, moved_id: str) -> None:
        """列尾空白落点：把被拖列追加到最末（复用列间 move 语义）"""
        rest = [c for c in self._columns if c.list_id() != moved_id]
        self.hide_list_drop_indicator()
        if rest:
            self.signal_list_move.emit(moved_id, rest[-1].list_id(), False)

    # ── paintEvent：渐变背景 ──────────────────────────────

    # 渐变对象按 (宽, 高, 主题模式) 缓存：展开/缩放动画逐帧触发本方法，
    # 每帧重建 QLinearGradient + 3×QColor 纯浪费；键空间随用户任意缩放
    # 有界性不可保证，超上限整体清空兜底
    _bg_gradient_cache: dict[tuple[int, int, str], QLinearGradient] = {}

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        # 纯 fillRect 无需抗锯齿（此前挂着无用的 Antialiasing 开销）
        key = (self.width(), self.height(), AppTheme.mode())
        gradient = self._bg_gradient_cache.get(key)
        if gradient is None:
            c = AppTheme.colors()
            gradient = QLinearGradient(0, 0, key[0], key[1])
            gradient.setColorAt(0.0, QColor(c["board_bg_start"]))
            gradient.setColorAt(0.55, QColor(c["board_bg_mid"]))
            gradient.setColorAt(1.0, QColor(c["board_bg_end"]))
            if len(self._bg_gradient_cache) > 64:
                self._bg_gradient_cache.clear()
            self._bg_gradient_cache[key] = gradient
        painter.fillRect(self.rect(), gradient)
        painter.end()

    # ── 数据刷新 ──────────────────────────────────────────

    def refresh(self, lists: list[BoardList],
                stats: dict | None = None,
                build_limit: int = 0) -> None:
        """按看板数据增量同步列（按 list.id 复用列与卡片控件）

        stats 传入 board.today_stats() 结果时统计行直接取用，免重扫。

        build_limit>0 为分帧补齐路径（展开首帧后的逐列补建）：始终传入
        全量 lists 以保持过滤/统计口径，但只处理前 build_limit 列、不做
        列回收（dispose）与布局重插——期间的数据变更走 build_limit=0
        的全量 refresh 一次建齐，随后的分帧经幂等 diff 自然收敛。
        """
        # 记录看板横向滚动位置，增删列后恢复
        sb = self._scroll.horizontalScrollBar()
        scroll_pos = sb.value()

        self._lists = lists

        visibles = {lst.id: self._visible_cards_for(lst) for lst in lists}

        # _lower_cache 只增不减（注释此前自称"随卡量有界"并不成立）：
        # 删除/撤销重建后 id 换代，死条目永驻。缓存膨胀到当前卡量的
        # 2 倍以上时按现存卡集驱逐一次，常态下 O(1) 短路
        if len(self._lower_cache) > 2 * sum(len(v or []) for v in visibles.values()):
            alive = {c.id for lst in lists for c in lst.cards}
            for dead in self._lower_cache.keys() - alive:
                del self._lower_cache[dead]

        by_id = {col.list_id(): col for col in self._columns}
        prev_order = [col.list_id() for col in self._columns]
        ordered_cols: list[ListColumn] = []
        kept: set[str] = set()
        collapsed_ids = AppConfig.get_collapsed_lists()   # 全列共享一次读取
        targets = lists[:build_limit] if build_limit else lists
        for lst in targets:
            col = by_id.get(lst.id)
            if col is None:
                col = self._make_column(lst, collapsed_ids)
                # 无过滤时短路（构造已完成全量显示），有过滤则增量同步
                col.set_visible_cards(visibles[lst.id])
                if build_limit:
                    # _make_column 只建控件并记入 _columns，不负责挂进布局；
                    # 分帧路径又跳过了下方全量重插，收口帧也补不上（_columns
                    # 届时已含该列 → 顺序未变 → 重插分支不触发）。列控件没有
                    # parent 就永不显示，故此处就地按序插入
                    self._lists_layout.insertWidget(
                        len(ordered_cols), col, 0, Qt.AlignTop)
            else:
                kept.add(lst.id)
                col.set_list(lst, visibles[lst.id])
            ordered_cols.append(col)
        # 列顺序未变（单卡变更/编辑保存等）→ 布局无需重插；仅增删列或
        # 拖拽重排造成顺序变化时才 remove+insert 保序（与卡片同一策略）。
        # 分帧路径跳过本次重插：新列已在建列处就地入布局，重排交给收口帧
        if (not build_limit
                and prev_order != [col.list_id() for col in ordered_cols]):
            for i, col in enumerate(ordered_cols):
                self._lists_layout.removeWidget(col)
                # AlignTop 让布局项取 sizeHint（内容高度）而非拉伸填满整列区
                self._lists_layout.insertWidget(i, col, 0, Qt.AlignTop)
        if not build_limit:
            for list_id, col in by_id.items():
                if list_id not in kept:
                    if (_ACTIVE_RENAME is not None
                            and shiboken6.isValid(_ACTIVE_RENAME)
                            and col.isAncestorOf(_ACTIVE_RENAME)):
                        # 编辑器宿主列即将销毁：先收口（丢弃修改——列都
                        # 没了，改名无意义），避免留下悬空编辑器
                        finish_active_rename(cancel=True)
                    self._columns.remove(col)
                    col.setParent(None)
                    col.deleteLater()
        # 列顺序与 lists 同步：列拖拽/撤销会改变 lists 顺序，布局已随上方
        # 循环重插，_columns 列表本身也必须跟随，否则 _apply_filter 的
        # zip(_lists, _columns) 在过滤模式下会与列配对错位。
        # 分帧路径只追加新建列：_columns 必须保持"lists 的前缀子序列"
        # （数据变更的全量 refresh 可能已建齐全部列，不能被前 k 列裁掉；
        # _apply_filter 的 zip 依赖此配对），顺序重排由收口帧全量接管
        if build_limit:
            known = {id(col) for col in self._columns}
            self._columns.extend(col for col in ordered_cols
                                 if id(col) not in known)
        else:
            self._columns = ordered_cols

        self.update_stats(lists, visibles=visibles, stats=stats)
        self._set_today_count(self._today_count_from(visibles))
        self._update_empty_hint(lists)
        # 列/卡控件按 id 复用，选中态属性随控件保留；重建出的新卡默认
        # 未选中，这里统一重刷一次（集合没变时设置是幂等短路）
        # 先收窄集合到现存卡：单卡删除路径不清集合，否则"已选 N 张"虚报
        if self._selected_ids:
            self._selected_ids &= {c.id for lst in lists for c in lst.cards}
            self._update_selection_bar()
        self._apply_selection_props()
        sb.setValue(scroll_pos)

    # ── 看板切换器 ────────────────────────────────────────

    def set_boards(self, metas: list[tuple[str, str]],
                   current_id: str) -> None:
        """注入看板元信息列表（id, name 有序）与当前看板 id"""
        self._board_meta = list(metas)
        self._current_board_id = current_id
        name = next((n for bid, n in metas if bid == current_id), "")
        self._set_board_name(name)

    def _set_board_name(self, name: str) -> None:
        self._board_name = name
        self._title_btn.set_full_text(name or tr("我的看板"))
        self._title_btn.setToolTip(
            tr("切换 / 管理看板") + f"（{name or tr('我的看板')}）")

    def _show_board_menu(self) -> None:
        menu = QMenu(self)
        for bid, name in self._board_meta:
            act = menu.addAction(name or tr("我的看板"))
            act.setCheckable(True)
            act.setChecked(bid == self._current_board_id)
            act.triggered.connect(
                lambda _=False, i=bid: self.signal_board_switch.emit(i))
        menu.addSeparator()
        act_new = menu.addAction(tr("+ 新建看板"))
        act_new.triggered.connect(self.signal_board_create.emit)
        act_rename = menu.addAction(tr("✏ 重命名看板"))
        act_rename.triggered.connect(self.signal_board_rename.emit)
        act_delete = menu.addAction(tr("🗑 删除看板"))
        act_delete.setEnabled(len(self._board_meta) > 1)
        act_delete.triggered.connect(
            lambda: self.signal_board_delete.emit(self._current_board_id))
        menu.exec(self._title_btn.mapToGlobal(
            QPoint(0, self._title_btn.height() + 2)))
        menu.deleteLater()

    # ── 多选批量操作 ──────────────────────────────────────

    def _iter_card_widgets(self):
        for col in self._columns:
            for cw in col._card_widgets:
                yield cw

    def _find_card_widget(self, card_id: str) -> "CardWidget | None":
        for cw in self._iter_card_widgets():
            if cw.card().id == card_id:
                return cw
        return None

    def _apply_selection_props(self) -> None:
        selected = self._selected_ids
        for cw in self._iter_card_widgets():
            cw.set_selected(cw.card().id in selected)

    def _update_selection_bar(self) -> None:
        bar = self._selection_bar
        if self._selected_ids:
            self._sel_count_label.setText(
                tr("已选 {n} 张").format(n=len(self._selected_ids)))
            bar.adjustSize()
            bar.move((self.width() - bar.width()) // 2,
                     self.height() - bar.height() - 14)
            was_visible = bar.isVisible()
            bar.show()
            bar.raise_()
            # 出现与卡片/列同一套淡入（此前是裸 show，浮层"啪"地跳出）
            if not was_visible and motion.enabled():
                motion.fade_in(bar, AppConfig.POPOVER_ANIM_MS)
        elif bar.isVisible():
            if motion.enabled():
                motion.fade_out(bar, AppConfig.CARD_EXIT_ANIM_MS,
                                on_finished=bar.hide)
            else:
                bar.hide()

    def _on_card_ctrl_clicked(self, card_id: str) -> None:
        if card_id in self._selected_ids:
            self._selected_ids.discard(card_id)
        else:
            self._selected_ids.add(card_id)
        self._sel_anchor = card_id
        self._apply_selection_props()
        self._update_selection_bar()

    def _on_card_shift_clicked(self, card_id: str) -> None:
        anchor = self._sel_anchor
        if anchor is None or anchor not in self._selected_ids:
            self._on_card_ctrl_clicked(card_id)
            return
        # 范围限定在同一列内（跨列 Shift 退化为单选该卡）
        for col in self._columns:
            ids = [cw.card().id for cw in col._card_widgets]
            if card_id in ids and anchor in ids:
                lo, hi = sorted((ids.index(anchor), ids.index(card_id)))
                self._selected_ids.update(ids[lo:hi + 1])
                break
        else:
            self._selected_ids.add(card_id)
        self._apply_selection_props()
        self._update_selection_bar()

    def clear_selection(self) -> None:
        if not self._selected_ids:
            return
        self._selected_ids.clear()
        self._sel_anchor = None
        self._apply_selection_props()
        self._update_selection_bar()

    def clear_view_filters(self) -> None:
        """切换看板时清空"对当前看板的临时视角"（搜索词 / 标签过滤）：

        过滤是临时视角，换看板保留旧过滤会让人以为新看板真的没内容
        （标签 chip 可能指向新看板不存在的标签，统计行恒显"匹配 0 张"）。
        今日模式是跨看板概念，保留。
        """
        self._search_edit.clear()          # 触发 textChanged → 立即恢复
        self._pinned_ids.clear()
        if self._label_filter is not None:
            self._label_filter = None
            self._update_label_chip()
            self._apply_filter()

    def clear_selection_if_active(self) -> bool:
        """有选中卡片时清空并吞掉本次 Esc（Esc 折叠链第一环）"""
        if not self._selected_ids:
            return False
        self.clear_selection()
        return True

    def selection_ids(self) -> list[str]:
        return list(self._selected_ids)

    def list_choices(self) -> list[tuple[str, str]]:
        """当前看板的 (列表 id, 标题) 序列（卡片"移动到列表"菜单用）"""
        return [(lst.id, lst.title) for lst in self._lists]

    def template_names(self) -> list[str]:
        """已保存的卡片模板名（列尾「+ 添加卡片」菜单用）"""
        from app.models import templates as templates_mod
        return [t["name"] for t in templates_mod.load_templates()]

    def _emit_batch(self, signal) -> None:
        ids = self.selection_ids()
        if not ids:
            return
        signal.emit(ids)
        self.clear_selection()

    def _on_batch_edit(self) -> None:
        """批量编辑：把选中集交给控制器开对话框（改完由控制器统一落盘）"""
        ids = self.selection_ids()
        if not ids:
            return
        self.signal_batch_edit.emit(ids)
        self.clear_selection()

    def _on_drop_archive(self, card_ids: list) -> None:
        """拖卡片到「归档」按钮：归档（多选在拖拽中被成组带走，一并清选中）"""
        self.clear_selection()
        self.signal_batch_archive.emit(card_ids)

    def _on_drop_to_today(self, card_ids: list) -> None:
        """拖卡片到「今日」按钮：加入今日聚焦"""
        self.clear_selection()
        self.signal_cards_to_today.emit(card_ids)

    def _on_batch_done(self) -> None:
        ids = self.selection_ids()
        board_lists = self._lists
        by_id = {c.id: c for lst in board_lists for c in lst.cards}
        # 存在任一未完成 → 全部置完成；否则全部取消完成（切换语义）
        target = not all(by_id.get(i).done
                         for i in ids if by_id.get(i) is not None)
        if not ids:
            return
        self.signal_batch_done.emit(ids, target)
        self.clear_selection()

    def _show_batch_label_menu(self) -> None:
        ids = self.selection_ids()
        if not ids:
            return
        menu = QMenu(self)
        for key in AppConfig.LABEL_COLORS:
            act = menu.addAction(label_display(key))
            act.triggered.connect(
                lambda _=False, k=key: (self.signal_batch_label.emit(ids, k),
                                        self.clear_selection()))
        menu.exec(self._sel_label_btn.mapToGlobal(
            QPoint(0, self._sel_label_btn.height() + 2)))
        menu.deleteLater()

    def _show_batch_move_menu(self) -> None:
        ids = self.selection_ids()
        if not ids:
            return
        menu = QMenu(self)
        for lst in self._lists:
            act = menu.addAction(lst.title)
            act.triggered.connect(
                lambda _=False, lid=lst.id:
                (self.signal_batch_move.emit(ids, lid),
                 self.clear_selection()))
        menu.exec(self._sel_move_btn.mapToGlobal(
            QPoint(0, self._sel_move_btn.height() + 2)))
        menu.deleteLater()

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
            if c.archived:
                continue   # 归档卡不渲染，也不进匹配数
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
            cards = [c for c in lst.cards
                     if not c.archived and c.in_today_focus(today)]
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
            return self._with_pinned(lst, cards)
        if not q and not tag:
            return None
        cards = (self._filter_cards(lst, q) if q
                 else [c for c in lst.cards if not c.archived])
        if tag:
            cards = [c for c in cards if tag in c.labels]
        return self._with_pinned(lst, cards)

    def _with_pinned(self, lst: BoardList, cards: list[Card]) -> list[Card]:
        """把过滤态新建的豁免卡并入可见集（不匹配过滤条件也保持可见）"""
        if not self._pinned_ids:
            return cards
        in_set = {c.id for c in cards}
        extra = [c for c in lst.cards
                 if c.id in self._pinned_ids and c.id not in in_set
                 and not c.archived]
        return cards + extra

    def pin_card_visible(self, card_id: str) -> None:
        """过滤生效期间新建的卡片临时豁免过滤（清过滤时一并失效）"""
        self._pinned_ids.add(card_id)

    def _on_label_clicked(self, key: str) -> None:
        """点卡片左缘色条按标签过滤全板；再点同色清除"""
        self._label_filter = None if self._label_filter == key else key
        self._update_label_chip()
        self._apply_filter()
        # 明示反馈：命中区只有几像素宽，不说明的话用户不知道刚发生了什么
        if self._label_filter is None:
            self.show_toast(tr("已清除标签筛选"))
        else:
            self.show_toast(tr("已按标签「{name}」筛选").format(
                name=label_display(key)))

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

    def _on_search_edited(self, text: str) -> None:
        """输入防抖过滤；清空（含 ✕ 按钮）立即恢复，不等防抖"""
        if not text:
            self._search_timer.stop()
            self._apply_filter()
            return
        self._search_timer.start()

    def _apply_filter(self, *_args) -> None:
        """今日开关 / 搜索防抖到点：单遍扫描刷新各列可见卡片与角标统计"""
        if (not self._search_query() and not self._label_filter
                and not self._today_btn.isChecked()):
            self._pinned_ids.clear()   # 无任何过滤：豁免集随之失效
        visibles: dict = {}
        for lst, col in zip(self._lists, self._columns):
            visible = self._visible_cards_for(lst)
            visibles[lst.id] = visible
            col.set_visible_cards(visible)
        # 角标恒为"今日聚焦总数"。此前过滤态下会改成匹配数，于是输入 "OKR"
        # 让 chip 变成「今日 2」——标签说今日、数值是匹配数，用户读到的
        # 是一句假话。匹配数另有出口（统计行），不该挤进今日角标。
        self._set_today_count(self._today_focus_total())
        # 统计行文案统一由 update_stats 决定（搜索 → 匹配数；标签过滤 →
        # "标签 X · N 张"；常态 → 总数/完成数）。把刚算好的 visibles 传进去，
        # 避免同一输入把全板文本再扫一遍。
        self.update_stats(self._lists, visibles=visibles)
        # 统计行是「匹配 N 张」的唯一渲染点，而它在窄窗收纳顺序里排第一——
        # 搜索时最该看到的数字反而最先消失。过滤态下强制它可见。
        if self._search_query() or self._label_filter:
            self._stats_label.show()
        # 过滤态会显隐标签 chip、匹配数文案也会变宽，工具栏可用宽度随之变化：
        # 只在 resizeEvent 里重算，会让这些变化把工具栏挤出窗口，直到下次
        # 缩放才恢复。_apply_toolbar_overflow 在隐藏集未变时自身早退，很便宜。
        self._apply_toolbar_overflow()

    def _today_count_from(self, visibles: dict) -> int:
        """角标数：恒为今日聚焦总数（见 _apply_filter 的说明）"""
        return self._today_focus_total()

    def _today_focus_total(self) -> int:
        """全板今日聚焦卡数（不含归档卡）"""
        today = date.today()
        return sum(1 for lst in self._lists for c in lst.cards
                   if not c.archived and c.in_today_focus(today))

    def _set_today_count(self, n: int) -> None:
        self._today_count = n
        self._today_btn.setText(tr("今日 {n}").format(n=n))

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
        act_md = menu.addAction(tr("Markdown（.md）"))
        act_csv = menu.addAction(tr("CSV（.csv）"))
        menu.addSeparator()
        act_backup = menu.addAction(tr("导出备份（.json）"))
        act_import = menu.addAction(tr("从备份导入…"))
        menu.addSeparator()
        act_trello = menu.addAction(tr("导入 Trello 看板（.json）…"))
        act_md_import = menu.addAction(tr("导入 Markdown（.md）…"))
        chosen = menu.exec(self._export_btn.mapToGlobal(
            QPoint(0, self._export_btn.height() + 2)))
        menu.deleteLater()   # 常驻 BoardView 上的菜单不销毁会每次导出累积一个
        if chosen is act_md:
            self.signal_export.emit("md")
        elif chosen is act_csv:
            self.signal_export.emit("csv")
        elif chosen is act_backup:
            self.signal_export_backup.emit()
        elif chosen is act_import:
            self.signal_import_backup.emit()
        elif chosen is act_trello:
            self.signal_import_trello.emit()
        elif chosen is act_md_import:
            self.signal_import_markdown.emit()

    def clear_search_if_active(self) -> bool:
        """清空搜索框（有内容时）；返回是否清空了搜索"""
        if self._search_edit.text():
            self._search_edit.clear()
            return True
        return False

    def search_has_focus(self) -> bool:
        """搜索框是否持有焦点（Esc 折叠链中仅此状态先清空搜索）"""
        return self._search_edit.hasFocus()

    def return_focus_from_search(self) -> None:
        """Esc 退出搜索框：焦点还给看板（随后方向键可直接导航卡片）"""
        self.setFocus()

    def focus_search(self) -> None:
        """Cmd+F 聚焦搜索框并全选"""
        self._search_edit.setFocus()
        self._search_edit.selectAll()

    def paste_target_list_id(self) -> str | None:
        """粘贴建卡的目标列：焦点卡片所在的列（键盘导航后不用再挑列）

        无焦点卡片（鼠标在任意处按 Ctrl+V）返回 None，由控制器落到首列
        ——与 Ctrl+N 快速添加的落点一致。
        """
        w = QApplication.focusWidget()
        while w is not None and not isinstance(w, CardWidget):
            w = w.parentWidget()
        if isinstance(w, CardWidget):
            col = w._owning_column()
            if col is not None:
                return col.list_id()
        return None

    def _make_column(self, board_list: BoardList,
                     collapsed_ids: set[str] | None = None) -> ListColumn:
        """创建列表列并连接信号（每个列生命周期内只连一次）

        collapsed_ids 由调用方一次读取传入（全量构建 10 列时省 10 次
        QSettings 读取 + json 解析）；None 时回退自查（兼容散建单列）。
        """
        col = ListColumn(board_list)
        col.signal_card_edit.connect(self._on_card_edit)
        col.signal_card_done.connect(self._on_card_done)
        col.signal_card_delete.connect(self._on_card_delete)
        col.signal_card_move.connect(self.signal_card_move)
        col.signal_cards_move.connect(self.signal_cards_move)
        col.signal_list_move.connect(self.signal_list_move)
        col.signal_add_card.connect(self.signal_card_add)
        col.signal_add_from_template.connect(self.signal_add_from_template)
        col.signal_title_changed.connect(self.signal_list_title_changed)
        col.signal_delete_list.connect(self.signal_list_delete)
        col.signal_card_pomo.connect(self.signal_card_pomo)
        col.signal_card_archive.connect(self.signal_card_archive)
        col.signal_card_star.connect(self.signal_card_star)
        col.signal_card_workdir_open.connect(self.signal_card_workdir_open)
        col.signal_card_workdir_set.connect(self.signal_card_workdir_set)
        col.signal_card_duplicate.connect(self.signal_card_duplicate)
        col.signal_card_attachment_open.connect(self.signal_card_attachment_open)
        col.signal_card_ctrl_clicked.connect(self._on_card_ctrl_clicked)
        col.signal_card_shift_clicked.connect(self._on_card_shift_clicked)
        col.signal_card_priority.connect(self.signal_card_priority)
        col.signal_card_due_set.connect(self.signal_card_due_set)
        col.signal_files_dropped.connect(self.signal_files_dropped)
        col.signal_card_save_template.connect(self.signal_card_save_template)
        # "移动到列表"复用拖拽落位信号（插到目标列顶部，index=0）
        col.signal_card_move_to.connect(
            lambda cid, tid: self.signal_card_move.emit(cid, tid, 0))
        # 标签过滤是纯视图态：内部消化，不冒泡控制器
        col.signal_label_clicked.connect(self._on_label_clicked)
        # 过滤态起拖被拒：解释原因（列已禁 drop，用户只会看到禁止光标）
        col.signal_drag_blocked.connect(
            lambda: self.show_toast(
                tr("过滤/搜索状态下不可拖拽，清除过滤后可拖动")))
        col.signal_collapsed_changed.connect(self.signal_list_collapsed)
        col.signal_fund_watch.connect(self.signal_list_fund_watch)
        col.signal_fund_export.connect(self.signal_list_fund_export)
        col.signal_duplicate_requested.connect(self.signal_list_duplicate)
        self._columns.append(col)
        # 恢复上次折叠状态（save=False 不触发持久化回调；建列时不播动画）
        if collapsed_ids is None:
            collapsed_ids = AppConfig.get_collapsed_lists()
        if board_list.id in collapsed_ids:
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
            self._stats_label.setText(tr("匹配 {n} 张").format(n=total))
            return
        if self._label_filter:
            # 标签过滤态：统计行持续反馈（数据变更经 refresh 刷新也不丢）
            if visibles is None:
                total = sum(len(self._visible_cards_for(l) or []) for l in lists)
            else:
                total = sum(len(v or []) for v in visibles.values())
            name = AppConfig.LABEL_NAMES.get(self._label_filter,
                                             self._label_filter)
            self._stats_label.setText(
                tr("标签 {name} · {n} 张").format(name=name, n=total))
            return
        if stats is not None:
            self._stats_label.setText(
                tr("{total} 张卡片 · 完成 {done}").format(
                    total=stats['total'], done=stats['done']))
            return
        total = sum(len(l.cards) for l in lists)
        done = sum(1 for l in lists for card in l.cards if card.done)
        self._stats_label.setText(
            tr("{total} 张卡片 · 完成 {done}").format(total=total, done=done))

    def _update_empty_hint(self, lists: list[BoardList]) -> None:
        """无任何列表时显示空看板引导（覆盖列表区，可穿透鼠标）

        窄窗下「+ 添加列表」可能已被收进「»」菜单——引导指向一个
        看不见的按钮等于指错路，按收纳态切换文案。
        """
        if lists:
            self._empty_hint.hide()
        else:
            if "add_list" in self._overflow_hidden:
                self._empty_hint.setText(
                    tr("看板还是空的\n点击右上角「»」→「+ 添加列表」创建第一列"))
            else:
                self._empty_hint.setText(
                    tr("看板还是空的\n点击右上角「+ 添加列表」创建第一列"))
            self._empty_hint.setGeometry(self.rect())
            self._empty_hint.show()
            self._empty_hint.raise_()

    def resizeEvent(self, event) -> None:
        """空状态引导与 toast 跟随窗口尺寸重定位；工具栏按宽度收纳"""
        super().resizeEvent(event)
        self._apply_toolbar_overflow()
        if self._empty_hint.isVisible():
            self._empty_hint.setGeometry(self.rect())
        if self._toast.isVisible():
            # 复用 show_toast 的避让偏移：多选栏可见时 toast 停在栏上方，
            # 窗口尺寸变化不该把它甩回底部盖住按钮
            self._toast.move((self.width() - self._toast.width()) // 2,
                             self.height() - self._toast.height() - 20
                             - self._toast_bottom_offset())
        if self._selection_bar.isVisible():
            self._update_selection_bar()

    # ── 工具栏窄窗收纳 ────────────────────────────────────

    def _aux_toolbar_widgets(self) -> list:
        """不参与收纳的固定控件（交通灯 / 窗口控制键 / 折叠键 / 标签筛选 chip）

        标签 chip 是动态显隐的（点色条开启筛选才出现），漏算它会让
        开启筛选后的工具栏超宽，收纳判定按不足的宽度算导致控件重叠。
        """
        return [w for w in (getattr(self, "_traffic_lights", None),
                            self._window_controls, self._collapse_btn,
                            getattr(self, "_label_chip", None))
                if w is not None and not w.isHidden()]

    @staticmethod
    def _min_width_of(widget) -> int:
        """控件在布局里实际占用的最小宽（收纳判定用）

        必须优先取 `minimumWidth()`，不能只用 `minimumSizeHint()`：
        - `setFixedWidth` 的按钮（"+ 添加列表" 96 / "+ 批量" 72）的
          minimumSizeHint 只有 80 / 56 —— 低估 32px；
        - `setFixedSize` 的自绘控件（交通灯）的 minimumSizeHint 是**无效值
          -1** —— 直接求和反而把总宽算小 55px。
        两处叠加曾让收纳判定以为"还放得下"，而实际布局早已压到重叠：
        实测 900px 下搜索框与"+ 添加列表"叠了 15px。
        """
        min_w = widget.minimumWidth()
        if min_w > 0:
            return min_w
        hint = widget.minimumSizeHint().width()
        return hint if hint > 0 else 0

    def _apply_toolbar_overflow(self) -> None:
        """窄窗收纳：放不下的次要按钮折进「»」菜单

        BOARD_MIN_WIDTH 是"够放整个工具栏"的下限，但小屏上它会被屏幕
        宽度钳低（见 MainWindow._effective_min_size），此时十来个控件的
        线型排布必然重叠：此前搜索框与「+ 添加列表」叠字、看板名被裁成
        半截。这里按优先级把次要项收进菜单，保证任何宽度下都不重叠。
        """
        if self.width() <= 0 or not hasattr(self, "_overflow_btn"):
            return
        lay = self._toolbar_layout
        margins = lay.contentsMargins()
        avail = self.width() - margins.left() - margins.right()
        spacing = lay.spacing()
        aux_w = sum(self._min_width_of(w)
                    for w in self._aux_toolbar_widgets())

        def needed_width(hidden: set) -> int:
            n = len(self._aux_toolbar_widgets())
            total = aux_w
            for key, widget in self._toolbar_items:
                if key in hidden:
                    continue
                if key == "overflow" and not hidden:
                    continue    # 没有收纳项时「»」自身隐藏，不占宽
                # 显式设过最小宽的控件按它算，而不是 minimumSizeHint：
                # `setFixedWidth` 的按钮（"+ 添加列表" 96 / "+ 批量" 72）的
                # minimumSizeHint 只有 80 / 56——按 hint 估算会低估 32px，
                # 收纳判定于是以为"还放得下"，实际布局早已压到重叠
                # （实测 900px 下搜索框与"+ 添加列表"叠了 15px）。
                total += self._min_width_of(widget)
                n += 1
            return total + spacing * max(0, n - 1)

        hidden: set = set()
        for key in self._overflow_order:
            if needed_width(hidden) <= avail:
                break
            hidden.add(key)

        # ── 终局降级：先把搜索框压到硬下限，再确认是否还要收更多 ──
        # 上面的循环把可收纳项收完就停，但剩余不可收纳项（标题 + 搜索 +
        # 齿轮 + 主题 + »）在小屏上仍可能超宽，而 _effective_min_size 允许
        # 窗口窄到 320px。此前没有兜底——正是本函数 docstring 说要防的那种
        # 重叠：控件直接叠在一起，且没有任何恢复路径。
        # 搜索框是可压缩项，先扣它；仍不够才把「今日」也收进「»」菜单。
        over = needed_width(hidden) - avail
        if over > 0:
            target = max(self._search_min_floor, self._search_min_w - over)
            if target != self._search_edit.minimumWidth():
                self._search_edit.setMinimumWidth(target)
        if needed_width(hidden) > avail:
            hidden.add("today")

        if hidden == set(self._overflow_hidden):
            return
        self._overflow_hidden = [k for k in self._overflow_order
                                 if k in hidden]
        for key, widget in self._toolbar_items:
            if key in self._overflow_order:
                widget.setVisible(key not in hidden)
        self._sync_overflow_menu()
        # 「+ 添加列表」收进菜单后，空看板引导要同步改指「»」入口
        if self._empty_hint.isVisible():
            self._update_empty_hint([])

    def _sync_overflow_menu(self) -> None:
        """被收纳的入口进「»」菜单（点菜单项等价于点原按钮）

        日历按钮是自绘图标按钮（text() 为空），直接读 text() 会出现
        空白菜单项——优先读显式文案表，按钮文本兜底。
        """
        labels = {"calendar": tr("日历"), "export": tr("导出"),
                  "archive": tr("归档"), "add_list": tr("+ 添加列表"),
                  # 「今日」按钮的 text() 是动态的（"今日 5"），进菜单要固定文案
                  "today": tr("今日聚焦"),
                  "title": tr("切换 / 管理看板")}
        self._overflow_menu.clear()
        by_key = dict(self._toolbar_items)
        actionable = 0
        for key in self._overflow_hidden:
            widget = by_key.get(key)
            if widget is None or not hasattr(widget, "click"):
                continue        # 纯信息项（统计数字）不进菜单
            text = labels.get(key) or widget.text() or widget.toolTip()
            act = self._overflow_menu.addAction(text)
            act.triggered.connect(widget.click)
            actionable += 1
        self._overflow_btn.setVisible(actionable > 0)

    def show_toast(self, text: str) -> None:
        """显示看板内轻提示（折叠态由控制器改走托盘通知）"""
        self._toast.show_message(text,
                                 bottom_offset=self._toast_bottom_offset())

    def _toast_bottom_offset(self) -> int:
        """toast 距底部的额外抬高：底部多选操作栏可见时避让到栏上方

        （两者都钉在底部居中，不避让 toast 会整块盖住批量按钮）
        """
        bar = self._selection_bar
        return (bar.height() + 12) if (bar is not None
                                       and bar.isVisible()) else 0

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

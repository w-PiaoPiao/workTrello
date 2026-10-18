"""单一矢量图标工厂：把"第三套图标语言"收口

为什么需要这一层
----------------
项目里同时长出了三套图标语言：

1. **自绘矢量**（`board_view` 的齿轮 / 日月 / 日历 / 勾选框、`today_popover`
   的星标、`traffic_lights` 的三色灯）——跨平台一致；
2. **Unicode 字形当图标**：`✕` `＋` `⏱` `≡` `◀` `▶`；
3. **emoji**：`📎` `📂` `🍅` `⭐` `☆` `🔁` `🏷` `👁` `📋` `🧭`。

后两类的字形由平台字体决定（Apple / Segoe / Noto 各画各的），基线也各不相同，
卡片信息行因此高低不齐；缺字形的平台直接渲染成豆腐块——`board_view.py` 与
`today_popover.py` 都为此写了注释并改成矢量，但当时只改了勾选框和星标，
其余仍是字形/emoji。本模块把那套画法收成一个工厂，让"再加一个图标"不需要
再复制一份 paintEvent。

设计要点
--------
- **统一线宽 `STROKE`（1.6px，设备像素，不随尺寸缩放）**：与现有自绘
  （齿轮 / 日历 1.4px、星标 1.4px）取同一视觉重量，略粗一档是因为
  这批图标多用于 16px 小按钮。
- **四档尺寸** `SIZES`：16 / 18 / 20 / 24，几何按 24 格坐标系定义后等比缩放，
  所以四档都是同一张图。
- **颜色从 `AppTheme` 实时取**：`icon()` 返回的 QIcon 用 `_IconEngine` 按需绘制，
  `paint()` 被调用时才读主题色——`QIcon(QPixmap)` 是静态快照，主题切换后
  不重建就还是旧色。`color` 传主题键名（如 `"accent"`）同样走实时查表；
  传 `"#RRGGBB"` / QColor 则是固定色。
- **只用 `setPen`，画刷只置 `NoBrush`**：`setBrush(Qt.NoPen)` 在 PySide6 6.10
  会抛 ValueError，而 paintEvent 内异常会泄漏 painter 并损坏 backing store
  （见 `traffic_lights.py` 的注释）。
- **不做路径缓存**：这里每条路径都是几个 `lineTo`，没有 `subtracted` 那种
  布尔运算；`board_view` 缓存齿轮环是因为布尔运算贵，不是所有路径都该缓存。
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (QColor, QIcon, QIconEngine, QPainter, QPainterPath,
                           QPen, QPixmap, QPolygonF)
from PySide6.QtWidgets import QPushButton

from app.views.theme import AppTheme

# 统一线宽：设备像素，不随尺寸缩放（16px 档也是这个粗细）
STROKE = 1.6

# 支持的四档尺寸
SIZES = (16, 18, 20, 24)

_DEFAULT_SIZE = 18


# ── 绘制基元 ──────────────────────────────────────────────────

def _pen(color: QColor) -> QPen:
    """统一画笔：圆头圆角连接，避免小尺寸下的尖角毛刺"""
    pen = QPen(color, STROKE)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def _grid(size: float) -> float:
    """24 格坐标系 → 实际像素的换算系数

    所有图标都在 24×24 的格子里定义几何，换档只改这个系数，
    不用给每一档写一套坐标。
    """
    return size / 24.0


def _arrow_head(painter: QPainter, tip: QPointF, dx: float, dy: float,
                size: float) -> None:
    """在 tip 处画一个朝向 (dx, dy) 的箭头（两段短线）

    只用于 repeat 图标；方向向量由调用方按角度算好，这里只做几何。
    """
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length
    for sign in (1, -1):
        ang = math.radians(35 * sign)
        rx = ux * math.cos(ang) - uy * math.sin(ang)
        ry = ux * math.sin(ang) + uy * math.cos(ang)
        painter.drawLine(tip, QPointF(tip.x() - rx * size,
                                      tip.y() - ry * size))


# ── 各图标几何（坐标写在 24 格坐标系里）───────────────────────

def _p_close(p: QPainter, s: float) -> None:
    u = _grid(s)
    p.drawLine(QPointF(7.4 * u, 7.4 * u), QPointF(16.6 * u, 16.6 * u))
    p.drawLine(QPointF(16.6 * u, 7.4 * u), QPointF(7.4 * u, 16.6 * u))


def _p_add(p: QPainter, s: float) -> None:
    u = _grid(s)
    p.drawLine(QPointF(12 * u, 6.2 * u), QPointF(12 * u, 17.8 * u))
    p.drawLine(QPointF(6.2 * u, 12 * u), QPointF(17.8 * u, 12 * u))


def _p_note(p: QPainter, s: float) -> None:
    """备注：三条横线（≡）"""
    u = _grid(s)
    for y in (7.2, 12.0, 16.8):
        p.drawLine(QPointF(6.4 * u, y * u), QPointF(17.6 * u, y * u))


def _p_attach(p: QPainter, s: float) -> None:
    """回形针：左侧长边 → 顶部半圆 → 右侧长边 → 底部半圆 → 内侧短线"""
    u = _grid(s)
    path = QPainterPath()
    path.moveTo(9.0 * u, 15.5 * u)
    path.lineTo(9.0 * u, 8.5 * u)
    path.arcTo(QRectF(9.0 * u, 5.5 * u, 6.0 * u, 6.0 * u), 180, -180)
    path.lineTo(15.0 * u, 16.0 * u)
    path.arcTo(QRectF(11.0 * u, 14.0 * u, 4.0 * u, 4.0 * u), 0, -180)
    path.lineTo(11.0 * u, 10.5 * u)
    p.drawPath(path)


def _p_image(p: QPainter, s: float) -> None:
    """图片：圆角相框 + 太阳 + 山线"""
    u = _grid(s)
    p.drawRoundedRect(QRectF(4.6 * u, 6.2 * u, 14.8 * u, 11.6 * u),
                      2.0 * u, 2.0 * u)
    p.drawEllipse(QPointF(9.0 * u, 10.0 * u), 1.15 * u, 1.15 * u)
    hill = QPainterPath()
    hill.moveTo(5.8 * u, 16.8 * u)
    hill.lineTo(10.2 * u, 12.4 * u)
    hill.lineTo(13.0 * u, 15.2 * u)
    hill.lineTo(15.2 * u, 13.0 * u)
    hill.lineTo(18.4 * u, 16.2 * u)
    p.drawPath(hill)


def _p_file(p: QPainter, s: float) -> None:
    """文件：折角页面"""
    u = _grid(s)
    page = QPainterPath()
    page.moveTo(6.2 * u, 4.6 * u)
    page.lineTo(13.8 * u, 4.6 * u)
    page.lineTo(18.4 * u, 9.2 * u)
    page.lineTo(18.4 * u, 19.4 * u)
    page.lineTo(6.2 * u, 19.4 * u)
    page.closeSubpath()
    p.drawPath(page)
    fold = QPainterPath()
    fold.moveTo(13.8 * u, 4.6 * u)
    fold.lineTo(13.8 * u, 9.2 * u)
    fold.lineTo(18.4 * u, 9.2 * u)
    p.drawPath(fold)


def _p_clock(p: QPainter, s: float) -> None:
    u = _grid(s)
    p.drawEllipse(QPointF(12 * u, 12 * u), 7.0 * u, 7.0 * u)
    p.drawLine(QPointF(12 * u, 7.8 * u), QPointF(12 * u, 12 * u))
    p.drawLine(QPointF(12 * u, 12 * u), QPointF(15.4 * u, 13.6 * u))


def _p_tomato(p: QPainter, s: float) -> None:
    """番茄：果实 + 果蒂与两片叶"""
    u = _grid(s)
    p.drawEllipse(QPointF(12 * u, 13.6 * u), 6.2 * u, 6.0 * u)
    p.drawLine(QPointF(12 * u, 7.4 * u), QPointF(12 * u, 5.2 * u))
    p.drawLine(QPointF(12 * u, 6.9 * u), QPointF(9.6 * u, 5.7 * u))
    p.drawLine(QPointF(12 * u, 6.9 * u), QPointF(14.4 * u, 5.7 * u))


def _star_polygon(s: float) -> QPolygonF:
    """五角星顶点（与 today_popover._StarButton 同款算法：外径 0.40、内径 0.45）"""
    cx = cy = s / 2
    r_out = s * 0.40
    r_in = r_out * 0.45
    poly = QPolygonF()
    for i in range(10):
        ang = math.radians(-90 + i * 36)
        r = r_out if i % 2 == 0 else r_in
        poly.append(QPointF(cx + r * math.cos(ang), cy + r * math.sin(ang)))
    return poly


def _p_star(p: QPainter, s: float) -> None:
    p.drawPolygon(_star_polygon(s))


def _p_star_filled(p: QPainter, s: float) -> None:
    p.setBrush(p.pen().color())
    p.drawPolygon(_star_polygon(s))


def _p_repeat(p: QPainter, s: float) -> None:
    """循环：上下两段圆弧 + 箭头（替代 🔁）"""
    u = _grid(s)
    cx = cy = 12.0 * u
    r = 6.2 * u
    rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
    for start, sweep, tip_deg in ((150, -120, 30), (-30, -120, -150)):
        path = QPainterPath()
        path.arcMoveTo(rect, start)
        path.arcTo(rect, start, sweep)
        p.drawPath(path)
        ang = math.radians(tip_deg)
        tip = QPointF(cx + r * math.cos(ang), cy - r * math.sin(ang))
        # 顺时针走（角度递减）时切线方向 = (sinθ, cosθ)（y 轴向下）
        _arrow_head(p, tip, math.sin(ang), math.cos(ang), 2.9 * u)


def _p_tag(p: QPainter, s: float) -> None:
    """标签：菱形牌 + 穿线孔（替代 🏷）"""
    u = _grid(s)
    tag = QPainterPath()
    tag.moveTo(11.0 * u, 4.6 * u)
    tag.lineTo(19.4 * u, 13.0 * u)
    tag.lineTo(13.0 * u, 19.4 * u)
    tag.lineTo(4.6 * u, 11.0 * u)
    tag.closeSubpath()
    p.drawPath(tag)
    p.drawEllipse(QPointF(8.9 * u, 8.9 * u), 1.15 * u, 1.15 * u)


def _p_eye(p: QPainter, s: float) -> None:
    u = _grid(s)
    eye = QPainterPath()
    eye.moveTo(4.4 * u, 12 * u)
    eye.cubicTo(7.8 * u, 6.6 * u, 16.2 * u, 6.6 * u, 19.6 * u, 12 * u)
    eye.cubicTo(16.2 * u, 17.4 * u, 7.8 * u, 17.4 * u, 4.4 * u, 12 * u)
    p.drawPath(eye)
    p.drawEllipse(QPointF(12 * u, 12 * u), 2.5 * u, 2.5 * u)


def _p_folder(p: QPainter, s: float) -> None:
    """文件夹：带标签页的开口夹（替代 📂）"""
    u = _grid(s)
    folder = QPainterPath()
    folder.moveTo(4.6 * u, 17.8 * u)
    folder.lineTo(4.6 * u, 6.8 * u)
    folder.lineTo(10.0 * u, 6.8 * u)
    folder.lineTo(11.9 * u, 9.2 * u)
    folder.lineTo(19.4 * u, 9.2 * u)
    folder.lineTo(19.4 * u, 17.8 * u)
    folder.closeSubpath()
    p.drawPath(folder)


def _p_compass(p: QPainter, s: float) -> None:
    """罗盘：外圈 + 指针（替代 🧭）"""
    u = _grid(s)
    p.drawEllipse(QPointF(12 * u, 12 * u), 7.2 * u, 7.2 * u)
    needle = QPolygonF([QPointF(15.6 * u, 8.4 * u),
                        QPointF(13.1 * u, 13.1 * u),
                        QPointF(8.4 * u, 15.6 * u),
                        QPointF(10.9 * u, 10.9 * u)])
    p.drawPolygon(needle)


def _p_check(p: QPainter, s: float) -> None:
    u = _grid(s)
    check = QPainterPath()
    check.moveTo(5.4 * u, 12.6 * u)
    check.lineTo(10.0 * u, 17.2 * u)
    check.lineTo(18.6 * u, 7.4 * u)
    p.drawPath(check)


def _p_chevron_left(p: QPainter, s: float) -> None:
    u = _grid(s)
    path = QPainterPath()
    path.moveTo(14.6 * u, 6.4 * u)
    path.lineTo(8.6 * u, 12 * u)
    path.lineTo(14.6 * u, 17.6 * u)
    p.drawPath(path)


def _p_chevron_right(p: QPainter, s: float) -> None:
    u = _grid(s)
    path = QPainterPath()
    path.moveTo(9.4 * u, 6.4 * u)
    path.lineTo(15.4 * u, 12 * u)
    path.lineTo(9.4 * u, 17.6 * u)
    p.drawPath(path)


_PAINTERS = {
    "close": _p_close,
    "add": _p_add,
    "note": _p_note,
    "attach": _p_attach,
    "image": _p_image,
    "file": _p_file,
    "clock": _p_clock,
    "tomato": _p_tomato,
    "star": _p_star,
    "star-filled": _p_star_filled,
    "repeat": _p_repeat,
    "tag": _p_tag,
    "eye": _p_eye,
    "folder": _p_folder,
    "compass": _p_compass,
    "check": _p_check,
    "chevron-left": _p_chevron_left,
    "chevron-right": _p_chevron_right,
}


def names() -> tuple[str, ...]:
    """本工厂支持的全部图标名（供自测/文档使用）"""
    return tuple(sorted(_PAINTERS))


# ── 取色 ──────────────────────────────────────────────────────

def _resolve(color) -> QColor:
    """颜色参数 → QColor

    - `None`：主题 `text_primary`（最常用的前景色）
    - 主题键名（`"accent"` / `"danger"` / …）：**每次调用实时查表**，跟随主题
    - 其它（`"#RRGGBB"` / QColor）：固定色
    """
    if color is None:
        return QColor(AppTheme.colors()["text_primary"])
    if isinstance(color, QColor):
        return QColor(color)
    c = AppTheme.colors()
    if isinstance(color, str) and color in c:
        return QColor(c[color])
    resolved = QColor(color)
    return resolved if resolved.isValid() else QColor(c["text_primary"])


# ── 绘制入口 ──────────────────────────────────────────────────

def paint(painter: QPainter, name: str, size: float, color=None) -> None:
    """把图标画进已打开的 painter（自绘控件用这一层）

    调用方负责 `setRenderHint(Antialiasing)`；这里只设画笔，画刷交给各图标的
    几何自己决定（描边图标置 `NoBrush`，实心图标自设画刷）。
    """
    fn = _PAINTERS.get(name)
    if fn is None or size <= 0:
        return
    painter.setPen(_pen(_resolve(color)))
    painter.setBrush(Qt.NoBrush)
    fn(painter, float(size))


class _IconEngine(QIconEngine):
    """按需绘制的图标引擎：每次 paint/pixmap 才取主题色与几何

    `QIcon(QPixmap)` 是静态快照——主题切换后必须重建每一个图标控件才不残留
    旧色。引擎方式把"取色"推迟到绘制那一刻，主题切换零重建。
    """

    def __init__(self, name: str, size: int, color) -> None:
        super().__init__()
        self._name = name
        self._size = size
        self._color = color

    def paint(self, painter: QPainter, rect, mode, state) -> None:
        side = min(rect.width(), rect.height()) or self._size
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.translate(rect.center())
        painter.translate(-side / 2.0, -side / 2.0)
        paint(painter, self._name, side, self._color)
        painter.restore()

    def pixmap(self, size: QSize, mode, state) -> QPixmap:
        side = min(size.width(), size.height()) or self._size
        pm = QPixmap(side, side)
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing)
        paint(painter, self._name, side, self._color)
        painter.end()
        return pm


def icon(name: str, size: int = _DEFAULT_SIZE, color=None) -> QIcon:
    """图标 → QIcon（`setIcon` 用）

    `size` 只作为默认档位提示；实际绘制按 Qt 请求的矩形尺寸等比缩放，
    所以 `setIconSize()` 与它不一致时也不会画出错位的图。
    """
    return QIcon(_IconEngine(name, size, color))


def pixmap(name: str, size: int = _DEFAULT_SIZE, color=None) -> QPixmap:
    """图标 → QPixmap（QLabel.setPixmap 用）"""
    side = max(1, int(size))
    pm = QPixmap(side, side)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    paint(painter, name, side, color)
    painter.end()
    return pm


def apply_icon(button, name: str, size: int = 16, color=None) -> None:
    """给按钮装上矢量图标并同步 iconSize（`setIcon` + `setIconSize` 的合并）"""
    button.setIcon(icon(name, size, color))
    button.setIconSize(QSize(size, size))


# ── 独立图标按钮 ──────────────────────────────────────────────

class IconButton(QPushButton):
    """自绘矢量图标按钮

    与 `setIcon()` 的区别：QSS 的 `color` 管不到 QIcon（悬停变色会失效），
    而本类在 `paintEvent` 里自己取色，所以：

    - 悬停色（`hover_color`）能跟随 `:hover` 语义，与原有 ✕ 按钮
      "常态次要色、悬停危险红" 的行为一致；
    - 主题切换时 `update()` 即换色，不需要重建图标；
    - 图标绘制不经过 QStyle 的文字绘制区，不会踩到"padding: 0 把内容区
      压成 0，字一个像素都不画"那个坑（`_remove_button_style` 的注释）。
    """

    def __init__(self, name: str, size: int = 16, color=None,
                 hover_color=None, parent=None) -> None:
        super().__init__(parent)
        self._icon_name = name
        self._icon_size = size
        self._color = color
        self._hover_color = hover_color
        self._hovered = False
        self.setIconSize(QSize(size, size))

    # ── 状态 ──
    def set_icon_name(self, name: str) -> None:
        if name != self._icon_name:
            self._icon_name = name
            self.update()

    def set_colors(self, color=None, hover_color=None) -> None:
        self._color = color
        self._hover_color = hover_color
        self.update()

    def enterEvent(self, event) -> None:
        self._hovered = True
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:
        self._hovered = False
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event) -> None:
        # 先让 QSS 画背景/边框（悬停底色等仍由样式表驱动），再叠加矢量图标；
        # 图标由本类直接绘制，不经过 QStyle 的文字绘制区，所以不会踩到
        # "padding: 0 把内容区压成 0，字一个像素都不画"那个坑
        # （`_remove_button_style` 的注释）。
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = self._color
        if self._hovered and self._hover_color is not None:
            color = self._hover_color
        paint(painter, self._icon_name, min(self.width(), self.height()),
              color)
        painter.end()

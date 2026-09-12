"""应用图标：「桌宠探头」—— 猫耳桌宠从看板后探头的矢量绘制

与 design/icon_candidates.html 中 8 号候选同源。托盘、窗口图标与
tools/make_icons.py（生成 ico/icns/png 资源）共用这一份绘制代码，
保证各处图标一致。

坐标系：设计基准 64×64，绘制时统一 scale 到目标尺寸，
线宽等细节随比例缩放；16px 级别自动省略小细节保可辨识度。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
    QPen,
)

# ── 配色（与候选页一致）──────────────────────────────
BG_TOP = QColor("#38BDF8")      # 背景渐变：天蓝
BG_BOTTOM = QColor("#6366F1")   # 背景渐变：靛蓝
WHITE = QColor("#FFFFFF")
INK = QColor("#2B2F3A")         # 眼睛
BLUSH = QColor(255, 179, 193)   # 腮红 #FFB3C1
INNER_EAR = QColor("#FFC9D6")
COLUMN = QColor("#C9D8F6")      # 看板列
PAW_EDGE = QColor("#D7E3F8")    # 爪子描边

# 背景连续圆角（squircle）轮廓，64×64 基准
_SQUIRCLE = QPainterPath()
_SQUIRCLE.moveTo(32, 0)
_SQUIRCLE.cubicTo(9.6, 0, 0, 9.6, 0, 32)
_SQUIRCLE.cubicTo(0, 54.4, 9.6, 64, 32, 64)
_SQUIRCLE.cubicTo(54.4, 64, 64, 54.4, 64, 32)
_SQUIRCLE.cubicTo(64, 9.6, 54.4, 0, 32, 0)
_SQUIRCLE.closeSubpath()


def _tri(path: QPainterPath, *points: tuple[float, float]) -> None:
    """三角形子路径（fill + 描边时用圆角连接柔化尖角）"""
    path.moveTo(*points[0])
    for p in points[1:]:
        path.lineTo(*p)
    path.closeSubpath()


def paint_peek_pet(painter: QPainter, size: int) -> None:
    """在 painter 上绘制图标（假定 painter 对应 size×size 的透明画布）"""
    k = size / 64.0
    tiny = size < 20  # 16px：只保留耳朵/头/看板剪影

    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.scale(k, k)  # 之后全部按 64 基准坐标绘制

    # 背景连续圆角方块 + 45° 渐变
    grad = QLinearGradient(0, 0, 64, 64)
    grad.setColorAt(0, BG_TOP)
    grad.setColorAt(1, BG_BOTTOM)
    painter.fillPath(_SQUIRCLE, QBrush(grad))

    # ── 桌宠（先画，下半被看板遮住形成"探头"）────────
    ear_pen = QPen(WHITE, 2.5)
    ear_pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(ear_pen)
    painter.setBrush(WHITE)
    ears = QPainterPath()
    _tri(ears, (23.5, 19), (25.5, 8.5), (31.5, 14.5))
    _tri(ears, (40.5, 19), (38.5, 8.5), (32.5, 14.5))
    painter.drawPath(ears)
    if not tiny:
        inner = QPainterPath()
        _tri(inner, (25.4, 16.2), (26.2, 11.8), (29.0, 14.6))
        _tri(inner, (38.6, 16.2), (37.8, 11.8), (35.0, 14.6))
        painter.setPen(Qt.NoPen)
        painter.setBrush(INNER_EAR)
        painter.drawPath(inner)

    painter.setPen(Qt.NoPen)
    painter.setBrush(WHITE)
    painter.drawEllipse(QPointF(32, 24), 12.5, 12.5)  # 头

    # 眼睛 + 高光
    painter.setBrush(INK)
    painter.drawEllipse(QPointF(27.8, 23.5), 2.1, 2.1)
    painter.drawEllipse(QPointF(36.2, 23.5), 2.1, 2.1)
    if not tiny:
        painter.setBrush(WHITE)
        painter.drawEllipse(QPointF(27.1, 22.8), 0.8, 0.8)
        painter.drawEllipse(QPointF(35.5, 22.8), 0.8, 0.8)
        painter.setBrush(BLUSH)
        painter.drawEllipse(QPointF(24, 27), 2.4, 1.5)
        painter.drawEllipse(QPointF(40, 27), 2.4, 1.5)

    # ── 看板 + 三列 ──────────────────────────────────
    painter.setBrush(WHITE)
    painter.drawRoundedRect(QRectF(10, 29, 44, 23), 7, 7)
    painter.setBrush(COLUMN)
    for x in (16, 27.2, 38.4):
        painter.drawRoundedRect(QRectF(x, 34.5, 9.5, 12), 2, 2)

    # 搭在看板上的爪子
    if not tiny:
        paw_pen = QPen(PAW_EDGE, 1.2)
        painter.setPen(paw_pen)
        painter.setBrush(WHITE)
        painter.drawEllipse(QPointF(24, 29.5), 3.1, 3.1)
        painter.drawEllipse(QPointF(40, 29.5), 3.1, 3.1)

    painter.restore()


def render_pixmap(size: int) -> QPixmap:
    """渲染 size×size 的图标位图（小尺寸用超采样抗锯齿）"""
    ss = 4 if size <= 128 else (2 if size <= 512 else 1)
    pm = QPixmap(size * ss, size * ss)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    paint_peek_pet(painter, size * ss)
    painter.end()
    if ss > 1:
        pm = pm.scaled(size, size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return pm


_icon_cache: QIcon | None = None


def create_app_icon() -> QIcon:
    """多尺寸应用图标（托盘 / 窗口 / 任务栏共用）

    进程内只渲染一次：7 个尺寸含 4x 超采样，重复渲染是启动路径上的
    纯浪费（main 与 TrayService 各调一次）。图标静态，缓存安全。
    """
    global _icon_cache
    if _icon_cache is None:
        icon = QIcon()
        for size in (16, 24, 32, 48, 64, 128, 256):
            icon.addPixmap(render_pixmap(size))
        _icon_cache = icon
    return _icon_cache

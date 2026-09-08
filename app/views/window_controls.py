"""
Windows 窗口控制键（最小化 ─ / 最大化 □ / 关闭 ✕）

仅 Windows 创建（AppConfig.IS_WINDOWS），对齐 Windows 原生标题栏范式：
右下角贴边三个方条按钮，悬停半透明灰底，关闭键悬停红底白 ✕。

信号语义与 macOS 红绿灯一致：
  signal_minimize = 折叠回桌宠（黄灯对应物）
  signal_zoom     = 最大化 / 还原（绿灯对应物）
  signal_close    = 退出应用（红灯对应物）
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from app.views.theme import AppTheme


class WindowControls(QWidget):
    """Windows 风格三键（自绘，无系统装饰依赖）"""

    signal_minimize = Signal()
    signal_zoom = Signal()
    signal_close = Signal()

    _BTN_W = 46          # 每个按钮宽度（对齐原生标题栏按钮区）
    _BTN_H = 40          # 按钮高度（撑满工具栏内边距后的可用高度）
    _RESIZE_HOVER = 8    # hover 高亮矩形与按钮边缘间距

    def __init__(self, parent=None):
        super().__init__(parent)
        self._zoomed = False
        self._hover = -1
        self._pressed = -1
        self.setMouseTracking(True)
        self.setFixedSize(self._BTN_W * 3, self._BTN_H)

    # ── 几何 ──────────────────────────────────────────────

    def _btn_rect(self, index: int) -> QRectF:
        return QRectF(index * self._BTN_W, 0, self._BTN_W, self._BTN_H)

    def _index_at(self, pos: QPointF) -> int:
        for i in range(3):
            if self._btn_rect(i).contains(pos):
                return i
        return -1

    # ── 状态 ──────────────────────────────────────────────

    def set_zoomed(self, zoomed: bool) -> None:
        """切换最大化图标（□ ⇆ ❐ 还原）"""
        if zoomed != self._zoomed:
            self._zoomed = zoomed
            self.update()

    def reapply(self) -> None:
        """主题切换时刷新绘制（颜色在 paintEvent 实时取）"""
        self.update()

    # ── 绘制 ──────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        c = AppTheme.colors()
        for i in range(3):
            r = self._btn_rect(i)
            if self._hover == i or self._pressed == i:
                if i == 2:      # 关闭键 hover/按下：红底
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QColor(c["danger"]))
                    painter.drawRect(QRectF(r.left() + 1, r.top() + 1,
                                            r.width() - 2, r.height() - 2))
                else:
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QColor(128, 128, 128, 77))
                    painter.drawRect(QRectF(r.left() + 1, r.top() + 1,
                                            r.width() - 2, r.height() - 2))
        self._paint_glyph(painter)
        painter.end()

    def _paint_glyph(self, painter: QPainter) -> None:
        # 图标色：关闭键 hover/按下时为白，其余取主题主文字色
        c = AppTheme.colors()
        close_active = self._hover == 2 or self._pressed == 2
        glyph = QColor(Qt.white if close_active else QColor(c["text_primary"]))
        pen = QPen(glyph, 1.6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        cy = self._BTN_H / 2
        for i in range(3):
            cx = i * self._BTN_W + self._BTN_W / 2
            if i == 0:      # 最小化 ─
                painter.drawLine(QPointF(cx - 8, cy), QPointF(cx + 8, cy))
            elif i == 1:    # 最大化 □ / 还原 ❐
                s = 9.0
                if self._zoomed:
                    # 还原图标：内缩的小方框
                    painter.drawRect(QRectF(cx - s / 2, cy - s / 2,
                                            s - 2, s - 2))
                else:
                    painter.drawRect(QRectF(cx - s / 2, cy - s / 2, s, s))
            else:           # 关闭 ✕
                painter.drawLine(QPointF(cx - 7, cy - 7),
                                 QPointF(cx + 7, cy + 7))
                painter.drawLine(QPointF(cx - 7, cy + 7),
                                 QPointF(cx + 7, cy - 7))

    # ── 交互 ──────────────────────────────────────────────

    def mouseMoveEvent(self, event) -> None:
        idx = self._index_at(event.position())
        if idx != self._hover:
            self._hover = idx
            self.update()

    def leaveEvent(self, event) -> None:
        if self._hover != -1:
            self._hover = -1
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        # 对齐原生：release 仍在同一键上才触发，按住拖出可反悔
        self._pressed = self._index_at(event.position())
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        idx = self._index_at(event.position())
        if idx != -1 and idx == self._pressed:
            if idx == 0:
                self.signal_minimize.emit()
            elif idx == 1:
                self.signal_zoom.emit()
            else:
                self.signal_close.emit()
        self._pressed = -1
        self.update()

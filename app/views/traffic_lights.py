"""
macOS 交通灯窗口控制键

仅 macOS 创建（AppConfig.IS_MACOS），对齐 macOS 窗口交互范式：
红 = 退出应用；黄 = 折叠为桌宠（相当于最小化）；绿 = 最大化 / 还原。
悬停时显示 ✕ / − / ⤢ 符号，与系统红绿灯行为一致。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from app.i18n import tr


class TrafficLights(QWidget):
    """红绿灯三键（自绘，无系统装饰依赖）"""

    signal_close = Signal()
    signal_minimize = Signal()
    signal_zoom = Signal()

    _RADIUS = 6.0
    _GAP = 8.0
    _FILL = ((255, 95, 87), (254, 188, 46), (40, 200, 64))
    _SYMBOL = QColor(66, 44, 20, 175)
    _TIP_DELAY_MS = 600

    @staticmethod
    def _tip_texts() -> tuple[str, str, str]:
        return (tr("退出应用"), tr("折叠为桌宠"), tr("最大化 / 还原"))

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hover = -1
        self._pressed = -1
        self.setMouseTracking(True)
        d = self._RADIUS * 2
        self.setFixedSize(int(d * 3 + self._GAP * 2) + 2, int(d) + 2)
        # 悬停提示：延迟显示，hover 变更/离开时取消
        self._tip_text = ""
        self._tip_pos = QPointF()
        self._tip_timer = QTimer(self)
        self._tip_timer.setSingleShot(True)
        self._tip_timer.setInterval(self._TIP_DELAY_MS)
        self._tip_timer.timeout.connect(self._show_hover_tip)

    # ── 几何 ──────────────────────────────────────────────

    def _circle_rect(self, index: int) -> QRectF:
        d = self._RADIUS * 2
        return QRectF(1 + index * (d + self._GAP), 1, d, d)

    def _index_at(self, pos: QPointF) -> int:
        for i in range(3):
            if self._circle_rect(i).contains(pos):
                return i
        return -1

    # ── 绘制 ──────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        for i in range(3):
            r = self._circle_rect(i)
            rgb = self._FILL[i]
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(*rgb) if self._hover == i
                             else QColor(*rgb, 230))
            painter.drawEllipse(r)
            if self._hover == i:
                self._paint_symbol(painter, i, r.center())
        painter.end()

    def _paint_symbol(self, painter: QPainter, index: int,
                      center: QPointF) -> None:
        # 注意：setBrush(Qt.NoPen) 在 PySide6 6.10 下会抛 ValueError，
        # paintEvent 内异常会泄漏 painter 并损坏 backing store（闪退），
        # 画线只用画笔，画刷用 NoBrush 置空即可
        pen = QPen(self._SYMBOL, 1.3)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        if index == 0:      # 关闭 ✕
            painter.drawLine(center + QPointF(-2.2, -2.2),
                             center + QPointF(2.2, 2.2))
            painter.drawLine(center + QPointF(-2.2, 2.2),
                             center + QPointF(2.2, -2.2))
        elif index == 1:    # 最小化 −
            painter.drawLine(center + QPointF(-3.2, 0),
                             center + QPointF(3.2, 0))
        else:               # 最大化 ⤢（对角双箭头）
            a = center + QPointF(-2.6, 2.6)
            b = center + QPointF(2.6, -2.6)
            painter.drawLine(a, b)
            painter.drawLine(b, b + QPointF(-3.0, 0.0))
            painter.drawLine(b, b + QPointF(0.0, 3.0))
            painter.drawLine(a, a + QPointF(3.0, 0.0))
            painter.drawLine(a, a + QPointF(0.0, -3.0))

    # ── 交互 ──────────────────────────────────────────────

    def _show_hover_tip(self) -> None:
        """延迟到点且仍悬停同一键：显示该键的功能提示"""
        if self._hover >= 0 and self._tip_text:
            pos = self.mapToGlobal(self._tip_pos.toPoint())
            QToolTip.showText(pos, self._tip_text, self)

    def reapply_texts(self) -> None:
        """语言切换：悬停中提示文本取当前语言"""
        if self._hover >= 0:
            self._tip_text = self._tip_texts()[self._hover]

    def mouseMoveEvent(self, event) -> None:
        idx = self._index_at(event.position())
        if idx != self._hover:
            self._hover = idx
            self.update()
            self._tip_timer.stop()
            if idx < 0:
                QToolTip.hideText()
            else:
                self._tip_text = self._tip_texts()[idx]
                self._tip_pos = event.position()
                self._tip_timer.start()

    def leaveEvent(self, event) -> None:
        if self._hover != -1:
            self._hover = -1
            self.update()
        self._tip_timer.stop()
        QToolTip.hideText()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        # 对齐原生红绿灯：release 仍在同一键上才触发，按住拖出可反悔
        self._pressed = self._index_at(event.position())

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        idx = self._index_at(event.position())
        if idx != -1 and idx == self._pressed:
            if idx == 0:
                self.signal_close.emit()
            elif idx == 1:
                self.signal_minimize.emit()
            else:
                self.signal_zoom.emit()
        self._pressed = -1

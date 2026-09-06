"""
系统托盘服务：图标绘制、右键菜单、气泡通知
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from app.config import AppConfig


class TrayService(QObject):
    """系统托盘：图标、菜单、气泡通知"""

    signal_show_requested = Signal()
    signal_hide_requested = Signal()
    signal_quit_requested = Signal()
    signal_always_top_toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._tray = QSystemTrayIcon(parent)
        self._tray.setIcon(self._create_icon())
        self._tray.setToolTip(AppConfig.APP_NAME)

        self._menu = QMenu()
        menu = self._menu
        self._toggle_action = QAction("隐藏", menu)
        self._toggle_action.triggered.connect(self._on_toggle)
        menu.addAction(self._toggle_action)
        self._always_top_action = QAction("窗口置顶", menu)
        self._always_top_action.setCheckable(True)
        self._always_top_action.setChecked(True)
        self._always_top_action.toggled.connect(
            self.signal_always_top_toggled.emit)
        menu.addAction(self._always_top_action)
        menu.addSeparator()
        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self.signal_quit_requested)
        menu.addAction(quit_action)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_activated)
        self._tray.show()

    def set_always_top_checked(self, on: bool) -> None:
        """由控制器同步勾选态（blockSignals 防止 toggled 回环）"""
        if self._always_top_action.isChecked() != on:
            self._always_top_action.blockSignals(True)
            self._always_top_action.setChecked(on)
            self._always_top_action.blockSignals(False)

    def set_window_visible(self, visible: bool) -> None:
        """窗口显隐变化后同步菜单文案（显示 ↔ 隐藏）"""
        self._toggle_action.setText("隐藏" if visible else "显示")

    def _on_toggle(self) -> None:
        if self._toggle_action.text() == "隐藏":
            self.signal_hide_requested.emit()
        else:
            self.signal_show_requested.emit()

    def show_notification(self, message: str) -> None:
        if self._tray.supportsMessages():
            self._tray.showMessage(
                AppConfig.APP_NAME,
                message,
                QSystemTrayIcon.Information,
                AppConfig.NOTIFICATION_DURATION_MS,
            )

    def hide(self) -> None:
        self._tray.hide()

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            if self._tray.isVisible() and self.parent().isVisible():
                self.signal_hide_requested.emit()
            else:
                self.signal_show_requested.emit()

    @staticmethod
    def _create_icon() -> QIcon:
        """托盘图标：渐变圆角方块上的看板小卡图形"""
        icon = QIcon()

        for size in (48, 32, 24, 16):
            pixmap = QPixmap(size, size)
            pixmap.fill(QColor(0, 0, 0, 0))
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)

            s = size
            m = max(1, s // 16)
            inner = s - 2 * m

            # 背景圆角方块（蓝紫渐变感：主色 + 顶部高光）
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#2F6BFF"))
            painter.drawRoundedRect(m, m, inner, inner, inner // 4, inner // 4)
            painter.setBrush(QColor(255, 255, 255, 42))
            painter.drawRoundedRect(m, m, inner, inner // 3, inner // 4, inner // 4)

            if s >= 24:
                # 三张看板小卡（错落排列）
                card_w = max(3, s // 7)
                card_h = max(4, s // 3)
                y0 = s * 0.30
                for i, x0 in enumerate(
                        (s * 0.20, s * 0.40, s * 0.60)):
                    painter.setBrush(QColor(255, 255, 255, 235))
                    painter.drawRoundedRect(
                        int(x0), int(y0 + (i % 2) * s * 0.06),
                        card_w, card_h, 1, 1)
            else:
                # 16px：单张白卡
                painter.setBrush(QColor(255, 255, 255, 235))
                painter.drawRoundedRect(
                    int(s * 0.28), int(s * 0.30),
                    int(s * 0.44), int(s * 0.44), 2, 2)

            painter.end()
            icon.addPixmap(pixmap)

        return icon

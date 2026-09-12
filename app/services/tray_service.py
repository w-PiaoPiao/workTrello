"""
系统托盘服务：图标绘制、右键菜单、气泡通知
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from app.config import AppConfig
from app.i18n import app_display_name, tr
from app.services.app_icon import create_app_icon


class TrayService(QObject):
    """系统托盘：图标、菜单、气泡通知"""

    signal_show_requested = Signal()
    signal_hide_requested = Signal()
    signal_quit_requested = Signal()
    signal_always_top_toggled = Signal(bool)
    signal_undo_requested = Signal()
    signal_settings_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self._tray = QSystemTrayIcon(parent)
        self._tray.setIcon(create_app_icon())
        self._tray.setToolTip(app_display_name())

        self._menu = QMenu()
        menu = self._menu
        self._toggle_action = QAction(tr("隐藏"), menu)
        self._toggle_action.triggered.connect(self._on_toggle)
        menu.addAction(self._toggle_action)
        self._always_top_action = QAction(tr("窗口置顶"), menu)
        self._always_top_action.setCheckable(True)
        self._always_top_action.setChecked(True)
        self._always_top_action.toggled.connect(
            self.signal_always_top_toggled.emit)
        menu.addAction(self._always_top_action)
        menu.addSeparator()
        self._undo_action = QAction(tr("撤销"), menu)
        self._undo_action.triggered.connect(self.signal_undo_requested.emit)
        menu.addAction(self._undo_action)
        self._settings_action = QAction(tr("设置…"), menu)
        self._settings_action.triggered.connect(
            self.signal_settings_requested.emit)
        menu.addAction(self._settings_action)
        self._quit_action = QAction(tr("退出"), menu)
        self._quit_action.triggered.connect(self.signal_quit_requested)
        menu.addAction(self._quit_action)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_activated)
        self._tray.show()

    def set_always_top_checked(self, on: bool) -> None:
        """由控制器同步勾选态（blockSignals 防止 toggled 回环）"""
        if self._always_top_action.isChecked() != on:
            self._always_top_action.blockSignals(True)
            self._always_top_action.setChecked(on)
            self._always_top_action.blockSignals(False)

    def set_tooltip(self, text: str) -> None:
        """更新托盘悬浮提示（番茄钟倒计时用）"""
        self._tray.setToolTip(text)

    def reapply_texts(self) -> None:
        """语言切换后刷新菜单文案（显隐项按当前窗口状态取词）"""
        visible = self.parent().isVisible() if self.parent() else True
        self._toggle_action.setText(tr("隐藏") if visible else tr("显示"))
        self._always_top_action.setText(tr("窗口置顶"))
        self._undo_action.setText(tr("撤销"))
        self._settings_action.setText(tr("设置…"))
        self._quit_action.setText(tr("退出"))

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
                app_display_name(),
                message,
                QSystemTrayIcon.Information,
                AppConfig.NOTIFICATION_DURATION_MS,
            )

    def hide(self) -> None:
        self._tray.hide()

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger and not AppConfig.IS_MACOS:
            # Windows/Linux 惯例：左键单击即切换显隐（macOS 单击是
            # 系统菜单，动了会与菜单弹出打架，保持双击切换）
            self._on_toggle()
            return
        if reason == QSystemTrayIcon.DoubleClick:
            if self._tray.isVisible() and self.parent().isVisible():
                self.signal_hide_requested.emit()
            else:
                self.signal_show_requested.emit()


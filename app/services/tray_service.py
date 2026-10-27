"""
系统托盘服务：图标绘制、右键菜单、气泡通知
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from app.config import AppConfig
from app.i18n import app_display_name, tr
from app.platform.win_integrity import at_least_medium
from app.services.app_icon import create_app_icon

logger = logging.getLogger(__name__)

# 进程能否真的把图标注册进通知区域（一次查询，结果进程内不变）
_icon_registrable: bool | None = None


def tray_icon_registrable() -> bool:
    """本进程注册托盘图标是否会成功

    Windows 的 UIPI 会拦掉低完整性进程对任务栏窗口的调用，
    `Shell_NotifyIcon` 随之失败——但 Qt 的 `isVisible()` 只反映
    "我要显示"，照样返回 True。低完整性下（沙箱、受限令牌）把窗口
    藏进托盘，等于藏进一个不存在的图标里。
    """
    global _icon_registrable
    if _icon_registrable is None:
        _icon_registrable = at_least_medium()
    return _icon_registrable


class TrayService(QObject):
    """系统托盘：图标、菜单、气泡通知"""

    signal_show_requested = Signal()
    signal_hide_requested = Signal()
    signal_quit_requested = Signal()
    signal_always_top_toggled = Signal(bool)
    signal_undo_requested = Signal()
    signal_settings_requested = Signal()
    signal_snooze_requested = Signal()      # 稍后提醒（推迟截止提醒）
    signal_notification_clicked = Signal()  # 气泡被点击

    def __init__(self, parent=None):
        super().__init__(parent)
        # 显隐状态标志：切显隐不能靠菜单文案比对——英文界面下文案是
        # "Hide"，与中文比对必然失败，托盘显隐切换会整体失效
        self._window_visible = True

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
        # 稍后提醒：截止提醒只弹一次气泡，错过就没了——给出"再等一会儿"
        # 的入口，而不是让用户自己盯着时间
        self._snooze_action = QAction(
            tr("稍后提醒 {n} 分钟").format(n=AppConfig.REMIND_SNOOZE_MINUTES),
            menu)
        self._snooze_action.triggered.connect(
            self.signal_snooze_requested.emit)
        menu.addAction(self._snooze_action)
        self._settings_action = QAction(tr("设置…"), menu)
        self._settings_action.triggered.connect(
            self.signal_settings_requested.emit)
        menu.addAction(self._settings_action)
        self._quit_action = QAction(tr("退出"), menu)
        self._quit_action.triggered.connect(self.signal_quit_requested)
        menu.addAction(self._quit_action)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_activated)
        # 气泡被点击：截止提醒时用来跳到对应卡片（无卡片上下文时无害）
        self._tray.messageClicked.connect(
            self.signal_notification_clicked.emit)
        self._tray.show()
        if not tray_icon_registrable():
            # 只记日志不弹窗：这是环境限制，不是用户操作错误。留在日志里
            # 是为了下次"图标怎么不见了"能一眼查到原因
            logger.warning(
                "进程完整性级别过低，托盘图标未能注册（把程序移出受限目录/"
                "沙箱后恢复正常）")

    def set_always_top_checked(self, on: bool) -> None:
        """由控制器同步勾选态（blockSignals 防止 toggled 回环）"""
        if self._always_top_action.isChecked() != on:
            self._always_top_action.blockSignals(True)
            self._always_top_action.setChecked(on)
            self._always_top_action.blockSignals(False)

    def set_tooltip(self, text: str) -> None:
        """更新托盘悬浮提示（番茄钟倒计时用）"""
        self._tray.setToolTip(text)

    def is_available(self) -> bool:
        """系统托盘是否真的可用

        窗口 ✕ 靠它决定"藏起来"还是"退出"：托盘不可用时把窗口藏掉，应用就
        再也叫不回来了（没有任务栏/Dock 图标），只能回退为退出。

        只问 `isSystemTrayAvailable()` + `isVisible()` 不够：低完整性
        进程注册图标会被 UIPI 拦掉，而这两个判断都还是 True。
        """
        if not tray_icon_registrable():
            return False
        return QSystemTrayIcon.isSystemTrayAvailable() and self._tray.isVisible()

    def reapply_texts(self) -> None:
        """语言切换后刷新菜单文案（显隐项按当前窗口状态取词）"""
        visible = self.parent().isVisible() if self.parent() else True
        self._window_visible = visible
        self._sync_toggle_text()
        self._always_top_action.setText(tr("窗口置顶"))
        self._undo_action.setText(tr("撤销"))
        self._snooze_action.setText(
            tr("稍后提醒 {n} 分钟").format(n=AppConfig.REMIND_SNOOZE_MINUTES))
        self._settings_action.setText(tr("设置…"))
        self._quit_action.setText(tr("退出"))
        self._tray.setToolTip(app_display_name())

    def set_window_visible(self, visible: bool) -> None:
        """窗口显隐变化后同步菜单文案（显示 ↔ 隐藏）"""
        self._window_visible = visible
        self._sync_toggle_text()

    def _sync_toggle_text(self) -> None:
        self._toggle_action.setText(
            tr("隐藏") if self._window_visible else tr("显示"))

    def _on_toggle(self) -> None:
        if self._window_visible:
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


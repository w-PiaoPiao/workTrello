"""
主题系统：浅色 / 深色双主题 + 全局 QSS

- AppTheme.colors() / label_style()：取当前主题色
- AppTheme.global_qss()：全局样式表
- AppTheme.register(callback)：主题切换时回调刷新
"""

from __future__ import annotations

import logging
import platform

from PySide6.QtCore import QObject, Signal

from app.config import AppConfig

logger = logging.getLogger(__name__)


def _default_dark() -> bool:
    """探测系统是否深色模式（Windows 注册表 / 其他平台保守返回 False）"""
    if platform.system() != "Windows":
        return False
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return value == 0
    except OSError:
        return False


class _Theme(QObject):
    """主题状态（浅色/深色）与全局样式"""

    signal_theme_applied = Signal(str)  # "light" | "dark"

    def __init__(self):
        super().__init__()
        self._mode = "light"
        try:
            if _default_dark():
                self._mode = "dark"
        except Exception:
            pass
        self._listeners: list = []

    # ── 模式 ──────────────────────────────────────────────

    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        """设置主题（light / dark / system）"""
        if mode == "system":
            mode = "dark" if _default_dark() else "light"
        if mode not in ("light", "dark"):
            mode = "light"
        if mode == self._mode:
            return
        self._mode = mode
        self.apply()

    def apply(self) -> None:
        """应用主题到 QApplication 并通知所有监听者"""
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(self.global_qss())
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                logger.exception("主题监听回调执行失败: %r", cb)
        self.signal_theme_applied.emit(self._mode)

    def register(self, callback) -> None:
        self._listeners.append(callback)

    # ── 取色 ──────────────────────────────────────────────

    def colors(self) -> dict:
        return AppConfig.DARK_COLORS if self._mode == "dark" \
            else AppConfig.COLORS

    def label_style(self, key: str) -> tuple[str, str]:
        """标签色 → (背景, 文字)，主题切换时深色下文字提亮"""
        bg, fg = AppConfig.LABEL_COLORS.get(key, ("#E5E7EB", "#374151"))
        if self._mode == "dark":
            fg = "#10131A"
        return bg, fg

    # ── 全局 QSS ──────────────────────────────────────────

    def global_qss(self) -> str:
        c = self.colors()
        return f"""
            QWidget {{
                font-family: "Microsoft YaHei UI", "PingFang SC", sans-serif;
                color: {c['text_primary']};
            }}
            QToolTip {{
                background: {c['bg_card']};
                color: {c['text_primary']};
                border: 1px solid {c['border']};
                border-radius: 6px;
                padding: 5px 8px;
            }}
            QLineEdit, QPlainTextEdit, QTextEdit {{
                background: {c['bg_card']};
                border: 1px solid {c['border']};
                border-radius: 8px;
                padding: 6px 10px;
                selection-background-color: {c['accent']};
            }}
            QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{
                border: 1.5px solid {c['accent']};
            }}
            QPushButton {{
                background: {c['bg_card']};
                border: 1px solid {c['border']};
                border-radius: 8px;
                padding: 6px 14px;
            }}
            QPushButton:hover {{ background: {c['bg_hover']}; }}
            QPushButton:pressed {{ background: {c['accent_soft']}; }}
            QMenu {{
                background: {c['bg_card']};
                border: 1px solid {c['border']};
                border-radius: 10px;
                padding: 6px;
            }}
            QMenu::item {{
                padding: 7px 22px;
                border-radius: 6px;
            }}
            QMenu::item:selected {{ background: {c['accent_soft']}; }}
            QMenu::separator {{
                height: 1px;
                background: {c['border']};
                margin: 5px 8px;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 10px;
                margin: 2px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(128, 128, 128, 0.35);
                border-radius: 4px;
                min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: rgba(128, 128, 128, 0.55);
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
            QScrollBar:horizontal {{
                background: transparent;
                height: 10px;
                margin: 2px;
            }}
            QScrollBar::handle:horizontal {{
                background: rgba(128, 128, 128, 0.35);
                border-radius: 4px;
                min-width: 30px;
            }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
                width: 0;
            }}
        """


AppTheme = _Theme()

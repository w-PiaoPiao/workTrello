"""
主题系统：浅色 / 深色双主题 + 全局 QSS

- AppTheme.colors() / label_style()：取当前主题色
- AppTheme.global_qss()：全局样式表
- AppTheme.register(callback)：主题切换时回调刷新（弱引用，见 register）
"""

from __future__ import annotations

import inspect
import logging
import weakref

from PySide6.QtGui import QColor, QPalette
from PySide6.QtCore import QObject, Signal

from app.config import AppConfig

logger = logging.getLogger(__name__)


def _default_dark() -> bool:
    """探测系统是否深色模式（Windows 注册表 / macOS 系统外观 / 其他平台 False）"""
    if AppConfig.IS_WINDOWS:
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return value == 0
        except OSError:
            return False
    if AppConfig.IS_MACOS:
        try:
            from PySide6.QtCore import Qt
            from PySide6.QtGui import QGuiApplication
            if QGuiApplication.instance() is None:
                return False
            return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
        except Exception:
            return False
    return False


class _StrongRef:
    """非绑定回调（函数/lambda）的强引用包装

    这类回调没有宿主，弱引用会立刻失效，只能强引用；绑定方法走
    WeakMethod（见 register）。
    """

    def __init__(self, cb):
        self._cb = cb

    def __call__(self):
        return self._cb


def _qt_alive(obj) -> bool:
    """QObject 包装对象是否仍然有效（C++ 侧未析构）"""
    try:
        import shiboken6
        return shiboken6.isValid(obj)
    except Exception:   # noqa: BLE001 — 判定失败时按"存活"处理，不影响回调
        return True


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
        # 是否已把调色板/QSS 下发到 QApplication。没有它时 set_mode 会被
        # "模式未变"短路掉，而首次启动恰好是"未变"（浅色→浅色），全局
        # 样式就永远不下发——此前靠 MainWindow 复制一份窗口级 QSS 兜底，
        # 那份副本又会在切主题时盖住已更新的 app 级规则。
        self._applied = False

    # ── 模式 ──────────────────────────────────────────────

    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        """设置主题（light / dark / system）"""
        if mode == "system":
            mode = "dark" if _default_dark() else "light"
        if mode not in ("light", "dark"):
            mode = "light"
        # 模式相同仅在"确实已下发过"时才算无事可做：首次调用必须真正
        # apply()，否则全局 QSS 永远是空的
        if mode == self._mode and self._applied:
            return
        self._mode = mode
        self.apply()

    def apply(self) -> None:
        """应用主题到 QApplication 并通知所有监听者"""
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            # 调色板先行：未走样式表的控件（日期框/勾选框指示器等）
            # 由系统调色板驱动，不设置会跟随系统深色、与浅色主题错乱
            app.setPalette(self._build_palette())
            app.setStyleSheet(self.global_qss())
        self._sync_native_appearance()
        self._applied = True
        for cb in self._live_listeners():
            try:
                cb()
            except Exception:
                logger.exception("主题监听回调执行失败: %r", cb)
        self.signal_theme_applied.emit(self._mode)

    def _build_palette(self) -> QPalette:
        """构建与主题一致的调色板"""
        c = self.colors()
        pal = QPalette()
        pal.setColor(QPalette.ColorRole.Window, QColor(c["bg_primary"]))
        pal.setColor(QPalette.ColorRole.WindowText, QColor(c["text_primary"]))
        pal.setColor(QPalette.ColorRole.Base, QColor(c["bg_card"]))
        pal.setColor(QPalette.ColorRole.AlternateBase, QColor(c["bg_hover"]))
        pal.setColor(QPalette.ColorRole.Text, QColor(c["text_primary"]))
        pal.setColor(QPalette.ColorRole.Button, QColor(c["bg_card"]))
        pal.setColor(QPalette.ColorRole.ButtonText, QColor(c["text_primary"]))
        pal.setColor(QPalette.ColorRole.Highlight, QColor(c["accent"]))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
        pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(c["bg_card"]))
        pal.setColor(QPalette.ColorRole.ToolTipText, QColor(c["text_primary"]))
        pal.setColor(QPalette.ColorRole.PlaceholderText,
                     QColor(c["text_disabled"]))
        for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText,
                     QPalette.ColorRole.WindowText):
            pal.setColor(QPalette.ColorGroup.Disabled, role,
                         QColor(c["text_disabled"]))
        return pal

    def _sync_native_appearance(self) -> None:
        """macOS：原生窗口部件（对话框标题栏/文件对话框）跟随应用主题"""
        if not AppConfig.IS_MACOS:
            return
        try:
            from app.platform.mac_activation import set_native_appearance
            set_native_appearance(self._mode == "dark")
        except Exception:
            logger.exception("同步原生外观失败")

    def register(self, callback) -> None:
        """注册主题回调（绑定方法按弱引用持有）

        常驻视图由各自宿主强引用；一次性对话框（日历/归档/浮窗）销毁后
        会自动从监听列表消失。此前只增不减：语言切换会 deleteLater 掉
        日历对话框，之后再切主题就对其已析构对象回调抛 RuntimeError，
        且每次"切语言 + 重开日历"都留一个僵尸监听器。
        """
        self._listeners.append(
            weakref.WeakMethod(callback) if inspect.ismethod(callback)
            else _StrongRef(callback))

    def unregister(self, callback) -> None:
        """摘除主题回调（按注册时的可调用对象比对；弱引用已覆盖常见场景）"""
        for ref in list(self._listeners):
            try:
                cb = ref()
            except Exception:   # noqa: BLE001 — 已失效的引用直接丢弃
                cb = None
            if cb is None or cb is callback or cb == callback:
                self._listeners.remove(ref)

    def _live_listeners(self) -> list:
        """仍然有效的回调；顺带剔除已失效项（宿主对象或 C++ 侧已销毁）"""
        alive, live = [], []
        for ref in self._listeners:
            cb = ref()
            if cb is None:
                continue
            owner = getattr(cb, "__self__", None)
            if owner is not None and not _qt_alive(owner):
                continue    # 对话框已销毁：静默剔除，不再每次切换报错
            alive.append(ref)
            live.append(cb)
        self._listeners = alive
        return live

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
        # macOS 只用系统中文字体，避免 Qt 为不存在的字体族做别名探测
        if AppConfig.IS_MACOS:
            font_family = '"PingFang SC"'
        else:
            font_family = '"Microsoft YaHei UI", "PingFang SC", sans-serif'
        return f"""
            QWidget {{
                font-family: {font_family};
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
            QDateEdit, QTimeEdit, QDateTimeEdit, QSpinBox {{
                background: {c['bg_card']};
                color: {c['text_primary']};
                border: 1px solid {c['border']};
                border-radius: 8px;
                padding: 4px 8px;
                selection-background-color: {c['accent']};
            }}
            QDateEdit::drop-down, QDateTimeEdit::drop-down {{
                border: none;
                width: 18px;
            }}
            QCheckBox {{
                color: {c['text_primary']};
                spacing: 6px;
            }}
            QCheckBox:disabled {{ color: {c['text_disabled']}; }}
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
                background: {c['scroll_handle']};
                border-radius: 4px;
                min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {c['scroll_handle_hover']};
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
                background: {c['scroll_handle']};
                border-radius: 4px;
                min-width: 30px;
            }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
                width: 0;
            }}
        """


AppTheme = _Theme()

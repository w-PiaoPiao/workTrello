"""
主题系统：浅色 / 深色双主题 + 全局 QSS

- AppTheme.colors() / label_style()：取当前主题色
- AppTheme.global_qss()：全局样式表
- AppTheme.register(callback)：主题切换时回调刷新（弱引用，见 register）
"""

from __future__ import annotations

import inspect
import logging
import re
import weakref

from PySide6.QtGui import QColor, QPalette
from PySide6.QtCore import QObject, Qt, Signal

from app.config import AppConfig
from app.views import tokens

logger = logging.getLogger(__name__)

# css rgb()/rgba() 解析（QColor 只认 #hex 与命名色，见 to_qcolor）
_CSS_COLOR_RE = re.compile(
    r"^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*"
    r"(?:,\s*([0-9.]+)\s*)?\)$")


def to_qcolor(spec) -> QColor:
    """主题色字符串 → QColor：支持 #hex 与 QSS 同款 css rgb()/rgba()

    色表里的半透明色（accent_soft / mask / glass / bg_panel 等）是 QSS
    才支持的 rgba() 语法，QColor 直接构造会得到**无效色**，Qt 把无效色
    画成纯黑——分段控件选中高亮块、开关关闭态轨道曾因此整块发黑。自绘
    代码取主题色一律经由本函数；解析失败按全透明兜底并记日志。
    """
    if isinstance(spec, QColor):
        return QColor(spec)
    text = str(spec).strip()
    m = _CSS_COLOR_RE.match(text)
    if m:
        r, g, b = (int(m.group(i)) for i in (1, 2, 3))
        alpha = float(m.group(4)) if m.group(4) else 1.0
        return QColor(r, g, b, max(0, min(255, round(alpha * 255))))
    color = QColor(text)
    if not color.isValid():
        logger.warning("无法解析主题色 %r，按全透明处理", text)
        color = QColor(Qt.transparent)
    return color


def primary_button_style(c: dict) -> str:
    """主操作按钮（accent 实心）统一样式：卡片 / 快速添加 / 设置 / 快捷键
    对话框共用同一份 token 化规则，改圆角/内边距只动 config 一处

    文字色取 `accent_on` 而不是写死白色：深色主题的 accent 是亮蓝，
    白字压上去只有 3.23:1——每个对话框的主按钮都不达标。浅色仍是白色。
    """
    return f"""
        QPushButton {{
            background: {c['accent']};
            color: {c['accent_on']};
            border: 1.5px solid transparent;
            border-radius: {tokens.radius('control')}px;
            {tokens.pad_preset(AppConfig.UI_PAD_PRIMARY)};
            font-weight: bold;
        }}
        QPushButton:hover {{ background: {c['accent_hover']}; }}
        QPushButton:pressed {{ background: {c['accent_hover']}; }}
        QPushButton:focus {{ border: 1.5px solid {c['focus_ring']}; }}
        QPushButton:disabled {{
            background: {c['mask']};
            color: {c['text_disabled']};
        }}
    """


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
        # 占位文字走 text_muted 而不是 text_disabled：占位符是**说明性文字**
        # 不是禁用控件，借用禁用色让搜索框占位实测只有 1.58:1（浅）/1.90:1（深），
        # 界面上最主要的筛选入口反而最读不清。
        pal.setColor(QPalette.ColorRole.PlaceholderText,
                     QColor(c["text_muted"]))
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
        """标签色 → (背景, 文字)

        深色走独立的 DARK_LABEL_COLORS。此前深色复用浅色那份 pastel 底、
        只把文字统一压成 #10131A：对比度够，但六种色的选中边框全变成同一个
        近黑色（选中态只剩填充一个载体），且六枚亮色块成了深色界面上最亮的
        物体——标签比卡片标题还抢眼，层级是反的。
        """
        table = (AppConfig.DARK_LABEL_COLORS if self._mode == "dark"
                 else AppConfig.LABEL_COLORS)
        return table.get(key, ("#E5E7EB", "#374151"))

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
                border-radius: {tokens.radius('chip')}px;
                {tokens.pad(5, 8)};
            }}
            QLineEdit, QPlainTextEdit, QTextEdit {{
                background: {c['bg_card']};
                border: 1px solid {c['border']};
                border-radius: {tokens.radius('control')}px;
                {tokens.pad_preset(AppConfig.UI_PAD_INPUT)};
                selection-background-color: {c['accent']};
            }}
            QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{
                border: 1.5px solid {c['focus_ring']};
            }}
            QDateEdit, QTimeEdit, QDateTimeEdit, QSpinBox {{
                background: {c['bg_card']};
                color: {c['text_primary']};
                border: 1px solid {c['border']};
                border-radius: {tokens.radius('control')}px;
                {tokens.pad_preset(AppConfig.UI_PAD_INPUT_DENSE)};
                selection-background-color: {c['accent']};
            }}
            /* 禁用态必须显式写：样式表一旦设了背景/边框，Qt 就不再套用调色板的
               禁用变暗，于是"不修改"状态的日期框看起来完全可用，点了没反应也
               没有解释（批量编辑对话框正是靠 setEnabled 灰掉字段的）。
               此前全项目只有归档对话框一处写了 :disabled，这是把它变成默认。 */
            QDateEdit:disabled, QTimeEdit:disabled, QDateTimeEdit:disabled,
            QSpinBox:disabled, QComboBox:disabled {{
                color: {c['text_disabled']};
            }}
            QDateEdit::drop-down, QDateTimeEdit::drop-down {{
                border: none;
                width: 18px;
            }}
            QComboBox {{
                background: {c['bg_card']};
                color: {c['text_primary']};
                border: 1px solid {c['border']};
                border-radius: {tokens.radius('control')}px;
                {tokens.pad(5, 10)};
            }}
            QComboBox:focus {{ border: 1.5px solid {c['focus_ring']}; }}
            QComboBox::drop-down {{
                border: none;
                width: 20px;
            }}
            QComboBox QAbstractItemView {{
                background: {c['bg_card']};
                color: {c['text_primary']};
                border: 1px solid {c['border']};
                border-radius: {tokens.radius('chip')}px;
                selection-background-color: {c['accent_soft']};
                selection-color: {c['text_primary']};
                outline: none;
            }}
            QCheckBox {{
                color: {c['text_primary']};
                spacing: 6px;
            }}
            QCheckBox:disabled {{ color: {c['text_disabled']}; }}
            QPushButton {{
                background: {c['bg_card']};
                border: 1px solid {c['border']};
                border-radius: {tokens.radius('control')}px;
                {tokens.pad_preset(AppConfig.UI_PAD_SECONDARY)};
            }}
            QPushButton:hover {{ background: {c['bg_hover']}; }}
            QPushButton:pressed {{ background: {c['accent_soft']}; }}
            /* 焦点环：这是键盘可用性的地基。样式表一旦给按钮设了背景/边框，
               Qt 就不再画原生焦点矩形——全项目原来只有 4 条 :focus 规则
               （都是文本框），于是 Tab 过按钮、对话框按钮行、标签色块时
               完全看不出焦点在哪，"键盘可操作"等于形同虚设。 */
            QPushButton:focus, QCheckBox:focus {{
                border: 1.5px solid {c['focus_ring']};
            }}
            /* 禁用态：同上，样式表会吃掉调色板的变暗，必须显式写 */
            QPushButton:disabled {{
                color: {c['text_disabled']};
                background: {c['mask']};
                border-color: transparent;
            }}
            QMenu {{
                background: {c['bg_card']};
                border: 1px solid {c['border']};
                border-radius: {tokens.radius('card')}px;
                padding: {tokens.space(6)}px;
            }}
            QMenu::item {{
                {tokens.pad(7, 22)};
                border-radius: {tokens.radius('chip')}px;
            }}
            QMenu::item:selected {{ background: {c['accent_soft']}; }}
            QMenu::separator {{
                height: 1px;
                background: {c['border']};
                {tokens.pad(5, 8)};
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 10px;
                margin: 2px;
            }}
            QScrollBar::handle:vertical {{
                background: {c['scroll_handle']};
                border-radius: {tokens.circle_radius(10)}px;
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
                border-radius: {tokens.circle_radius(10)}px;
                min-width: 30px;
            }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
                width: 0;
            }}
        """


AppTheme = _Theme()

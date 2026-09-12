"""设置界面：外观 / 语言 / 桌宠 / 窗口 / 数据 / 关于

布局参考 GoGauge 的设置页（浅灰底 + 白色圆角分区卡片，行内
"标题+说明"在左、控件在右），控件沿用本项目 accent 选择按钮语言。
所有设置项**即时生效**，无保存按钮；对话框由控制器持久持有（单例），
主题回调由控制器转发 reapply_theme，语言回调经 i18n.register 自动刷新。

信号只报告"用户改了什么"，持久化与副作用由控制器统一处理（与
视图层不直接写盘的项目约定一致）。
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QUrl, Qt, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.config import AppConfig
from app.i18n import skin_display, tr
from app.views.controls import SegmentedControl, ToggleSwitch
from app.views.theme import AppTheme

_ABOUT_LINE = "桌宠形态的轻量任务看板：今日聚焦、番茄钟、归档与导出。"


def _skin_swatch(key: str) -> QIcon:
    """皮肤色板图标：body 渐变圆 + 描边（取自 PET_SKINS 调色板）"""
    spec = AppConfig.PET_SKINS.get(key, AppConfig.PET_SKINS["milk"])
    pm = QPixmap(28, 28)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    grad = QLinearGradient(0, 0, 0, 28)
    grad.setColorAt(0, QColor(*spec["body_top"]))
    grad.setColorAt(1, QColor(*spec["body_bottom"]))
    painter.setPen(QPen(QColor(*spec["outline"]), 2))
    painter.setBrush(grad)
    painter.drawEllipse(QRectF(3, 3, 22, 22))
    # 耳内点缀色小圆（识别度）
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(*spec["ear_inner"]))
    painter.drawEllipse(QRectF(19, 6, 6, 6))
    painter.end()
    return QIcon(pm)


class _Section(QFrame):
    """白色圆角分区卡片：节标题 + 若干设置行"""

    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("settingsCard")
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(16, 12, 16, 14)
        self._lay.setSpacing(10)
        self._title = QLabel(title)
        self._title.setObjectName("sectionTitle")
        self._lay.addWidget(self._title)

    def add_row(self, title: str, hint: str, widget) -> tuple[QLabel, QLabel]:
        """加一行：左侧(标题+说明)，右侧控件（垂直居中）"""
        row = QHBoxLayout()
        row.setSpacing(12)
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        t = QLabel(title)
        t.setObjectName("rowTitle")
        t.setWordWrap(True)
        h = QLabel(hint)
        h.setObjectName("rowHint")
        h.setWordWrap(True)
        text_col.addWidget(t)
        text_col.addWidget(h)
        row.addLayout(text_col, 1)
        row.addWidget(widget, 0, Qt.AlignVCenter)
        self._lay.addLayout(row)
        return t, h

    def retexts(self, title: str) -> None:
        self._title.setText(title)


class SettingsDialog(QDialog):
    """设置（模态，即时生效；由控制器持久持有）"""

    signal_theme_selected = Signal(str)       # light / dark / system
    signal_language_selected = Signal(str)    # zh / en
    signal_skin_selected = Signal(str)        # 皮肤 key
    signal_animation_toggled = Signal(bool)   # 动画启用
    signal_always_top_toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setModal(True)
        self.setFixedWidth(560)
        self.setMinimumHeight(500)
        self.setSizeGripEnabled(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 滚动内容区 ────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setObjectName("settingsScroll")
        host = QWidget()
        host.setObjectName("settingsHost")
        page = QVBoxLayout(host)
        page.setContentsMargins(20, 16, 20, 16)
        page.setSpacing(12)

        # 外观
        self._sec_appearance = _Section(tr("外观"))
        self._theme_seg = SegmentedControl(
            [("system", tr("跟随系统")), ("light", tr("浅色")),
             ("dark", tr("深色"))])
        self._theme_seg.changed.connect(self.signal_theme_selected.emit)
        self._t_theme, self._h_theme = self._sec_appearance.add_row(
            tr("主题"), tr("亮色 / 深色 / 跟随系统外观"), self._theme_seg)
        page.addWidget(self._sec_appearance)

        # 语言 / Language
        self._sec_language = _Section(tr("语言 / Language"))
        self._lang_seg = SegmentedControl([("zh", "中文"), ("en", "English")])
        self._lang_seg.changed.connect(self.signal_language_selected.emit)
        self._t_lang, self._h_lang = self._sec_language.add_row(
            tr("界面语言"), tr("切换后立即生效"), self._lang_seg)
        page.addWidget(self._sec_language)

        # 桌宠
        self._sec_pet = _Section(tr("桌宠"))
        self._anim_toggle = ToggleSwitch()
        self._anim_toggle.toggled.connect(self.signal_animation_toggled.emit)
        self._t_anim, self._h_anim = self._sec_pet.add_row(
            tr("待机动画"), tr("漂浮、呼吸、眨眼与全部过渡动效的总开关"),
            self._anim_toggle)
        self._skin_row_title = QLabel(tr("皮肤"))
        self._skin_row_title.setObjectName("rowTitle")
        self._skin_row_hint = QLabel(tr("折叠态小家伙的配色"))
        self._skin_row_hint.setObjectName("rowHint")
        self._sec_pet._lay.addWidget(self._skin_row_title)
        self._sec_pet._lay.addWidget(self._skin_row_hint)
        skin_host = QWidget()
        skin_lay = QHBoxLayout(skin_host)
        skin_lay.setContentsMargins(0, 0, 0, 0)
        skin_lay.setSpacing(8)
        self._skin_buttons: dict[str, QPushButton] = {}
        for key in AppConfig.PET_SKINS:
            btn = QPushButton(skin_display(key))
            btn.setObjectName("skinBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setIcon(_skin_swatch(key))
            btn.clicked.connect(
                lambda _=False, k=key: self.signal_skin_selected.emit(k))
            self._skin_buttons[key] = btn
            skin_lay.addWidget(btn)
        skin_lay.addStretch(1)
        self._sec_pet._lay.addWidget(skin_host)
        page.addWidget(self._sec_pet)

        # 窗口
        self._sec_window = _Section(tr("窗口"))
        self._top_toggle = ToggleSwitch()
        self._top_toggle.toggled.connect(self.signal_always_top_toggled.emit)
        self._t_top, self._h_top = self._sec_window.add_row(
            tr("窗口置顶"), tr("桌宠与看板始终悬浮在其他窗口之上"),
            self._top_toggle)
        page.addWidget(self._sec_window)

        # 数据
        self._sec_data = _Section(tr("数据"))
        self._dir_open_btn = QPushButton(tr("打开目录"))
        self._dir_open_btn.setObjectName("ghostBtn")
        self._dir_open_btn.setCursor(Qt.PointingHandCursor)
        self._dir_open_btn.clicked.connect(self._open_data_dir)
        self._t_data, self._h_data = self._sec_data.add_row(
            tr("数据目录"), tr("看板数据与自动备份保存在"), self._dir_open_btn)
        self._dir_label = QLabel(str(AppConfig.DATA_DIR))
        self._dir_label.setObjectName("dirPath")
        self._dir_label.setWordWrap(True)
        self._sec_data._lay.addWidget(self._dir_label)
        page.addWidget(self._sec_data)

        # 关于
        self._sec_about = _Section(tr("关于"))
        self._about_label = QLabel()
        self._about_label.setObjectName("aboutLabel")
        self._about_label.setWordWrap(True)
        self._sec_about._lay.addWidget(self._about_label)
        page.addWidget(self._sec_about)

        page.addStretch(1)
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

        # 底部：完成
        footer = QFrame()
        footer.setObjectName("settingsFooter")
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(20, 10, 20, 10)
        fl.addStretch(1)
        self._done_btn = QPushButton(tr("完成"))
        self._done_btn.setObjectName("primaryBtn")
        self._done_btn.setCursor(Qt.PointingHandCursor)
        self._done_btn.clicked.connect(self.accept)
        fl.addWidget(self._done_btn)
        root.addWidget(footer)

        self.reapply_theme()
        self.retexts()

    # ── 对外 ──────────────────────────────────────────────

    def sync_from_prefs(self, theme_mode: str, lang: str, skin: str,
                        animation: bool, always_top: bool) -> None:
        """打开时把当前偏好刷进控件（偏好可能被桌宠菜单等其他入口改过）"""
        self._theme_seg.set_value(theme_mode)
        self._lang_seg.set_value(lang)
        for key, btn in self._skin_buttons.items():
            btn.setChecked(key == skin)
        self._anim_toggle.setChecked(animation)
        self._top_toggle.setChecked(always_top)

    def reapply_theme(self) -> None:
        self.setStyleSheet(self._build_qss())

    def retexts(self) -> None:
        """语言切换后刷新全部文案（控件选中值不变）"""
        self.setWindowTitle(tr("设置"))
        self._sec_appearance.retexts(tr("外观"))
        self._t_theme.setText(tr("主题"))
        self._h_theme.setText(tr("亮色 / 深色 / 跟随系统外观"))
        self._theme_seg.retexts(
            [("system", tr("跟随系统")), ("light", tr("浅色")),
             ("dark", tr("深色"))])
        self._sec_language.retexts(tr("语言 / Language"))
        self._t_lang.setText(tr("界面语言"))
        self._h_lang.setText(tr("切换后立即生效"))
        self._sec_pet.retexts(tr("桌宠"))
        self._t_anim.setText(tr("待机动画"))
        self._h_anim.setText(tr("漂浮、呼吸、眨眼与全部过渡动效的总开关"))
        self._skin_row_title.setText(tr("皮肤"))
        self._skin_row_hint.setText(tr("折叠态小家伙的配色"))
        for key, btn in self._skin_buttons.items():
            btn.setText(skin_display(key))
        self._sec_window.retexts(tr("窗口"))
        self._t_top.setText(tr("窗口置顶"))
        self._h_top.setText(tr("桌宠与看板始终悬浮在其他窗口之上"))
        self._sec_data.retexts(tr("数据"))
        self._t_data.setText(tr("数据目录"))
        self._h_data.setText(tr("看板数据与自动备份保存在"))
        self._dir_open_btn.setText(tr("打开目录"))
        self._dir_label.setText(str(AppConfig.DATA_DIR))
        self._sec_about.retexts(tr("关于"))
        self._about_label.setText(
            f"{AppConfig.APP_NAME}  v{AppConfig.APP_VERSION}\n{tr(_ABOUT_LINE)}")
        self._done_btn.setText(tr("完成"))

    # ── 内部 ──────────────────────────────────────────────

    def _open_data_dir(self) -> None:
        AppConfig.DATA_DIR.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(AppConfig.DATA_DIR)))

    @staticmethod
    def _build_qss() -> str:
        c = AppTheme.colors()
        return f"""
            QDialog {{ background: {c['bg_primary']}; }}
            QScrollArea#settingsScroll, QWidget#settingsHost {{
                background: transparent;
            }}
            QFrame#settingsCard {{
                background: {c['bg_card']};
                border: 1px solid {c['border']};
                border-radius: 12px;
            }}
            QLabel#sectionTitle {{
                color: {c['text_secondary']};
                font-size: 12px;
                font-weight: bold;
                letter-spacing: 1px;
                background: transparent;
            }}
            QLabel#rowTitle {{
                color: {c['text_primary']};
                font-size: 13px;
                font-weight: 600;
                background: transparent;
            }}
            QLabel#rowHint {{
                color: {c['text_secondary']};
                font-size: 11px;
                background: transparent;
            }}
            QLabel#dirPath {{
                color: {c['text_secondary']};
                font-size: 11px;
                background: {c['mask']};
                border-radius: 6px;
                padding: 4px 8px;
            }}
            QLabel#aboutLabel {{
                color: {c['text_secondary']};
                font-size: 12px;
                background: transparent;
            }}
            QPushButton#segmentBtn {{
                background: transparent;
                color: {c['text_secondary']};
                border: 1px solid transparent;
                border-radius: 7px;
                padding: 4px 12px;
                font-size: 12px;
            }}
            QPushButton#segmentBtn:hover {{ color: {c['text_primary']}; }}
            QPushButton#segmentBtn:checked {{
                background: {c['accent_soft']};
                color: {c['accent']};
                border: 1.5px solid {c['accent']};
                font-weight: bold;
            }}
            QPushButton#skinBtn {{
                background: {c['mask']};
                color: {c['text_primary']};
                border: 1.5px solid transparent;
                border-radius: 9px;
                padding: 5px 10px;
                font-size: 12px;
            }}
            QPushButton#skinBtn:hover {{ border: 1.5px solid {c['accent']}; }}
            QPushButton#skinBtn:checked {{
                background: {c['accent_soft']};
                color: {c['accent']};
                border: 2px solid {c['accent']};
                font-weight: bold;
            }}
            QPushButton#ghostBtn {{
                background: {c['bg_card']};
                color: {c['text_primary']};
                border: 1px solid {c['border']};
                border-radius: 8px;
                padding: 5px 12px;
                font-size: 12px;
            }}
            QPushButton#ghostBtn:hover {{ background: {c['bg_hover']}; }}
            QPushButton#primaryBtn {{
                background: {c['accent']};
                color: white;
                border: none;
                border-radius: 8px;
                padding: 7px 22px;
                font-weight: bold;
            }}
            QPushButton#primaryBtn:hover {{ background: {c['accent_hover']}; }}
            QFrame#settingsFooter {{
                background: transparent;
                border-top: 1px solid {c['border']};
            }}
        """

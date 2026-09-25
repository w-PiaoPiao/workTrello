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
    QButtonGroup,
    QComboBox,
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
    signal_pet_enabled_toggled = Signal(bool)  # 桌宠显示总开关
    signal_always_top_toggled = Signal(bool)
    signal_remind_advance_changed = Signal(int)  # 截止提前提醒天数 0~3
    signal_autostart_toggled = Signal(bool)      # 开机自启动
    signal_default_view_selected = Signal(str)   # pet / board

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
        self._pet_toggle = ToggleSwitch()
        self._pet_toggle.toggled.connect(self.signal_pet_enabled_toggled.emit)
        self._t_pet, self._h_pet = self._sec_pet.add_row(
            tr("显示桌宠"),
            tr("关闭后不再出现桌宠，收起看板即隐藏到托盘，常驻开销更低"),
            self._pet_toggle)
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
        # 皮肤是单选：必须挂互斥组。此前只是各自 checkable，点新的不会取消
        # 旧的——皮肤实际换了（偏好已存），界面却显示两个都选中。
        # 互斥组同时挡住"再点一次已选中的那个把它取消掉"，避免出现零选中。
        self._skin_group = QButtonGroup(self)
        self._skin_group.setExclusive(True)
        for key in AppConfig.PET_SKINS:
            btn = QPushButton(skin_display(key))
            btn.setObjectName("skinBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setIcon(_skin_swatch(key))
            btn.clicked.connect(
                lambda _=False, k=key: self.signal_skin_selected.emit(k))
            self._skin_group.addButton(btn)
            self._skin_buttons[key] = btn
            skin_lay.addWidget(btn)
        skin_lay.addStretch(1)
        self._sec_pet._lay.addWidget(skin_host)
        page.addWidget(self._sec_pet)

        # 窗口
        self._sec_window = _Section(tr("窗口"))
        self._view_seg = SegmentedControl(
            [("pet", tr("桌宠")), ("board", tr("展开看板"))])
        self._view_seg.changed.connect(self.signal_default_view_selected.emit)
        self._t_view, self._h_view = self._sec_window.add_row(
            tr("默认打开形态"),
            tr("启动应用或从托盘显示时，以哪种形态打开"),
            self._view_seg)
        self._top_toggle = ToggleSwitch()
        self._top_toggle.toggled.connect(self.signal_always_top_toggled.emit)
        self._t_top, self._h_top = self._sec_window.add_row(
            tr("窗口置顶"), tr("桌宠与看板始终悬浮在其他窗口之上"),
            self._top_toggle)
        self._autostart_toggle = ToggleSwitch()
        self._autostart_toggle.toggled.connect(
            self.signal_autostart_toggled.emit)
        self._t_autostart, self._h_autostart = self._sec_window.add_row(
            tr("开机自启动"), tr("登录系统后自动启动，常驻托盘"),
            self._autostart_toggle)
        page.addWidget(self._sec_window)

        # 提醒
        self._sec_remind = _Section(tr("提醒"))
        self._remind_combo = QComboBox()
        self._remind_combo.setCursor(Qt.PointingHandCursor)
        for value, label in ((0, tr("不提前（仅当天与逾期）")),
                             (1, tr("提前 1 天")), (2, tr("提前 2 天")),
                             (3, tr("提前 3 天"))):
            self._remind_combo.addItem(label, value)
        self._remind_combo.currentIndexChanged.connect(
            lambda idx: self.signal_remind_advance_changed.emit(
                int(self._remind_combo.itemData(idx) or 0)))
        self._t_remind, self._h_remind = self._sec_remind.add_row(
            tr("截止提前提醒"), tr("距离截止日还剩 N 天时也开始提醒"),
            self._remind_combo)
        page.addWidget(self._sec_remind)

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
                        animation: bool, always_top: bool,
                        remind_advance: int = 0,
                        autostart: bool = False,
                        default_view: str = "pet",
                        pet_enabled: bool = True) -> None:
        """打开时把当前偏好刷进控件（偏好可能被桌宠菜单等其他入口改过）

        全程静音：这是"把真实状态刷进界面"，不是用户按了什么。此前
        setChecked 照常 emit toggled，于是"打开设置"这个动作本身会触发
        置顶/动画等副作用——切置顶会重建主窗口的原生句柄、连带把子对话框
        （包括正在打开的这一个）一起隐藏，用户看到的就是"设置打不开"。
        静音后同一份同步逻辑可以随便跑，不再有副作用。
        """
        silent = (self._pet_toggle, self._anim_toggle, self._top_toggle,
                  self._autostart_toggle, self._remind_combo)
        prev = [w.blockSignals(True) for w in silent]
        try:
            self._theme_seg.set_value(theme_mode)
            self._lang_seg.set_value(lang)
            # 分段控件只在用户点击时发 changed，set_value 天然静音
            self._view_seg.set_value(default_view)
            self.set_skin(skin)
            self._pet_toggle.setChecked(pet_enabled)
            self._anim_toggle.setChecked(animation)
            self._top_toggle.setChecked(always_top)
            self._autostart_toggle.setChecked(autostart)
            idx = self._remind_combo.findData(int(remind_advance))
            if idx >= 0:
                self._remind_combo.setCurrentIndex(idx)
        finally:
            for w, was in zip(silent, prev):
                w.blockSignals(was)

    def set_skin(self, key: str) -> None:
        """皮肤选中态对齐真实偏好（重开设置时消除残留的多个选中）"""
        btn = self._skin_buttons.get(key)
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)

    def set_autostart(self, on: bool) -> None:
        """回写开关状态**不发信号**：这是同步/回滚，不是用户改设置

        写系统启动项失败时控制器要把开关拨回原位，若走 toggled 会再触发一次
        写入（成功一次失败一次，开关来回跳）。
        """
        prev = self._autostart_toggle.blockSignals(True)
        self._autostart_toggle.setChecked(on)
        self._autostart_toggle.blockSignals(prev)

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
        self._t_pet.setText(tr("显示桌宠"))
        self._h_pet.setText(tr("关闭后不再出现桌宠，收起看板即隐藏到托盘，常驻开销更低"))
        self._t_anim.setText(tr("待机动画"))
        self._h_anim.setText(tr("漂浮、呼吸、眨眼与全部过渡动效的总开关"))
        self._skin_row_title.setText(tr("皮肤"))
        self._skin_row_hint.setText(tr("折叠态小家伙的配色"))
        for key, btn in self._skin_buttons.items():
            btn.setText(skin_display(key))
        self._sec_window.retexts(tr("窗口"))
        self._t_view.setText(tr("默认打开形态"))
        self._h_view.setText(tr("启动应用或从托盘显示时，以哪种形态打开"))
        self._view_seg.retexts([("pet", tr("桌宠")), ("board", tr("展开看板"))])
        self._t_top.setText(tr("窗口置顶"))
        self._h_top.setText(tr("桌宠与看板始终悬浮在其他窗口之上"))
        self._t_autostart.setText(tr("开机自启动"))
        self._h_autostart.setText(tr("登录系统后自动启动，常驻托盘"))
        self._sec_remind.retexts(tr("提醒"))
        self._t_remind.setText(tr("截止提前提醒"))
        self._h_remind.setText(tr("距离截止日还剩 N 天时也开始提醒"))
        for idx, (_v, label) in enumerate(
                ((0, tr("不提前（仅当天与逾期）")), (1, tr("提前 1 天")),
                 (2, tr("提前 2 天")), (3, tr("提前 3 天")))):
            self._remind_combo.setItemText(idx, label)
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
                border-radius: {AppConfig.UI_RADIUS_PANEL}px;
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
            /* 选中态的背景/边框由 SegmentedControl 自绘（会滑动的高亮块），
               按钮只上文字色——两边都画就会叠成两层色块 */
            QPushButton#segmentBtn {{
                background: transparent;
                color: {c['text_secondary']};
                border: none;
                border-radius: {AppConfig.UI_RADIUS_PILL}px;
                padding: {AppConfig.UI_PAD_PILL};
                font-size: 12px;
            }}
            QPushButton#segmentBtn:hover {{ color: {c['text_primary']}; }}
            QPushButton#segmentBtn:checked {{
                background: transparent;
                color: {c['accent']};
                font-weight: bold;
            }}
            QPushButton#skinBtn {{
                background: {c['mask']};
                color: {c['text_primary']};
                border: 1.5px solid transparent;
                border-radius: {AppConfig.UI_RADIUS_PILL}px;
                padding: {AppConfig.UI_PAD_PILL};
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
                border-radius: {AppConfig.UI_RADIUS_CONTROL}px;
                padding: {AppConfig.UI_PAD_SECONDARY};
                font-size: 12px;
            }}
            QPushButton#ghostBtn:hover {{ background: {c['bg_hover']}; }}
            QComboBox {{
                background: {c['bg_card']};
                color: {c['text_primary']};
                border: 1px solid {c['border']};
                border-radius: 8px;
                padding: 5px 10px;
                font-size: 12px;
            }}
            QComboBox QAbstractItemView {{
                background: {c['bg_card']};
                color: {c['text_primary']};
                selection-background-color: {c['accent_soft']};
                selection-color: {c['accent']};
            }}
            QPushButton#primaryBtn {{
                background: {c['accent']};
                color: white;
                border: none;
                border-radius: {AppConfig.UI_RADIUS_CONTROL}px;
                padding: {AppConfig.UI_PAD_PRIMARY};
                font-weight: bold;
            }}
            QPushButton#primaryBtn:hover {{ background: {c['accent_hover']}; }}
            QFrame#settingsFooter {{
                background: transparent;
                border-top: 1px solid {c['border']};
            }}
        """

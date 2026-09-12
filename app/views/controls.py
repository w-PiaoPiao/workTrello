"""设置界面通用控件：滑动开关 / 分段按钮

配色实时取 AppTheme（paintEvent 内取色），样式风格与看板 accent
体系一致。控件不自行注册主题回调——由持有一个 SettingsDialog 单例
的宿主在主题回调里统一调 reapply_theme()，避免一次性对话框泄漏监听。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QAbstractButton, QButtonGroup, QFrame, QHBoxLayout, QPushButton

from app.views.theme import AppTheme


class ToggleSwitch(QAbstractButton):
    """iOS 风格滑动开关（自绘轨道 + 圆形滑块，120ms 滑动动画）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self._pos = 0.0        # 滑块位置 0..1（动画插值）
        self._anim: QVariantAnimation | None = None
        self.setFixedSize(44, 26)

    def _set_pos(self, v: float) -> None:
        self._pos = v
        self.update()

    def reapply_theme(self) -> None:
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        c = AppTheme.colors()
        track = QColor(c["accent"] if (self.isChecked() or self._pos > 0.5)
                       else c["mask_hover"])
        if not self.isEnabled():
            track = QColor(c["mask"])
        painter.setPen(Qt.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(0, 0, self.width(), self.height()),
                                self.height() / 2, self.height() / 2)
        # 滑块：左右各留 3px 边距，直径 = 高 - 6
        d = self.height() - 6
        x = 3 + self._pos * (self.width() - d - 6)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(QRectF(x, 3, d, d))
        painter.end()

    def _animate_to(self, target: float) -> None:
        if self._anim is not None:
            self._anim.stop()
        anim = QVariantAnimation(self)
        anim.setDuration(120)
        start, end = self._pos, target
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.valueChanged.connect(self._set_pos)
        anim.finished.connect(anim.deleteLater)
        self._anim = anim
        anim.start()

    def nextCheckState(self) -> None:   # 点击时走这里（覆盖默认翻转）
        self.setChecked(not self.isChecked())

    def setChecked(self, on: bool) -> None:   # type: ignore[override]
        # super() 在状态变化时自动 emit toggled，这里只负责滑动动画
        super().setChecked(on)
        self._animate_to(1.0 if on else 0.0)


class SegmentedControl(QFrame):
    """分段按钮组（单选）：胶囊容器 + checkable 按钮，选中 accent 态

    options: [(value, label), ...]；label 可后续经 retexts() 更新
    （语言切换）。样式由宿主下发（#segmentBtn 选择器）。
    """

    changed = Signal(str)   # 新选中的 value

    def __init__(self, options: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self._values = [v for v, _ in options]
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for value, label in options:
            btn = QPushButton(label)
            btn.setObjectName("segmentBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.clicked.connect(
                lambda _=False, v=value: self.changed.emit(v))
            self._group.addButton(btn)
            self._buttons[value] = btn
            lay.addWidget(btn)

    def set_value(self, value: str) -> None:
        btn = self._buttons.get(value)
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)

    def value(self) -> str | None:
        for v, btn in self._buttons.items():
            if btn.isChecked():
                return v
        return None

    def retexts(self, options: list[tuple[str, str]]) -> None:
        """语言切换后更新按钮文案（value 顺序须与构造时一致）"""
        for value, label in options:
            btn = self._buttons.get(value)
            if btn is not None:
                btn.setText(label)

    def reapply_theme(self) -> None:
        # 样式表由 SettingsDialog 统一下发（子控件继承），此处仅重绘
        self.update()

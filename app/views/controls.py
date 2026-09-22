"""设置界面通用控件：滑动开关 / 分段按钮

配色实时取 AppTheme（paintEvent 内取色），样式风格与看板 accent
体系一致。控件不自行注册主题回调——由持有一个 SettingsDialog 单例
的宿主在主题回调里统一调 reapply_theme()，避免一次性对话框泄漏监听。
"""

from __future__ import annotations

from PySide6.QtCore import (QPointF, QEasingCurve, QEvent, QRectF, Qt,
                            QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QButtonGroup, QFrame, QHBoxLayout, QPushButton

from app.config import AppConfig
from app.views import motion
from app.views.theme import AppTheme


def _lerp_rect(a: QRectF, b: QRectF, t: float) -> QRectF:
    """高亮块滑动插值（QVariantAnimation 只给进度，几何自己算）"""
    return QRectF(a.x() + (b.x() - a.x()) * t,
                  a.y() + (b.y() - a.y()) * t,
                  a.width() + (b.width() - a.width()) * t,
                  a.height() + (b.height() - a.height()) * t)


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
        if not motion.enabled():
            # "暂停动画"总开关：开关滑块同样瞬时落位（此前只有本控件漏检）
            self._set_pos(target)
            return
        anim = QVariantAnimation(self)
        anim.setDuration(120)
        anim.setEasingCurve(QEasingCurve.OutCubic)
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
    """分段按钮组（单选）：胶囊容器 + checkable 按钮 + 会滑动的选中高亮块

    options: [(value, label), ...]；label 可后续经 retexts() 更新
    （语言切换）。按钮自身样式由宿主下发（#segmentBtn 选择器）。

    **高亮块由本控件自绘**：QSS 只能给按钮贴静态样式，选中态是"原地出现
    的边框"，点另一侧时看不出选中项换了哪边（选项只有两三个字、挨在一起
    时尤其像没反应）。故按钮一律透明、只负责文字，容器在它们背后画一块
    会滑过去的药丸，切换方向一眼可见。宿主须把 #segmentBtn:checked 的
    背景与边框置空（见 settings_dialog._build_qss），否则与自绘块叠两层。

    滑动只在**用户点击**时播（点哪儿滑哪儿）；set_value 是同步真实偏好、
    布局变化是重排，都必须瞬时落位——否则"打开设置"这个动作本身就在动画。

    几何**不缓存**：静止时每次绘制现取选中按钮的 geometry，只在动画期间用
    插值。缓存过一次就错过——Qt 可能先把容器的 resize 事件发出来、之后才
    activate 布局（macOS 实测如此），在 resizeEvent 里读按钮几何拿到的是中间
    态，高亮块会偏差几个像素；而布局落位按钮本身不会惊动容器的 resizeEvent，
    没有任何时机能补上这一笔。改为监听按钮自身的 Move/Resize 触发重绘（见
    eventFilter），绘制时现取，偏差从结构上不可能发生。
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
            btn.clicked.connect(lambda _=False, v=value: self._on_clicked(v))
            btn.installEventFilter(self)   # 布局落位 → 重绘（见 eventFilter）
            self._group.addButton(btn)
            self._buttons[value] = btn
            lay.addWidget(btn)
        self._pill = QRectF()            # 仅动画期间有效：插值中的高亮块几何
        self._selected: str | None = None
        self._pill_anim: QVariantAnimation | None = None

    # ── 高亮块 ────────────────────────────────────────────

    def _on_clicked(self, value: str) -> None:
        self._move_pill(value, animate=True)
        self.changed.emit(value)

    def _paint_rect(self) -> QRectF:
        """本次绘制该画的高亮块几何

        静止时现取选中按钮的 geometry——**不缓存**：布局落位按钮的时机既不在
        容器的 resizeEvent 里、也不保证任何时点能补上，缓存值会停在中间态
        （macOS 实测偏 2px，CI 抓到）。动画期间才用插值。
        """
        if self._pill_anim is not None:
            return QRectF(self._pill)
        btn = self._buttons.get(self._selected) if self._selected else None
        return QRectF(btn.geometry()) if btn is not None else QRectF()

    def _move_pill(self, value: str, animate: bool) -> None:
        btn = self._buttons.get(value)
        if btn is None:
            return
        start = self._paint_rect()          # 此刻 _selected 仍是旧值
        self._selected = value
        target = QRectF(btn.geometry())
        if self._pill_anim is not None:
            self._pill_anim.stop()
            self._pill_anim = None
        # 无起点（布局还没跑过）/ 原地 / 非用户点击 / "暂停动画"总开关：
        # 一律瞬时落位，把几何交回"绘制时现取"
        if (start.isNull() or start == target or not animate
                or not motion.enabled()):
            self._pill = QRectF()
            self.update()
            return
        anim = QVariantAnimation(self)
        anim.setDuration(AppConfig.SEGMENT_PILL_MS)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.valueChanged.connect(
            lambda t: self._set_anim_pill(_lerp_rect(start, target, float(t))))

        def _done() -> None:
            # 终点交还给按钮自己：动画的值只是过渡，最终几何以布局为准
            self._pill_anim = None
            self._pill = QRectF()
            self.update()
            anim.deleteLater()

        anim.finished.connect(_done)
        self._pill_anim = anim
        self._pill = start          # 起播首帧就有几何，不留空窗
        anim.start()

    def _set_anim_pill(self, rect: QRectF) -> None:
        """动画推进：只改插值几何"""
        self._pill = rect
        self.update()

    def paintEvent(self, event) -> None:
        # 基类先画（宿主将来若给容器下发底色，高亮块才不会压在它下面）
        super().paintEvent(event)
        rect = self._paint_rect()
        if rect.isEmpty():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        c = AppTheme.colors()
        # 描边居中在路径上：内缩半个笔宽，否则边框会被按钮盒子裁掉一半
        painter.setPen(QPen(QColor(c["accent"]), 1.5))
        painter.setBrush(QColor(c["accent_soft"]))
        r = AppConfig.UI_RADIUS_PILL
        painter.drawRoundedRect(rect.adjusted(0.75, 0.75, -0.75, -0.75), r, r)
        painter.end()

    def eventFilter(self, obj, event) -> bool:
        """按钮被布局挪动/改尺寸 → 重绘高亮块

        容器自身的 resizeEvent 不是可靠时机（Qt 可能先发它、之后再 activate
        布局，见类注释），盯按钮自己的 Move/Resize 才对得上。
        """
        if (event.type() in (QEvent.Move, QEvent.Resize)
                and self._selected is not None):
            self.update()
        return super().eventFilter(obj, event)

    def set_value(self, value: str) -> None:
        btn = self._buttons.get(value)
        if btn is None:
            return
        if not btn.isChecked():
            btn.setChecked(True)
        self._move_pill(value, animate=False)   # 同步路径不播动画

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

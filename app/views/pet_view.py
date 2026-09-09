"""
桌宠视图（折叠模式）— 纯 QPainter 程序绘制的卡通形象

形象设计：圆滚滚的小伙伴（奶白色身体 + 腮红 + 大眼睛）
动画：
- 空闲：漂浮（offsetY）+ 呼吸（scale）+ 随机小动作（歪头/跳跃）
- 眨眼：随机间隔闭眼 120ms
- 悬停：弹跳一下
交互：单击展开、拖拽移动、右键菜单
"""

from __future__ import annotations

import random
import time

from PySide6.QtCore import (
    Property,
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QSequentialAnimationGroup,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QRadialGradient,
    QColor,
    QPen,
    QBrush,
)
from PySide6.QtWidgets import QLabel, QMenu, QSizePolicy, QVBoxLayout, QWidget

from app.config import AppConfig
from app.views.theme import AppTheme


class PetCanvas(QWidget):
    """桌宠绘制画布（自绘，支持平移/缩放/旋转属性动画 + 眨眼）"""

    def __init__(self, base_size: int, parent=None):
        super().__init__(parent)
        self._base = base_size
        self._skin_key = AppConfig.get_pet_skin()
        self._offset_y = 0.0
        self._scale = 1.0
        self._angle = 0.0
        self._blink_until = 0.0     # 眨眼截止时间戳（time.monotonic 秒）
        self._squash = 0.0          # 落地压扁量 0..1（小动作落地弹性）
        self._focus_mode = False    # 专注模式：闭眼打瞌睡
        self._mood = "happy"        # happy / sad（有逾期卡片时难过）
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        # 眨眼定时器
        self._blink_timer = QTimer(self)
        self._blink_timer.setSingleShot(True)
        self._blink_timer.timeout.connect(self._do_blink)
        self._schedule_blink()

    # ── Qt 属性（动画驱动） ─────────────────────────────────

    def _get_offset_y(self) -> float:
        return self._offset_y

    def _set_offset_y(self, value: float) -> None:
        self._offset_y = value
        self.update()

    offsetY = Property(float, _get_offset_y, _set_offset_y)

    def _get_scale(self) -> float:
        return self._scale

    def _set_scale(self, value: float) -> None:
        self._scale = value
        self.update()

    scale = Property(float, _get_scale, _set_scale)

    def _get_angle(self) -> float:
        return self._angle

    def _set_angle(self, value: float) -> None:
        self._angle = value
        self.update()

    angle = Property(float, _get_angle, _set_angle)

    def _get_squash(self) -> float:
        return self._squash

    def _set_squash(self, value: float) -> None:
        self._squash = value
        self.update()

    squash = Property(float, _get_squash, _set_squash)

    # ── 眨眼 ──────────────────────────────────────────────

    def _schedule_blink(self) -> None:
        self._blink_timer.start(random.randint(2200, 5200))

    def _do_blink(self) -> None:
        self._blink_until = time.monotonic() + 0.13
        self.update()
        QTimer.singleShot(140, self.update)
        self._schedule_blink()

    def reset_transform(self) -> None:
        self._offset_y = 0.0
        self._scale = 1.0
        self._angle = 0.0
        self._squash = 0.0
        self.update()

    def set_focus_mode(self, on: bool) -> None:
        if self._focus_mode != on:
            self._focus_mode = on
            self.update()

    def set_mood(self, mood: str) -> None:
        if self._mood != mood:
            self._mood = mood
            self.update()

    # ── 皮肤 ──────────────────────────────────────────────

    def skin(self) -> dict:
        """当前皮肤配色（非法 key 回退默认"milk"）"""
        return AppConfig.PET_SKINS.get(self._skin_key,
                                       AppConfig.PET_SKINS["milk"])

    def set_skin(self, key: str) -> None:
        if key in AppConfig.PET_SKINS and key != self._skin_key:
            self._skin_key = key
            self.update()

    # ── 绘制 ──────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        s = min(self.width(), self.height()) - 2 * AppConfig.PET_CANVAS_MARGIN
        s = max(s, 40)
        cx = self.width() / 2
        cy = self.height() / 2 + self._offset_y

        painter.translate(cx, cy)
        painter.rotate(self._angle)

        # 呼吸缩放 + 落地压扁（squash: 横向拉宽纵向压扁）
        sc = self._scale
        sq = self._squash
        painter.scale(sc * (1 + sq * 0.10), sc * (1 - sq * 0.10))

        self._draw_pet(painter, s)

        painter.end()

    def _draw_pet(self, painter: QPainter, s: float) -> None:
        """绘制卡通形象（以中心为原点，s 为基准尺寸）"""
        half = s / 2

        # ── 配色（皮肤可换，见 AppConfig.PET_SKINS） ──
        skin = self.skin()
        body_top = QColor(*skin["body_top"])
        body_bottom = QColor(*skin["body_bottom"])
        outline = QColor(*skin["outline"])
        blush = QColor(*skin["blush"])
        eye_color = QColor(*skin["eye"])
        accent = QColor(*skin["ear_inner"])
        belly = QColor(*skin["belly"])

        blinking = self._focus_mode or time.monotonic() < self._blink_until

        # ── 影子（脚下椭圆） ──
        shadow = QColor(60, 40, 20, 28)
        painter.setPen(Qt.NoPen)
        painter.setBrush(shadow)
        painter.drawEllipse(
            int(-half * 0.62), int(half * 0.80),
            int(half * 1.24), int(half * 0.26))

        # ── 耳朵（两只三角圆耳） ──
        painter.setPen(QPen(outline, max(2.0, s * 0.022)))
        painter.setBrush(QBrush(body_bottom))
        for side in (-1, 1):
            ear = QPainterPath()
            ear_x = side * half * 0.52
            ear_y = -half * 0.62
            ear.moveTo(ear_x - half * 0.16, ear_y + half * 0.18)
            ear.quadTo(
                ear_x - half * 0.02, ear_y - half * 0.42,
                ear_x + half * 0.18, ear_y + half * 0.14)
            ear.quadTo(
                ear_x, ear_y + half * 0.24,
                ear_x - half * 0.16, ear_y + half * 0.18)
            painter.drawPath(ear)
            # 耳内
            painter.setPen(Qt.NoPen)
            painter.setBrush(accent)
            inner = QPainterPath()
            inner.moveTo(ear_x - half * 0.08, ear_y + half * 0.16)
            inner.quadTo(
                ear_x + side * half * 0.02, ear_y - half * 0.24,
                ear_x + half * 0.09, ear_y + half * 0.12)
            painter.drawPath(inner)
            painter.setPen(QPen(outline, max(2.0, s * 0.022)))
            painter.setBrush(QBrush(body_bottom))

        # ── 身体（圆润胶囊形） ──
        body = QPainterPath()
        body.addRoundedRect(
            int(-half * 0.78), int(-half * 0.72),
            int(half * 1.56), int(half * 1.52),
            int(half * 0.62), int(half * 0.62))
        gradient = QRadialGradient(0, -half * 0.3, half * 1.4)
        gradient.setColorAt(0, body_top)
        gradient.setColorAt(1, body_bottom)
        painter.fillPath(body, QBrush(gradient))

        # 肚皮（浅色椭圆）
        painter.setPen(Qt.NoPen)
        painter.setBrush(belly)
        painter.drawEllipse(
            int(-half * 0.34), int(half * 0.08),
            int(half * 0.68), int(half * 0.52))

        # 身体描边
        painter.setPen(QPen(outline, max(2.2, s * 0.024)))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(body)

        # ── 腮红 ──
        painter.setPen(Qt.NoPen)
        painter.setBrush(blush)
        painter.drawEllipse(
            int(-half * 0.56), int(-half * 0.06),
            int(half * 0.20), int(half * 0.13))
        painter.drawEllipse(
            int(half * 0.36), int(-half * 0.06),
            int(half * 0.20), int(half * 0.13))

        # ── 眼睛 ──
        eye_dx = half * 0.24
        eye_y = -half * 0.18
        eye_r = half * 0.085
        if blinking:
            # 闭眼：两条弧线
            pen = QPen(eye_color, max(2.0, s * 0.028))
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            for side in (-1, 1):
                painter.drawLine(
                    int(side * eye_dx - eye_r), int(eye_y),
                    int(side * eye_dx + eye_r), int(eye_y))
        else:
            painter.setPen(Qt.NoPen)
            painter.setBrush(eye_color)
            for side in (-1, 1):
                painter.drawEllipse(
                    int(side * eye_dx - eye_r), int(eye_y - eye_r),
                    int(eye_r * 2), int(eye_r * 2))
            # 高光
            painter.setBrush(QColor(255, 255, 255, 220))
            for side in (-1, 1):
                painter.drawEllipse(
                    int(side * eye_dx - eye_r * 0.15), int(eye_y - eye_r * 0.55),
                    int(eye_r * 0.55), int(eye_r * 0.55))

        # ── 嘴巴（小 w 形；难过时下弯；今天有截止时小圆嘴+汗珠） ──
        pen = QPen(eye_color, max(1.8, s * 0.022))
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        mouth_y = half * 0.02
        w = half * 0.09
        if self._mood == "sad":
            painter.drawArc(int(-w * 1.4), int(mouth_y), int(w * 2.8),
                            int(w * 1.6), 0, 180 * 16)
        elif self._mood == "worried":
            painter.drawEllipse(
                int(-w * 0.4), int(mouth_y - w * 0.3),
                int(w * 0.9), int(w * 0.9))
            # 额头汗珠（颜色随皮肤调色板）
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(*skin["sweat"]))
            hx = int(-half * 0.42)
            hy = int(-half * 0.54)
            painter.drawEllipse(
                hx - int(half * 0.05), hy - int(half * 0.14),
                int(half * 0.12), int(half * 0.22))
        else:
            painter.drawArc(
                int(-w), int(mouth_y - w * 0.5), int(w), int(w), 180 * 16, 180 * 16)
            painter.drawArc(
                int(0), int(mouth_y - w * 0.5), int(w), int(w), 180 * 16, 180 * 16)

        # ── 脚（两个小半圆） ──
        painter.setPen(QPen(outline, max(2.0, s * 0.022)))
        painter.setBrush(QBrush(accent))
        painter.drawEllipse(
            int(-half * 0.40), int(half * 0.62), int(half * 0.30), int(half * 0.20))
        painter.drawEllipse(
            int(half * 0.10), int(half * 0.62), int(half * 0.30), int(half * 0.20))


class PetView(QWidget):
    """桌宠视图（折叠态）"""

    signal_expand_clicked = Signal()
    signal_quick_add_clicked = Signal()
    signal_today_list_clicked = Signal()   # 打开今日清单浮窗
    signal_quit_requested = Signal()
    signal_animation_toggled = Signal(bool)  # 空闲动画启用状态
    signal_always_top_toggled = Signal(bool)  # 窗口置顶开关
    signal_skin_selected = Signal(str)        # 皮肤 key（持久化由控制器负责）

    def __init__(self, parent=None):
        super().__init__(parent)

        self._pressed = False
        self._press_global = QPoint()
        self._count = 0
        self._focus_mode = False
        self._animations_enabled = True
        self._badge_override: str | None = None   # 番茄钟倒计时等临时文本
        self._celebrating = None                  # 庆祝动画组

        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self._float_delta = AppConfig.PET_FLOAT_DELTA
        base = AppConfig.PET_WIDTH - 2 * AppConfig.PET_CANVAS_MARGIN
        self._pet_canvas = PetCanvas(base, self)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._pet_canvas)

        # 计数角标（右上角）
        self._badge: QLabel | None = QLabel(self)
        self._badge.setFixedHeight(AppConfig.PET_BADGE_SIZE)
        self._badge.setAlignment(Qt.AlignCenter)
        self._badge.hide()

        # 空闲动画
        self._active_action: QSequentialAnimationGroup | None = None
        self._hover_anim: QSequentialAnimationGroup | None = None
        self._action_timer = QTimer(self)
        self._action_timer.setSingleShot(True)
        self._action_timer.timeout.connect(self._do_random_action)
        self._build_idle_animations()

        # 右键菜单
        self._context_menu = QMenu(self)
        self._act_expand = QAction("展开看板", self._context_menu)
        self._act_quick_add = QAction("快速添加卡片", self._context_menu)
        self._act_today_list = QAction("今日清单", self._context_menu)
        self._act_quit = QAction("退出", self._context_menu)
        self._act_animation = QAction("暂停动画", self._context_menu)
        self._act_animation.setCheckable(True)
        self._act_always_top = QAction("窗口置顶", self._context_menu)
        self._act_always_top.setCheckable(True)
        self._act_always_top.setChecked(True)
        self._context_menu.addAction(self._act_expand)
        self._context_menu.addAction(self._act_today_list)
        self._context_menu.addAction(self._act_quick_add)
        self._context_menu.addSeparator()
        self._context_menu.addAction(self._act_animation)
        self._context_menu.addAction(self._act_always_top)
        self._context_menu.addSeparator()
        # 换皮肤：预置配色单选（挂 QActionGroup 保证互斥）
        self._skin_actions: dict[str, QAction] = {}
        skin_menu = self._context_menu.addMenu("换皮肤")
        self._skin_group = QActionGroup(self)
        current_skin = AppConfig.get_pet_skin()
        for key, spec in AppConfig.PET_SKINS.items():
            act = QAction(spec.get("name", key), skin_menu)
            act.setCheckable(True)
            act.setChecked(key == current_skin)
            act.triggered.connect(
                lambda _=False, k=key: self._on_skin_triggered(k))
            self._skin_group.addAction(act)
            self._skin_actions[key] = act
            skin_menu.addAction(act)
        self._context_menu.addAction(self._act_quit)
        self._act_expand.triggered.connect(self.signal_expand_clicked.emit)
        self._act_quick_add.triggered.connect(self.signal_quick_add_clicked.emit)
        self._act_today_list.triggered.connect(
            self.signal_today_list_clicked.emit)
        self._act_quit.triggered.connect(self.signal_quit_requested.emit)
        # checkable 动作：点击后 checked 翻转 → toggled → 切换动画
        self._act_animation.toggled.connect(self._on_animation_toggled)
        self._act_always_top.toggled.connect(
            self.signal_always_top_toggled.emit)

        AppTheme.register(self.reapply_theme)

    # ── 空闲动画 ──────────────────────────────────────────

    def _build_idle_animations(self) -> None:
        self._float_anim = QSequentialAnimationGroup(self)
        points = (0.0, float(self._float_delta), 0.0,
                  float(-self._float_delta), 0.0)
        for i in range(len(points) - 1):
            anim = QPropertyAnimation(self._pet_canvas, b"offsetY", self)
            anim.setDuration(AppConfig.PET_FLOAT_MS)
            anim.setStartValue(points[i])
            anim.setEndValue(points[i + 1])
            anim.setEasingCurve(QEasingCurve.InOutSine)
            self._float_anim.addAnimation(anim)
        self._float_anim.setLoopCount(-1)

        grow = 1.0 + AppConfig.PET_BREATH_RATIO
        self._breath_anim = QSequentialAnimationGroup(self)
        for start, end in ((1.0, grow), (grow, 1.0)):
            anim = QPropertyAnimation(self._pet_canvas, b"scale", self)
            anim.setDuration(AppConfig.PET_BREATH_MS)
            anim.setStartValue(start)
            anim.setEndValue(end)
            anim.setEasingCurve(QEasingCurve.InOutSine)
            self._breath_anim.addAnimation(anim)
        self._breath_anim.setLoopCount(-1)

    def _make_tilt_action(self) -> QSequentialAnimationGroup:
        group = QSequentialAnimationGroup(self)
        dur1, dur2, dur3 = AppConfig.TILT_PHASE_MS
        for start, end, dur in ((0.0, 8.0, dur1), (8.0, -6.0, dur2),
                                (-6.0, 0.0, dur3)):
            anim = QPropertyAnimation(self._pet_canvas, b"angle", self)
            anim.setDuration(dur)
            anim.setStartValue(start)
            anim.setEndValue(end)
            anim.setEasingCurve(QEasingCurve.InOutSine)
            group.addAnimation(anim)
        return group

    def _make_jump_action(self) -> QSequentialAnimationGroup:
        """跳跃：起跳 → 落地压扁 → 回弹"""
        group = QSequentialAnimationGroup(self)
        up = QPropertyAnimation(self._pet_canvas, b"offsetY", self)
        up.setDuration(AppConfig.JUMP_UP_MS)
        up.setStartValue(0.0)
        up.setEndValue(float(-AppConfig.PET_JUMP_HEIGHT))
        up.setEasingCurve(QEasingCurve.OutCubic)
        down = QPropertyAnimation(self._pet_canvas, b"offsetY", self)
        down.setDuration(AppConfig.JUMP_DOWN_MS)
        down.setStartValue(float(-AppConfig.PET_JUMP_HEIGHT))
        down.setEndValue(0.0)
        down.setEasingCurve(QEasingCurve.InCubic)
        squash = QPropertyAnimation(self._pet_canvas, b"squash", self)
        squash.setDuration(AppConfig.SQUASH_MS)
        squash.setStartValue(0.0)
        squash.setEndValue(1.0)
        squash.setEasingCurve(QEasingCurve.OutCubic)
        recover = QPropertyAnimation(self._pet_canvas, b"squash", self)
        recover.setDuration(AppConfig.SQUASH_RECOVER_MS)
        recover.setStartValue(1.0)
        recover.setEndValue(0.0)
        recover.setEasingCurve(QEasingCurve.OutElastic)
        for a in (up, down, squash, recover):
            group.addAnimation(a)
        return group

    def _schedule_random_action(self) -> None:
        self._action_timer.start(random.randint(
            AppConfig.PET_IDLE_ACTION_MIN_MS, AppConfig.PET_IDLE_ACTION_MAX_MS))

    def _do_random_action(self) -> None:
        if self._active_action is not None:
            self._schedule_random_action()
            return
        if random.random() < 0.5:
            self._active_action = self._make_tilt_action()
        else:
            self._active_action = self._make_jump_action()
            self._float_anim.pause()
        self._active_action.finished.connect(self._on_action_finished)
        self._active_action.start()

    def _on_action_finished(self) -> None:
        group = self._active_action
        self._active_action = None
        if group is not None:
            group.deleteLater()
        self._float_anim.resume()
        # 仅空闲动画仍在播放时才排下一次小动作；
        # stop_idle 触发本回调时 float 已停止，不应再拉起定时器
        if self._float_anim.state() == QAbstractAnimation.Running:
            self._schedule_random_action()

    # ── 悬停反馈 ──────────────────────────────────────────

    def enterEvent(self, event: QEvent) -> None:
        if self._hover_anim is None or \
                self._hover_anim.state() != QAbstractAnimation.Running:
            self._breath_anim.pause()
            group = QSequentialAnimationGroup(self)
            for start, end, dur in ((1.0, 1.12, AppConfig.HOVER_UP_MS),
                                    (1.12, 1.0, AppConfig.HOVER_DOWN_MS)):
                anim = QPropertyAnimation(self._pet_canvas, b"scale", self)
                anim.setDuration(dur)
                anim.setStartValue(start)
                anim.setEndValue(end)
                anim.setEasingCurve(QEasingCurve.OutCubic)
                group.addAnimation(anim)
            group.finished.connect(self._on_hover_finished)
            self._hover_anim = group
            group.start()
        super().enterEvent(event)

    def _on_hover_finished(self) -> None:
        self._breath_anim.resume()
        group = self._hover_anim
        self._hover_anim = None
        if group is not None:
            group.deleteLater()

    # ── 动画开关 ──────────────────────────────────────────

    def set_animation_enabled(self, enabled: bool) -> None:
        self._animations_enabled = enabled
        self._act_animation.setChecked(not enabled)
        if not enabled:
            self.stop_idle()

    def set_always_top_checked(self, on: bool) -> None:
        """由控制器同步勾选态（blockSignals 防止 toggled 回环）"""
        if self._act_always_top.isChecked() != on:
            self._act_always_top.blockSignals(True)
            self._act_always_top.setChecked(on)
            self._act_always_top.blockSignals(False)

    def _on_animation_toggled(self, paused: bool) -> None:
        self.set_animation_enabled(not paused)
        self.signal_animation_toggled.emit(not paused)

    def start_idle(self) -> None:
        if not self._animations_enabled:
            return
        self._float_anim.start()
        self._breath_anim.start()
        self._schedule_random_action()

    def stop_idle(self) -> None:
        self._float_anim.stop()
        self._breath_anim.stop()
        self._action_timer.stop()
        if self._active_action is not None:
            self._active_action.stop()
            self._active_action = None
        if self._hover_anim is not None:
            self._hover_anim.stop()
            self._hover_anim = None
        if self._celebrating is not None:
            # 断开 finished 再停，避免回调里重新拉起待机动画
            self._celebrating.finished.disconnect(self._on_celebrate_finished)
            self._celebrating.stop()
            self._celebrating.deleteLater()
            self._celebrating = None
        self._pet_canvas.reset_transform()

    # ── 状态联动 ──────────────────────────────────────────

    def set_badge_override(self, text: str | None) -> None:
        """临时覆盖角标文本（番茄钟倒计时）；None 恢复计数显示"""
        self._badge_override = text
        self._layout_badge()

    def set_focus_mode(self, on: bool) -> None:
        """专注模式：桌宠打瞌睡（闭眼），暂停空闲小动作"""
        self._focus_mode = on
        if on:
            self.stop_idle()
        self._pet_canvas.set_focus_mode(on)

    def _on_skin_triggered(self, key: str) -> None:
        """皮肤菜单选中：切换绘制并通知控制器持久化"""
        self._pet_canvas.set_skin(key)
        self.signal_skin_selected.emit(key)

    def nudge(self) -> None:
        """提醒示意：跳一下（专注/动画禁用/已有小动作时跳过；
        空闲动画运行中会先暂停、结束后恢复）"""
        if not self._animations_enabled or self._focus_mode:
            return
        if self._active_action is not None:
            return
        if self._float_anim.state() == QAbstractAnimation.Running:
            self._float_anim.pause()
        group = self._make_jump_action()
        self._active_action = group
        group.finished.connect(self._on_action_finished)
        group.start()

    def set_mood(self, mood: str) -> None:
        self._pet_canvas.set_mood(mood)

    def celebrate(self) -> None:
        """全部完成庆祝：连跳两次后恢复待机"""
        if not self._animations_enabled:
            return
        self.stop_idle()
        group = QSequentialAnimationGroup(self)
        group.addAnimation(self._make_jump_action())
        group.addAnimation(self._make_jump_action())
        group.finished.connect(self._on_celebrate_finished)
        self._celebrating = group
        group.start()

    def _on_celebrate_finished(self) -> None:
        group = self._celebrating
        self._celebrating = None
        if group is not None:
            group.deleteLater()
        self.start_idle()

    # ── 更新 ──────────────────────────────────────────────

    def update_count(self, count: int) -> None:
        """更新角标为当前卡片总数；0 张时隐藏角标"""
        self._count = count
        self.reapply_theme()
        self._layout_badge()

    def _layout_badge(self) -> None:
        """按当前计数/覆盖文本排版角标（尺寸未定时调用也安全）"""
        badge = self._badge
        if badge is None:
            return
        if self._badge_override is not None:
            text = self._badge_override        # 番茄钟倒计时等
        elif self._count > 0:
            text = "99+" if self._count > 99 else str(self._count)
        else:
            badge.hide()
            return
        badge.setText(text)
        badge.adjustSize()
        width = max(badge.sizeHint().width(), AppConfig.PET_BADGE_SIZE)
        badge.setFixedWidth(width)
        badge.move(self.width() - width - 6, 6)
        badge.show()
        badge.raise_()

    def resizeEvent(self, event) -> None:
        """首次布局/尺寸变化后重定位角标（早期 update_count 时宽度可能为 0）"""
        super().resizeEvent(event)
        if self._badge is not None:
            self._layout_badge()

    # ── 鼠标交互 ──────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._pressed = True
            self._forward_to_window(event)
            event.accept()
        elif event.button() == Qt.RightButton:
            self._context_menu.exec(event.globalPosition().toPoint())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._pressed and event.buttons() == Qt.LeftButton:
            moved = (event.globalPosition().toPoint()
                     - self._press_global).manhattanLength()
            if moved > AppConfig.PET_CLICK_THRESHOLD:
                self._forward_to_window(event)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self._pressed:
            self._pressed = False
            self._forward_to_window(event)
            moved = (event.globalPosition().toPoint()
                     - self._press_global).manhattanLength()
            if moved <= AppConfig.PET_CLICK_THRESHOLD:
                self.signal_expand_clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _forward_to_window(self, event: QMouseEvent) -> None:
        window = self.window()
        if window is None or window is self:
            return
        if event.type() == QEvent.Type.MouseButtonPress:
            window.mousePressEvent(event)
        elif event.type() == QEvent.Type.MouseMove:
            window.mouseMoveEvent(event)
        elif event.type() == QEvent.Type.MouseButtonRelease:
            window.mouseReleaseEvent(event)

    # ── 主题 ──────────────────────────────────────────────

    def reapply_theme(self) -> None:
        c = AppTheme.colors()
        if self._badge is not None:
            self._badge.setStyleSheet(f"""
                QLabel {{
                    background: {c['accent']};
                    color: white;
                    border-radius: {AppConfig.PET_BADGE_SIZE // 2}px;
                    font-size: 12px;
                    font-weight: bold;
                    padding: 0 5px;
                }}
            """)
        self._context_menu.setStyleSheet(AppTheme.global_qss())

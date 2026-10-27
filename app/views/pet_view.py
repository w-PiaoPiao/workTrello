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

import math
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
from app.i18n import skin_display, tr
from app.views import tokens
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
        # 绘制资源缓存（见 _paint_assets）：按 (皮肤, 尺寸) 分档
        self._paint_assets_cache: dict = {}
        self._blink_enabled = True
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        # 眨眼定时器：显示后才开始（隐藏/暂停动画时停，见 showEvent/hideEvent），
        # 不再构造即常驻自续——桌宠隐藏到托盘后事件循环不该被持续唤醒
        self._blink_timer = QTimer(self)
        self._blink_timer.setSingleShot(True)
        self._blink_timer.timeout.connect(self._do_blink)

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

    def set_idle_pose(self, offset_y: float, scale: float) -> bool:
        """直接落定空闲姿态（不逐属性 update，由调用方合并一次重绘）

        空闲驱动每帧调用；值未变时返回 False，省掉一次重绘调度。
        """
        changed = False
        if offset_y != self._offset_y:
            self._offset_y = offset_y
            changed = True
        if scale != self._scale:
            self._scale = scale
            changed = True
        return changed

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

    # ── 可见性与眨眼启停 ──────────────────────────────────

    def set_blink_enabled(self, on: bool) -> None:
        """"暂停动画"总开关同步：关=停眨眼；开=可见时恢复"""
        self._blink_enabled = on
        if not on:
            self._blink_timer.stop()
        elif self.isVisible():
            self._schedule_blink()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._blink_enabled:
            self._schedule_blink()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._blink_timer.stop()

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
        a = self._paint_assets(s)

        blinking = self._focus_mode or time.monotonic() < self._blink_until

        # ── 影子（脚下椭圆） ──
        painter.setPen(Qt.NoPen)
        painter.setBrush(a["shadow"])
        painter.drawEllipse(
            int(-half * 0.62), int(half * 0.80),
            int(half * 1.24), int(half * 0.26))

        # ── 耳朵（两只三角圆耳） ──
        painter.setPen(a["ear_pen"])
        painter.setBrush(a["body_brush"])
        for ear, inner in a["ears"]:
            painter.drawPath(ear)
            # 耳内
            painter.setPen(Qt.NoPen)
            painter.setBrush(a["accent"])
            painter.drawPath(inner)
            painter.setPen(a["ear_pen"])
            painter.setBrush(a["body_brush"])

        # ── 身体（圆润胶囊形） ──
        painter.fillPath(a["body"], a["gradient_brush"])

        # 肚皮（浅色椭圆）
        painter.setPen(Qt.NoPen)
        painter.setBrush(a["belly_brush"])
        painter.drawEllipse(
            int(-half * 0.34), int(half * 0.08),
            int(half * 0.68), int(half * 0.52))

        # 身体描边
        painter.setPen(a["body_pen"])
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(a["body"])

        # ── 腮红 ──
        painter.setPen(Qt.NoPen)
        painter.setBrush(a["blush_brush"])
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
            painter.setPen(a["eye_pen"])
            painter.setBrush(Qt.NoBrush)
            for side in (-1, 1):
                painter.drawLine(
                    int(side * eye_dx - eye_r), int(eye_y),
                    int(side * eye_dx + eye_r), int(eye_y))
        else:
            painter.setPen(Qt.NoPen)
            painter.setBrush(a["eye_brush"])
            for side in (-1, 1):
                painter.drawEllipse(
                    int(side * eye_dx - eye_r), int(eye_y - eye_r),
                    int(eye_r * 2), int(eye_r * 2))
            # 高光
            painter.setBrush(a["highlight"])
            for side in (-1, 1):
                painter.drawEllipse(
                    int(side * eye_dx - eye_r * 0.15), int(eye_y - eye_r * 0.55),
                    int(eye_r * 0.55), int(eye_r * 0.55))

        # ── 嘴巴（小 w 形；难过时下弯；今天有截止时小圆嘴+汗珠） ──
        painter.setPen(a["mouth_pen"])
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
            painter.setBrush(a["sweat"])
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
        painter.setPen(a["foot_pen"])
        painter.setBrush(a["foot_brush"])
        painter.drawEllipse(
            int(-half * 0.40), int(half * 0.62), int(half * 0.30), int(half * 0.20))
        painter.drawEllipse(
            int(half * 0.10), int(half * 0.62), int(half * 0.30), int(half * 0.20))

    def _paint_assets(self, s: float) -> dict:
        """按 (皮肤, 尺寸) 分档缓存绘制资源

        色彩/画笔/路径/渐变在帧间完全不变，逐帧重建是纯对象 churn——
        桌宠态即闲置态，这是应用最长的稳态负载路径。换皮肤走新 key，
        resize 另起新档；档位超限整体清空（防尺寸抖动积累）。
        """
        key = (self._skin_key, int(s))
        assets = self._paint_assets_cache.get(key)
        if assets is not None:
            return assets
        skin = self.skin()
        half = s / 2

        # 耳朵与耳内路径（左右对称各一份）
        ears: list[tuple[QPainterPath, QPainterPath]] = []
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
            inner = QPainterPath()
            inner.moveTo(ear_x - half * 0.08, ear_y + half * 0.16)
            inner.quadTo(
                ear_x + side * half * 0.02, ear_y - half * 0.24,
                ear_x + half * 0.09, ear_y + half * 0.12)
            ears.append((ear, inner))

        body = QPainterPath()
        body.addRoundedRect(
            int(-half * 0.78), int(-half * 0.72),
            int(half * 1.56), int(half * 1.52),
            int(half * 0.62), int(half * 0.62))
        gradient = QRadialGradient(0, -half * 0.3, half * 1.4)
        gradient.setColorAt(0, QColor(*skin["body_top"]))
        gradient.setColorAt(1, QColor(*skin["body_bottom"]))

        eye_pen = QPen(QColor(*skin["eye"]), max(2.0, s * 0.028))
        eye_pen.setCapStyle(Qt.RoundCap)
        mouth_pen = QPen(QColor(*skin["eye"]), max(1.8, s * 0.022))
        mouth_pen.setCapStyle(Qt.RoundCap)

        assets = {
            "shadow": QColor(60, 40, 20, 28),
            "accent": QColor(*skin["ear_inner"]),
            "sweat": QColor(*skin["sweat"]),
            "ear_pen": QPen(QColor(*skin["outline"]), max(2.0, s * 0.022)),
            "body_pen": QPen(QColor(*skin["outline"]), max(2.2, s * 0.024)),
            "eye_pen": eye_pen,
            "mouth_pen": mouth_pen,
            "foot_pen": QPen(QColor(*skin["outline"]), max(2.0, s * 0.022)),
            "body_brush": QBrush(QColor(*skin["body_bottom"])),
            "belly_brush": QBrush(QColor(*skin["belly"])),
            "blush_brush": QBrush(QColor(*skin["blush"])),
            "eye_brush": QBrush(QColor(*skin["eye"])),
            "foot_brush": QBrush(QColor(*skin["ear_inner"])),
            "gradient_brush": QBrush(gradient),
            "highlight": QColor(255, 255, 255, 220),
            "ears": ears,
            "body": body,
        }
        if len(self._paint_assets_cache) >= 8:
            self._paint_assets_cache.clear()
        self._paint_assets_cache[key] = assets
        return assets


class PetView(QWidget):
    """桌宠视图（折叠态）"""

    signal_expand_clicked = Signal()
    signal_quick_add_clicked = Signal()
    signal_today_list_clicked = Signal()   # 打开今日清单浮窗
    signal_quit_requested = Signal()
    signal_animation_toggled = Signal(bool)  # 空闲动画启用状态
    signal_always_top_toggled = Signal(bool)  # 窗口置顶开关
    signal_skin_selected = Signal(str)        # 皮肤 key（持久化由控制器负责）
    signal_settings_clicked = Signal()        # 打开设置界面

    def __init__(self, parent=None):
        super().__init__(parent)

        self._pressed = False
        self._press_global = QPoint()
        self._count = 0
        self._focus_mode = False
        self._mood = "happy"      # 供 tooltip 解释表情（见 _update_tooltip）
        self._animations_enabled = True
        self._badge_override: str | None = None   # 番茄钟倒计时等临时文本
        self._celebrating = None                  # 庆祝动画组

        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        # 折叠态的应用**就是**这只小家伙，但它此前没有任何"我可以点"的暗示：
        # 唯一线索是悬停放大 12%，而那是装饰性的。补三样——
        # 手型光标（桌面应用的通用"可点"语言）、tooltip（写清左右键各做什么）、
        # 焦点策略与无障碍名（否则键盘与读屏用户完全够不着折叠态）。
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(AppConfig.APP_NAME)
        self._update_tooltip()

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
        # 漂浮 + 呼吸合并为单定时器自驱动（设计权衡见 _idle_tick）：
        # 此前是两条 60fps 的 QPropertyAnimation 常驻循环，桌宠态即闲置态，
        # 实测桌宠可见时全进程 10-17% 单核，是应用最大的稳态负载——
        # 秒级缓动降到 ~30fps 视觉无差，常驻开销直接减半；且两动画每帧
        # 各自 setter+update，合并后一帧只调度一次重绘
        self._idle_timer = QTimer(self)
        self._idle_timer.setInterval(AppConfig.PET_IDLE_FPS_TICK_MS)
        self._idle_timer.timeout.connect(self._idle_tick)
        self._float_t = 0.0         # 漂浮相位（循环毫秒）
        self._breath_t = 0.0        # 呼吸相位（循环毫秒）
        self._float_frozen = False  # 小动作/提醒跳跃期间冻结漂浮
        self._breath_frozen = False # 悬停弹跳期间冻结呼吸
        self._last_tick = 0.0

        # 右键菜单
        self._context_menu = QMenu(self)
        self._act_expand = QAction(tr("展开看板"), self._context_menu)
        self._act_quick_add = QAction(tr("快速添加卡片"), self._context_menu)
        self._act_today_list = QAction(tr("今日清单"), self._context_menu)
        self._act_quit = QAction(tr("退出"), self._context_menu)
        self._act_settings = QAction(tr("设置…"), self._context_menu)
        self._act_animation = QAction(tr("暂停动画"), self._context_menu)
        self._act_animation.setCheckable(True)
        self._act_always_top = QAction(tr("窗口置顶"), self._context_menu)
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
        skin_menu = self._context_menu.addMenu(tr("换皮肤"))
        self._skin_menu = skin_menu
        self._skin_group = QActionGroup(self)
        current_skin = AppConfig.get_pet_skin()
        for key, spec in AppConfig.PET_SKINS.items():
            act = QAction(skin_display(key), skin_menu)
            act.setCheckable(True)
            act.setChecked(key == current_skin)
            act.triggered.connect(
                lambda _=False, k=key: self._on_skin_triggered(k))
            self._skin_group.addAction(act)
            self._skin_actions[key] = act
            skin_menu.addAction(act)
        self._context_menu.addAction(self._act_settings)
        self._context_menu.addAction(self._act_quit)
        self._act_expand.triggered.connect(self.signal_expand_clicked.emit)
        self._act_quick_add.triggered.connect(self.signal_quick_add_clicked.emit)
        self._act_today_list.triggered.connect(
            self.signal_today_list_clicked.emit)
        self._act_quit.triggered.connect(self.signal_quit_requested.emit)
        self._act_settings.triggered.connect(self.signal_settings_clicked.emit)
        # checkable 动作：点击后 checked 翻转 → toggled → 切换动画
        self._act_animation.toggled.connect(self._on_animation_toggled)
        self._act_always_top.toggled.connect(
            self.signal_always_top_toggled.emit)

        AppTheme.register(self.reapply_theme)

    # ── 空闲动画（漂浮 + 呼吸，30fps 自驱动） ─────────────

    # 关键帧复刻原 QSequentialAnimationGroup 的分段 InOutSine 插值：
    # 漂浮 0→+δ→0→-δ→0 四段循环，呼吸 1→1.05→1 两段循环
    _FLOAT_KEYPOINTS = (0.0, float(AppConfig.PET_FLOAT_DELTA), 0.0,
                        float(-AppConfig.PET_FLOAT_DELTA), 0.0)
    _FLOAT_PERIOD_MS = AppConfig.PET_FLOAT_MS * (len(_FLOAT_KEYPOINTS) - 1)
    _BREATH_KEYPOINTS = (1.0, 1.0 + AppConfig.PET_BREATH_RATIO, 1.0)
    _BREATH_PERIOD_MS = AppConfig.PET_BREATH_MS * (len(_BREATH_KEYPOINTS) - 1)

    @staticmethod
    def _loop_value(t_ms: float, seg_ms: int, keypoints) -> float:
        """分段 InOutSine 循环插值（t_ms 已取模到 [0, 周期)）"""
        n = len(keypoints) - 1
        seg = min(int(t_ms // seg_ms), n - 1)
        t = (t_ms - seg * seg_ms) / seg_ms
        eased = 0.5 * (1.0 - math.cos(math.pi * t))   # Qt InOutSine
        a, b = keypoints[seg], keypoints[seg + 1]
        return a + (b - a) * eased

    def _idle_tick(self) -> None:
        """一帧同时推进漂浮与呼吸，合并为一次重绘

        漂浮/呼吸各自独立冻结（小动作冻结漂浮、悬停冻结呼吸），等价于
        原先对单条动画 pause/resume，且赋值天然幂等——不再有"对未暂停
        动画 resume"的 Qt 警告噪音。dt 钳单步上限：系统睡眠唤醒后相位
        不跳变，只是从原相位继续走。
        """
        now = time.monotonic()
        dt = min(now - self._last_tick, 0.1) * 1000.0
        self._last_tick = now
        canvas = self._pet_canvas
        y = canvas._offset_y
        s = canvas._scale
        if not self._float_frozen:
            self._float_t = (self._float_t + dt) % self._FLOAT_PERIOD_MS
            y = self._loop_value(self._float_t, AppConfig.PET_FLOAT_MS,
                                 self._FLOAT_KEYPOINTS)
        if not self._breath_frozen:
            self._breath_t = (self._breath_t + dt) % self._BREATH_PERIOD_MS
            s = self._loop_value(self._breath_t, AppConfig.PET_BREATH_MS,
                                 self._BREATH_KEYPOINTS)
        if canvas.set_idle_pose(y, s):
            canvas.update()

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
            self._float_frozen = True   # 跳跃全程由动作接管垂直位移
        self._active_action.finished.connect(self._on_action_finished)
        self._active_action.start()

    def _on_action_finished(self) -> None:
        group = self._active_action
        self._active_action = None
        if group is not None:
            group.deleteLater()
        self._float_frozen = False
        # 仅空闲动画仍在播放时才排下一次小动作；
        # stop_idle 触发本回调时 float 已停止，不应再拉起定时器
        if self._idle_timer.isActive():
            self._schedule_random_action()

    # ── 悬停反馈 ──────────────────────────────────────────

    def enterEvent(self, event: QEvent) -> None:
        # 待机动画开关关掉时不做悬停弹跳（与随机小动作/庆祝同一判据；
        # 此前只有悬停这一条漏检，"暂停动画"下仍会放大回弹）。
        # 专注/睡觉态同理：睡觉的桌宠不该被悬停"惊醒"蹦一下
        if not self._animations_enabled or self._focus_mode:
            super().enterEvent(event)
            return
        if self._hover_anim is None or \
                self._hover_anim.state() != QAbstractAnimation.Running:
            self._breath_frozen = True
            group = self._hover_anim
            if group is None:
                # 动画单实例复用（构造一次，随控件析构）：与 controls.
                # ToggleSwitch 同款教训——事件回调里临时新建动画对象，
                # 真机 cocoa 下有 PySide6 override 失效段错误前科
                group = QSequentialAnimationGroup(self)
                for start, end, dur in ((1.0, 1.12, AppConfig.HOVER_UP_MS),
                                        (1.12, 1.0, AppConfig.HOVER_DOWN_MS)):
                    anim = QPropertyAnimation(self._pet_canvas, b"scale", self)
                    anim.setDuration(dur)
                    anim.setStartValue(start)
                    anim.setEndValue(end)
                    anim.setEasingCurve(QEasingCurve.OutCubic)
                    group.addAnimation(anim)
                self._hover_anim = group
                # finished 回调随单实例动画只挂一次：放循环外会随每次
                # 悬停重复 connect，常驻进程下连接无界累积
                group.finished.connect(self._on_hover_finished)
            group.start()
        super().enterEvent(event)

    def _on_hover_finished(self) -> None:
        self._breath_frozen = False
        # 动画对象保留复用，不 deleteLater（见 enterEvent）

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

    def set_skin_checked(self, key: str) -> None:
        """同步右键菜单里皮肤的勾选态（互斥由 QActionGroup 保证）

        设置页换肤是另一个入口，菜单不跟着走的话，两个界面会各显示一个
        "当前皮肤"——与实际生效的那个都可能是错的。
        """
        act = self._skin_actions.get(key)
        if act is not None and not act.isChecked():
            act.setChecked(True)

    def _on_animation_toggled(self, paused: bool) -> None:
        self.set_animation_enabled(not paused)
        self.signal_animation_toggled.emit(not paused)

    def start_idle(self) -> None:
        if (not self._animations_enabled or self._focus_mode
                or not self.isVisible()):
            # 专注中不启动（打瞌睡不该蹦跳，折叠路径会调到这里）；
            # 不可见（展开态 stack 里藏着 / 隐藏到托盘）不启动，防 30fps
            # 空转——专注结束的恢复由 set_focus_mode 判 isVisible 决定
            return
        # 相位清零：桌宠重新亮相时从漂浮原点/自然体态起步
        self._float_t = 0.0
        self._breath_t = 0.0
        self._float_frozen = False
        self._breath_frozen = False
        self._last_tick = time.monotonic()
        self._idle_timer.start()
        self._pet_canvas.set_blink_enabled(True)
        self._schedule_random_action()

    def stop_idle(self) -> None:
        self._idle_timer.stop()
        self._float_frozen = False
        self._breath_frozen = False
        self._action_timer.stop()
        self._pet_canvas.set_blink_enabled(False)
        if self._active_action is not None:
            self._active_action.stop()
            self._active_action.deleteLater()   # 打断的动画组否则随 parent 常驻
            self._active_action = None
        if self._hover_anim is not None:
            self._hover_anim.stop()   # finished 回调自行解冻呼吸
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
        self._update_tooltip()
        if on:
            self.stop_idle()
        else:
            # 专注结束重启待机动画：此前只清闭眼态，idle 定时器已停，
            # 桌宠会定格不动直到下次展开/隐藏（start_idle 内部按
            # isVisible 过滤，展开态不会空转）
            self.start_idle()
        self._pet_canvas.set_focus_mode(on)

    def _on_skin_triggered(self, key: str) -> None:
        """皮肤菜单选中：切换绘制并通知控制器持久化"""
        self._pet_canvas.set_skin(key)
        self.signal_skin_selected.emit(key)

    def nudge(self) -> None:
        """提醒示意：跳一下（专注/动画禁用/已有小动作时跳过；
        空闲动画运行中会先冻结、结束后恢复）
        """
        if not self.isVisible():
            return    # 桌宠被禁用或窗口隐藏：跳了也没人看，别让动画空转
        if not self._animations_enabled or self._focus_mode:
            return
        if self._active_action is not None:
            return
        if self._idle_timer.isActive():
            self._float_frozen = True
        group = self._make_jump_action()
        self._active_action = group
        group.finished.connect(self._on_action_finished)
        group.start()

    def set_mood(self, mood: str) -> None:
        self._pet_canvas.set_mood(mood)
        # 表情必须能解释自己：此前"难过脸"只在有卡片逾期时出现，界面上
        # 没有任何地方说明为什么——用户要么当成装饰（于是忽略真实的逾期
        # 信号），要么当成 bug。把原因写进 tooltip，和角标数字说同一件事。
        self._mood = mood
        self._update_tooltip()

    def _update_tooltip(self) -> None:
        """桌宠 tooltip：交互方式 + 角标含义 + 表情原因

        折叠态没有菜单栏、没有标题栏，tooltip 是唯一能就地解释自己的地方。
        """
        lines = [tr("单击展开看板；右键打开菜单")]
        if self._focus_mode:
            lines.append(tr("专注进行中"))
        elif self._count:
            lines.append(tr("今日聚焦 {n} 张").format(n=self._count))
        if self._mood == "sad":
            lines.append(tr("有卡片已逾期"))
        elif self._mood == "worried":
            lines.append(tr("今天有卡片截止"))
        self.setToolTip("\n".join(lines))

    def celebrate(self) -> None:
        """全部完成庆祝：连跳两次后恢复待机"""
        if not self.isVisible():
            return    # 桌宠被禁用或窗口隐藏：跳了也没人看，别让动画空转
        if not self._animations_enabled or self._focus_mode:
            return    # 专注中桌宠闭眼睡觉：庆祝会把睡着的桌宠跳醒
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
        """更新角标为当前卡片总数；0 张时隐藏角标

        只动角标不动主题：样式随主题切换回调下发，此前每次计数都
        reapply_theme（重拼全局 QSS + setStyleSheet re-polish）是
        与计数无关的纯浪费。
        """
        if count == self._count:
            return
        self._count = count
        self._layout_badge()
        self._update_tooltip()   # 角标数字的含义写在 tooltip 里

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._badge is not None:
            # 隐藏到托盘期间计数/倒计时可能已变（_layout_badge 对隐藏态
            # 短路跳过排版），重新显示后补一次。必须用闭包而非 bound
            # method 直接作 slot：PySide6 在 showEvent 派发中连接 bound
            # method 会段错误（offscreen 平台稳定复现）
            QTimer.singleShot(0, lambda: self._layout_badge())

    def _layout_badge(self) -> None:
        """按当前计数/覆盖文本排版角标（尺寸未定时调用也安全）"""
        badge = self._badge
        if badge is None:
            return
        if not self.isVisible():
            # 隐藏到托盘时每秒的番茄钟 tick 仍会驱动到这里：全量排版
            # （adjustSize/raise_）是纯无效功；显示/尺寸变化时有
            # showEvent/resizeEvent 补算路径，跳过不会漏排版
            return
        if self._badge_override is not None:
            text = self._badge_override        # 番茄钟倒计时等
        elif self._count > 0:
            text = "99+" if self._count > 99 else str(self._count)
        else:
            badge.hide()
            return
        # 文本未变只跟随窗口宽度挪位：倒计时逐秒变化但等宽，adjustSize/
        # setFixedWidth/raise_ 全套每秒执行是无效功（set_badge_override
        # 每秒驱动一次）
        if badge.text() == text and badge.isVisible():
            badge.move(self.width() - badge.width() - 6, 6)
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

    def _show_context_menu(self, global_pos) -> None:
        """弹出桌宠右键菜单（鼠标右键与键盘菜单键共用同一入口）"""
        self._context_menu.exec(global_pos)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._pressed = True
            self._forward_to_window(event)
            event.accept()
        elif event.button() == Qt.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())
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

    def keyPressEvent(self, event) -> None:
        """键盘操作折叠态：Return/空格展开看板，菜单键弹右键菜单

        折叠态没有标题栏、没有菜单栏，此前键盘与读屏用户**完全够不着**它：
        打不开看板、开不了快速添加、进不去设置、也退不出应用，只能先去
        找托盘图标。桌宠是应用的主入口，不能只有鼠标能走。
        """
        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.signal_expand_clicked.emit()
            event.accept()
            return
        if key == Qt.Key_Menu:      # 键盘上的"右键"键
            # exec 期望屏幕坐标：rect().center() 是本地坐标，会把菜单
            # 弹到主屏左上角（(44,44) 一带）而不是桌宠旁边
            self._show_context_menu(
                self.mapToGlobal(self.rect().center()))
            event.accept()
            return
        super().keyPressEvent(event)

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

    def reapply_texts(self) -> None:
        """语言切换：右键菜单与皮肤子菜单整组重设文案"""
        self._act_expand.setText(tr("展开看板"))
        self._act_quick_add.setText(tr("快速添加卡片"))
        self._act_today_list.setText(tr("今日清单"))
        self._act_settings.setText(tr("设置…"))
        self._act_quit.setText(tr("退出"))
        self._act_animation.setText(tr("暂停动画"))
        self._act_always_top.setText(tr("窗口置顶"))
        self._skin_menu.setTitle(tr("换皮肤"))
        for key, act in self._skin_actions.items():
            act.setText(skin_display(key))

    # ── 主题 ──────────────────────────────────────────────

    def reapply_theme(self) -> None:
        c = AppTheme.colors()
        if self._badge is not None:
            qss = f"""
                QLabel {{
                    background: {c['accent']};
                    /* accent_on 而非写死白：深色系皮肤的 accent 是亮色，
                       白字压上去不到 3:1（与 theme.primary_button_style
                       同一条理由） */
                    color: {c['accent_on']};
                    border-radius: {tokens.circle_radius(AppConfig.PET_BADGE_SIZE)}px;
                    {tokens.font_css(AppConfig.UI_FONT_LABEL)};
                    font-weight: bold;
                    padding: 0 {tokens.space(5)}px;
                }}
            """
            # 内容相同也触发 re-polish：仅在真正变化时下发
            if self._badge.styleSheet() != qss:
                self._badge.setStyleSheet(qss)
            # 菜单样式由应用级 QSS 覆盖，无需再下发整份全局样式表

"""
过渡动画工具：全局开关 + 可打断的淡入淡出

设计要点（均为实测结论驱动）：
- **全局开关**：`enabled()` 跟随"暂停动画"偏好。所有新增动效都必须经过
  本模块，开关一关即整体退化为瞬时切换——既是低配机器的退路，也是
  Qt 6.10 没有系统级"减弱动效"接口时的唯一调节手段（QStyleHints 无
  任何 motion/reduce 成员）。
- **播完即卸**：淡入结束会摘掉 QGraphicsOpacityEffect，回到无 effect
  基线（实测 0.089ms/帧 vs 0.090ms/帧）。effect 常驻会让控件持续走
  离屏渲染并各留一份缓存位图，多张卡叠加时是纯浪费。
- **可打断**：新动画从当前不透明度续接，不留半透明残留态（悬停/搜索
  等高频交互会反复打断同一条淡入淡出）。

不要用 effect 做主题变色（要插值 QSS 字符串，贵且易脏）；也不要用
QGraphicsDropShadowEffect（实测 0.20ms/帧/卡，是 opacity 的 8 倍）。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QTimer
from PySide6.QtWidgets import QGraphicsOpacityEffect

logger = logging.getLogger(__name__)

_enabled = True


def enabled() -> bool:
    """过渡动画总开关（"暂停动画"偏好）"""
    return _enabled


def set_enabled(on: bool) -> None:
    global _enabled
    _enabled = bool(on)


def _drop_effect(widget) -> None:
    """延迟摘掉淡入用的临时 effect

    延迟到下一轮事件循环，避免在动画回调内部销毁动画的 target。
    守卫：若期间已有新动画接管（_motion_fade 非 None），跳过——否则会把
    新动画的 effect 摘掉（悬停/搜索这类高频交互会连续触发淡入淡出）。
    """
    def _do() -> None:
        if getattr(widget, "_motion_fade", None) is not None:
            return          # 已被新的动画接管，别动
        try:
            widget.setGraphicsEffect(None)
        except RuntimeError:
            pass            # 控件已销毁
    QTimer.singleShot(0, _do)


def fade(widget, to_value: float, duration: int, on_finished=None) -> None:
    """把控件不透明度动画到 to_value

    - 开关关闭 / 时长为 0 → 直接落到终态并立即回调
    - 目标为 1.0 时动画结束会摘掉 effect（回到无 effect 基线）
    - 重复调用会从当前不透明度续接（打断安全）
    """
    prev = getattr(widget, "_motion_fade", None)
    if prev is not None:
        prev.stop()
        widget._motion_fade = None

    if not _enabled or duration <= 0:
        _settle(widget, to_value, on_finished)
        return

    fading_in = to_value >= 1.0
    eff = widget.graphicsEffect()
    if not isinstance(eff, QGraphicsOpacityEffect):
        # 新建 effect：起点必须按方向给足，否则 1.0→1.0 等于没有动画
        # （淡入从全透明起步，淡出从当前不透明起步）
        eff = QGraphicsOpacityEffect(widget)
        eff.setOpacity(0.0 if fading_in else 1.0)
        widget.setGraphicsEffect(eff)

    anim = QPropertyAnimation(eff, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(float(eff.opacity()))
    anim.setEndValue(float(to_value))
    anim.setEasingCurve(QEasingCurve.OutCubic)

    def _finished() -> None:
        if getattr(widget, "_motion_fade", None) is anim:
            widget._motion_fade = None
            if fading_in:
                _drop_effect(widget)   # 淡入结束：摘掉临时 effect
        if on_finished is not None:
            on_finished()
        anim.deleteLater()

    anim.finished.connect(_finished)
    widget._motion_fade = anim
    anim.start()


def _settle(widget, to_value: float, on_finished) -> None:
    """无动画路径：直接落到终态"""
    eff = widget.graphicsEffect()
    if to_value >= 1.0:
        if isinstance(eff, QGraphicsOpacityEffect):
            try:
                widget.setGraphicsEffect(None)
            except RuntimeError:
                pass
    elif isinstance(eff, QGraphicsOpacityEffect):
        eff.setOpacity(float(to_value))
    if on_finished is not None:
        on_finished()


def fade_in(widget, duration: int) -> None:
    """淡入到完全不透明"""
    fade(widget, 1.0, duration)


def fade_out(widget, duration: int, on_finished=None) -> None:
    """淡出到透明；on_finished 里通常 hide() 或 deleteLater()"""
    fade(widget, 0.0, duration, on_finished)

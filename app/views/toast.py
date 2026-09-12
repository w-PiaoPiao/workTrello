"""
看板内轻提示（toast）：操作后的短反馈，约 2 秒后淡出

- 固定在看板底部居中，不拦截鼠标事件
- 深色底白字，浅/深主题下都清晰可读
"""

from __future__ import annotations

from PySide6.QtCore import QPropertyAnimation, QTimer, Qt
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel

from app.config import AppConfig


class Toast(QLabel):
    """浮层提示：show_message() 显示文本，超时淡出"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setStyleSheet("""
            QLabel {
                background: rgba(22, 27, 36, 0.92);
                color: #FFFFFF;
                border-radius: 8px;
                padding: 7px 16px;
                font-size: 12px;
            }
        """)
        self.hide()

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)

        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(260)
        self._fade.finished.connect(self._on_fade_finished)

    def _rebuild_effect(self) -> None:
        """重建 effect 与其动画（setGraphicsEffect(None) 会销毁 effect）"""
        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(260)
        self._fade.finished.connect(self._on_fade_finished)

    def _drop_effect(self) -> None:
        """摘掉 effect：隐藏期不走离屏渲染通道（对齐 motion.py"播完即卸"）"""
        if self._effect is None:
            return
        self._fade.stop()
        self.setGraphicsEffect(None)
        self._effect = None
        self._fade = None

    # ── 对外 ──────────────────────────────────────────────

    def show_message(self, text: str) -> None:
        """显示提示（重置淡出计时与透明度）"""
        if self._effect is None:
            self._rebuild_effect()
        self._fade.stop()
        self._effect.setOpacity(1.0)
        self.setText(text)
        self.adjustSize()
        parent = self.parentWidget()
        if parent is not None:
            self.move((parent.width() - self.width()) // 2,
                      parent.height() - self.height() - 20)
        self.show()
        self.raise_()
        self._timer.start(AppConfig.NOTIFICATION_DURATION_MS)

    def hide_now(self) -> None:
        """立即隐藏（不播放淡出）"""
        self._timer.stop()
        self.hide()
        self._drop_effect()

    # ── 淡出 ──────────────────────────────────────────────

    def _fade_out(self) -> None:
        self._fade.stop()
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _on_fade_finished(self) -> None:
        if self._effect is not None and self._effect.opacity() <= 0.01:
            self.hide()
            self._drop_effect()

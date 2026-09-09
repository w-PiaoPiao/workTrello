"""
无边框主窗口：桌宠（折叠）⇆ 看板（展开）双模式切换

- 无边框、置顶、透明背景
- 桌宠态：固定尺寸、可拖拽
- 看板态：带动画展开、可拖拽缩放
- 位置持久化 + 屏幕边缘吸附
"""

from __future__ import annotations

import logging

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QCursor, QKeySequence, QMouseEvent, QScreen, QShortcut
from PySide6.QtWidgets import QApplication, QStackedWidget, QVBoxLayout, QWidget

from app.config import AppConfig
from app.views.theme import AppTheme

logger = logging.getLogger(__name__)


class MainWindow(QWidget):
    """无边框置顶主窗口"""

    zoom_state_changed = Signal(bool)
    undo_shortcut = Signal()      # Windows/Linux：Ctrl+Z 触发撤销
    new_card_shortcut = Signal()  # Windows/Linux：Ctrl+N 触发快速新建卡片

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool  # 不在任务栏显示
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        if AppConfig.IS_MACOS:
            # Qt.Tool 窗口默认是 hidesOnDeactivate 的 NSPanel：切到其他应用
            # 时会被原生隐藏，且不发 hideEvent，托盘状态错乱、窗口"彻底消失"。
            # 该属性让 Qt 创建面板时设 hidesOnDeactivate=NO，桌宠/看板常驻可见。
            self.setAttribute(Qt.WA_MacAlwaysShowToolWindow, True)

        self._mode = "collapsed"
        self._drag_pos = QPoint()
        self._is_dragging = False
        self._animation_running = False
        self._expanding = False
        self._expand_delta = QPoint()
        self._visible_cb = None
        self._zoomed = False
        self._zoom_restore_geo = QRect()

        # Windows 边缘缩放状态（仅展开态启用；macOS 不启用）
        self._resize_active = False
        if AppConfig.IS_WINDOWS:
            self.setMouseTracking(True)
            self._refresh_edge_cursor()

        # 子视图占位（外部注入）
        self._collapsed_view: QWidget | None = None
        self._expanded_view: QWidget | None = None

        self._stack = QStackedWidget()
        self._stack.setCurrentIndex(0)
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._stack)
        self.setLayout(layout)

        self.setStyleSheet(AppTheme.global_qss())
        AppTheme.register(lambda: self.setStyleSheet(AppTheme.global_qss()))

        self._collapsed_size = QSize(AppConfig.PET_WIDTH, AppConfig.PET_HEIGHT)
        self._expanded_size = self._load_expanded_size()

        # Esc 折叠：QShortcut 挂在窗口上，不依赖焦点落在本窗口（看板内控件聚焦时同样生效）
        self._esc_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self._esc_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._esc_shortcut.activated.connect(self._on_esc_pressed)
        # 撤销（Cmd+Z）由菜单栏 QAction 承担（控制器构建，避免重复快捷键冲突）
        # Cmd+F / Ctrl+F 聚焦搜索框
        self._find_shortcut = QShortcut(QKeySequence.Find, self)
        self._find_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._find_shortcut.activated.connect(self._focus_board_search)

        # Windows/Linux 键盘入口：撤销 / 新建卡片（macOS 由全局菜单栏 QAction
        # 承担同键，注册会与菜单快捷键双重触发）
        if not AppConfig.IS_MACOS:
            self._undo_shortcut = QShortcut(QKeySequence.Undo, self)
            self._undo_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            self._undo_shortcut.activated.connect(self.undo_shortcut.emit)
            self._new_card_shortcut = QShortcut(QKeySequence.New, self)
            self._new_card_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            self._new_card_shortcut.activated.connect(
                self.new_card_shortcut.emit)

        self._move_to_default_position()

    # ── 置顶切换 ──────────────────────────────────────────

    def is_always_on_top(self) -> bool:
        return bool(self.windowFlags() & Qt.WindowStaysOnTopHint)

    def set_always_on_top(self, on: bool) -> None:
        """切换窗口置顶；setWindowFlags 会隐藏并重建原生窗口，需补 show"""
        if self.is_always_on_top() == on:
            return
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
        if was_visible:
            self.show()
            if on:
                self.raise_()
                self.activateWindow()

    # ── 视图注入 ──────────────────────────────────────────

    def set_views(self, collapsed_view: QWidget, expanded_view: QWidget) -> None:
        self._collapsed_view = collapsed_view
        self._expanded_view = expanded_view
        self._stack.addWidget(collapsed_view)
        self._stack.addWidget(expanded_view)
        self.setFixedSize(self._collapsed_size)

    # ── 模式 ──────────────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._mode

    def _set_pet_idle(self, active: bool) -> None:
        view = self._collapsed_view
        if view is None or self._mode != "collapsed":
            return
        handler = getattr(view, "start_idle" if active else "stop_idle", None)
        if handler is None:
            return
        if active and not self._animation_running:
            handler()
        elif not active:
            handler()

    def start_collapsed_idle(self) -> None:
        self._set_pet_idle(True)

    def expand(self) -> None:
        if self._mode == "expanded" or self._animation_running:
            return
        if not self.isVisible():
            self.show()  # 展开前确保窗口可见（单击桌面宠物时）
        self._set_pet_idle(False)
        self._zoomed = False
        target = self._effective_expanded_size()
        screen = self._current_screen()
        base_geo = self.geometry()
        self._expand_delta = self._visible_expand_delta(base_geo, target, screen)
        self._mode = "expanded"
        self._expanding = True

        self.setFixedSize(QSize(16777215, 16777215))
        self.setMinimumSize(AppConfig.BOARD_MIN_WIDTH, AppConfig.BOARD_MIN_HEIGHT)
        self.setMaximumSize(AppConfig.BOARD_MAX_WIDTH, AppConfig.BOARD_MAX_HEIGHT)
        self._stack.setCurrentWidget(self._expanded_view)
        self._animate_size(
            target.width(), target.height(),
            delta=self._expand_delta, base_geo=base_geo)
        # 展开完成后（鼠标可能正悬停在边缘）刷新一次缩放光标
        if AppConfig.IS_WINDOWS:
            QTimer.singleShot(AppConfig.ANIMATION_MS + 30,
                              self._refresh_edge_cursor)

    def collapse(self) -> None:
        if self._mode == "collapsed" or self._animation_running:
            return
        self._finish_board_rename()    # 提交未完成的重命名
        self._zoomed = False
        base_geo = self.geometry()
        self._mode = "collapsed"

        self.setMaximumSize(16777215, 16777215)
        self.setMinimumSize(0, 0)
        self.setFixedSize(QSize(16777215, 16777215))
        self._animate_size(
            self._collapsed_size.width(), self._collapsed_size.height(),
            delta=-self._expand_delta, base_geo=base_geo)

    def _on_esc_pressed(self) -> None:
        if self._mode != "expanded":
            return
        if self._finish_board_rename(cancel=True):
            return    # Esc 先取消重命名，再按一次才折叠
        # 仅在焦点位于搜索框时用 Esc 清空搜索（正在输入的用户预期先清空）；
        # 搜索有字但焦点在别处时，Esc 的意图是收起看板，直接折叠
        if (self._expanded_view is not None
                and self._expanded_view.search_has_focus()
                and self._expanded_view.clear_search_if_active()):
            return
        self.collapse()

    def _focus_board_search(self) -> None:
        if self._mode == "expanded" and self._expanded_view is not None:
            self._expanded_view.focus_search()

    def _finish_board_rename(self, cancel: bool = False) -> bool:
        """关闭看板里可能打开的列表重命名编辑器；返回是否有关闭"""
        closer = getattr(self._expanded_view, "finish_rename", None)
        return bool(closer is not None and closer(cancel=cancel))

    def toggle_zoom(self) -> None:
        """红绿灯绿键 / Windows 最大化键：最大化 ⇆ 还原（仅看板态有效）"""
        if self._mode != "expanded" or self._animation_running:
            return
        if self._zoomed:
            geo = self._zoom_restore_geo
            self._zoomed = False
        else:
            self._zoom_restore_geo = self.geometry()
            screen = self._current_screen()
            if screen is None:
                geo = self.geometry()
            elif AppConfig.IS_WINDOWS:
                # Windows 原生最大化：铺满工作区（任务栏除外），不留边
                geo = screen.availableGeometry()
            else:
                # macOS 红绿灯绿灯语义：四边留 SCREEN_MARGIN
                margin = AppConfig.SCREEN_MARGIN
                geo = screen.availableGeometry().adjusted(
                    margin, margin, -margin, -margin)
            self._zoomed = True
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.setGeometry(geo)
        if not self._zoomed:
            self.setMinimumSize(AppConfig.BOARD_MIN_WIDTH,
                                AppConfig.BOARD_MIN_HEIGHT)
            self.setMaximumSize(AppConfig.BOARD_MAX_WIDTH,
                                AppConfig.BOARD_MAX_HEIGHT)
        self.zoom_state_changed.emit(self._zoomed)

    # ── 拖拽 / 边缘缩放 ─────────────────────────────────

    def _mouse_in_expanded(self) -> bool:
        """窗口是否处于可拖拽/可缩放的展开态（非动画中）"""
        return (self._mode == "expanded"
                and not self._animation_running
                and not self._expanding)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            if AppConfig.IS_WINDOWS and self._mouse_in_expanded():
                edge = self._edge_at(event.position().toPoint())
                if edge:
                    # 命中边缘 → 交给系统级缩放（鼠标被 OS 捕获直到释放）
                    handle = self.windowHandle()
                    if handle is not None and handle.startSystemResize(edge):
                        self._resize_active = True
                    event.accept()
                    return
            self._set_pet_idle(False)
            self._drag_pos = (event.globalPosition().toPoint()
                              - self.frameGeometry().topLeft())
            self._is_dragging = True
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._is_dragging and event.buttons() == Qt.LeftButton:
            if (AppConfig.IS_WINDOWS and self._zoomed
                    and not self._animation_running):
                # Windows 惯例：拖动最大化的窗口 = 还原，光标落回标题栏
                self.toggle_zoom()
                restored = self.geometry()
                self._drag_pos = QPoint(restored.width() // 2, 28)
                self.move(event.globalPosition().toPoint() - self._drag_pos)
                event.accept()
                return
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        if (AppConfig.IS_WINDOWS
                and self._mode == "expanded"
                and not self._animation_running):
            # 无按键悬停：按边缘刷新缩放光标（折叠/动画中不改变）
            self._refresh_edge_cursor()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            if self._resize_active:
                self._resize_active = False
                # 系统缩放循环期间 Qt 收不到鼠标事件，结束后窗口 cursor
                # 仍停在缩放样式；按鼠标真实位置重算（边缘→缩放光标，
                # 内部→箭头），并等事件循环恢复 hover 后再兜底刷一次
                self._refresh_edge_cursor()
                QTimer.singleShot(0, self._refresh_edge_cursor)
                self._snap_to_screen_edge()
                self._save_position()
                event.accept()
                return
            self._is_dragging = False
            self._snap_to_screen_edge()
            self._save_position()
            self._set_pet_idle(True)
            event.accept()

    def resizeEvent(self, event) -> None:
        """看板态用户调整尺寸时即时持久化（排除动画/中间态/最大化还原）"""
        if (self._mode == "expanded"
                and not self._animation_running
                and not self._expanding
                and not self._zoomed):
            self._expanded_size = self.size()
            AppConfig.save_expanded_size(self._expanded_size)
        super().resizeEvent(event)

    # ── 显隐（托盘"隐藏"时窗口隐藏而非销毁，事件通知控制器同步菜单） ──

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._visible_cb is not None:
            self._visible_cb(True)

    def hideEvent(self, event) -> None:
        # 立即终止进行中的折叠/展开动画，避免隐藏态下动画继续驱动 geometry
        if self._animation_running and hasattr(self, "anim"):
            self.anim.stop()
        self._animation_running = False
        self._expanding = False
        self._finish_board_rename()    # 隐藏前提交未完成的重命名
        if self._collapsed_view is not None:
            self._set_pet_idle(False)
        if self._mode == "expanded":
            self._mode = "collapsed"
            if self._collapsed_view is not None:
                self._stack.setCurrentWidget(self._collapsed_view)
                self.setFixedSize(self._collapsed_size)
        super().hideEvent(event)
        if self._visible_cb is not None:
            self._visible_cb(False)

    # ── 动画 ──────────────────────────────────────────────

    def _animate_size(self, target_w: int, target_h: int,
                      delta: QPoint | None = None,
                      base_geo: QRect | None = None) -> None:
        self._animation_running = True
        self.anim = QPropertyAnimation(self, b"geometry")
        self.anim.setDuration(AppConfig.ANIMATION_MS)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)

        base = base_geo if base_geo is not None else self.geometry()
        dx = delta.x() if delta is not None else 0
        dy = delta.y() if delta is not None else 0
        self.anim.setStartValue(self.geometry())
        self.anim.setEndValue(QRect(base.x() + dx, base.y() + dy,
                                    target_w, target_h))
        self.anim.finished.connect(self._on_animation_finished)
        self.anim.start()

    def _on_animation_finished(self) -> None:
        self._animation_running = False
        self._expanding = False
        if self._mode == "collapsed":
            self._stack.setCurrentWidget(self._collapsed_view)
            self.setFixedSize(self._collapsed_size)
            self.unsetCursor()
            self._set_pet_idle(True)

    def _visible_expand_delta(self, pet_geo: QRect, target: QSize,
                              screen: QScreen | None) -> QPoint:
        """展开时左上角位移，保证看板完整落在屏幕可用区域内"""
        margin = AppConfig.SCREEN_MARGIN
        x = pet_geo.x()
        y = pet_geo.y()
        if screen is not None:
            geo = screen.availableGeometry()
            tw, th = target.width(), target.height()
            if pet_geo.left() < geo.left():
                x = geo.left() + margin
            elif pet_geo.left() + tw > geo.right() - margin:
                anchor_x = pet_geo.right() + 1 - tw
                if anchor_x >= geo.left() + margin:
                    x = anchor_x
                else:
                    x = geo.right() - margin - tw + 1
            if pet_geo.top() < geo.top():
                y = geo.top() + margin
            elif pet_geo.top() + th > geo.bottom() - margin:
                anchor_y = pet_geo.bottom() + 1 - th
                if anchor_y >= geo.top() + margin:
                    y = anchor_y
                else:
                    y = geo.bottom() - margin - th + 1
        return QPoint(x - pet_geo.x(), y - pet_geo.y())

    # ── 尺寸与位置 ────────────────────────────────────────

    def _load_expanded_size(self) -> QSize:
        size = AppConfig.get_expanded_size()
        if isinstance(size, QSize):
            w = max(AppConfig.BOARD_MIN_WIDTH,
                    min(size.width(), AppConfig.BOARD_MAX_WIDTH))
            h = max(AppConfig.BOARD_MIN_HEIGHT,
                    min(size.height(), AppConfig.BOARD_MAX_HEIGHT))
            return QSize(w, h)
        return QSize(AppConfig.BOARD_WIDTH, AppConfig.BOARD_HEIGHT)

    def _effective_expanded_size(self) -> QSize:
        w = self._expanded_size.width()
        h = self._expanded_size.height()
        screen = self._current_screen()
        if screen:
            geo = screen.availableGeometry()
            margin = AppConfig.SCREEN_MARGIN
            w = min(w, geo.width() - margin * 2)
            h = min(h, geo.height() - margin * 2)
        w = max(AppConfig.BOARD_MIN_WIDTH, min(w, AppConfig.BOARD_MAX_WIDTH))
        h = max(AppConfig.BOARD_MIN_HEIGHT, min(h, AppConfig.BOARD_MAX_HEIGHT))
        return QSize(w, h)

    def _move_to_default_position(self) -> None:
        if self._restore_position():
            return
        screen = QApplication.primaryScreen()
        if screen:
            geometry = screen.availableGeometry()
            x = geometry.right() - self._collapsed_size.width() \
                - AppConfig.SCREEN_MARGIN
            y = geometry.top() + AppConfig.SCREEN_MARGIN
            self.move(x, y)

    def _save_position(self) -> None:
        AppConfig.save_window_pos(self.pos())

    def _restore_position(self) -> bool:
        pos = AppConfig.get_window_pos()
        if pos is not None and isinstance(pos, QPoint):
            self.move(pos)
            if QApplication.screenAt(self.geometry().center()) is not None:
                return True
            logger.warning("恢复的窗口位置超出屏幕范围，回退默认位置: %s", pos)
        return False

    def _current_screen(self) -> QScreen | None:
        screen = QApplication.screenAt(self.geometry().center())
        if screen is None:
            for s in QApplication.screens():
                if s.geometry().intersects(self.geometry()):
                    return s
            screen = QApplication.primaryScreen()
        return screen

    def _snap_to_screen_edge(self) -> None:
        screen = self._current_screen()
        if not screen:
            return
        geo = screen.availableGeometry()
        pos = self.pos()
        w, h = self.width(), self.height()
        margin = AppConfig.SCREEN_MARGIN

        new_x = max(geo.left() + margin,
                    min(pos.x(), geo.right() - w - margin))
        new_y = max(geo.top() + margin,
                    min(pos.y(), geo.bottom() - h - margin))
        if new_x != pos.x() or new_y != pos.y():
            self.move(new_x, new_y)

    # ── Windows 边缘缩放 ─────────────────────────────────

    def _edge_at(self, pos: QPoint) -> Qt.Edge | None:
        """pos（窗口内坐标）是否落在可缩放边缘/角；返回对应 Qt.Edge 组合或 None"""
        if (self._mode != "expanded" or self._animation_running
                or self._zoomed):
            # 最大化状态下不允许边缘缩放（对齐原生窗口行为）
            return None
        m = AppConfig.RESIZE_MARGIN
        w, h = self.width(), self.height()
        x, y = pos.x(), pos.y()
        edge = Qt.Edge()
        if x < m:
            edge |= Qt.LeftEdge
        elif x >= w - m:
            edge |= Qt.RightEdge
        if y < m:
            edge |= Qt.TopEdge
        elif y >= h - m:
            edge |= Qt.BottomEdge
        return edge if edge else None

    def _refresh_edge_cursor(self) -> None:
        """无按键悬停时按边缘更新光标（展开态 + Windows 生效）"""
        if not (AppConfig.IS_WINDOWS and self._mouse_in_expanded()):
            self.unsetCursor()
            return
        edge = self._edge_at(self.mapFromGlobal(QCursor.pos()))
        if edge is None:
            self.unsetCursor()
            return
        cursor = None
        if edge & Qt.LeftEdge and edge & Qt.TopEdge:
            cursor = Qt.SizeFDiagCursor
        elif edge & Qt.RightEdge and edge & Qt.BottomEdge:
            cursor = Qt.SizeFDiagCursor
        elif edge & Qt.RightEdge and edge & Qt.TopEdge:
            cursor = Qt.SizeBDiagCursor
        elif edge & Qt.LeftEdge and edge & Qt.BottomEdge:
            cursor = Qt.SizeBDiagCursor
        elif edge & Qt.LeftEdge or edge & Qt.RightEdge:
            cursor = Qt.SizeHorCursor
        else:
            cursor = Qt.SizeVerCursor
        self.setCursor(cursor)

    def set_visibility_callback(self, callback) -> None:
        """控制器注入显隐回调（同步托盘菜单文案）"""
        self._visible_cb = callback

    def hide_to_tray(self) -> None:
        """隐藏到托盘：先折叠回桌宠态，再隐藏窗口"""
        if self._mode == "expanded":
            self.collapse()
        self.hide()

    def show_and_activate(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        if AppConfig.IS_MACOS:
            # accessory 面板被点击/托盘唤起时不会自动激活应用，需显式接管菜单栏
            from app.platform.mac_activation import activate_app
            activate_app()
        if self._mode == "collapsed":
            self._set_pet_idle(True)
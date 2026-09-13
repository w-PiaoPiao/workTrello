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
    QPointF,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPen,
    QScreen,
    QShortcut,
)
from PySide6.QtWidgets import QApplication, QStackedWidget, QVBoxLayout, QWidget

from app.config import AppConfig
from app.i18n import tr
from app.views.theme import AppTheme

logger = logging.getLogger(__name__)


class _ResizeGrip(QWidget):
    """macOS 看板右下角缩放把手（自绘三条斜线，拖动改窗口尺寸）

    Windows 已有系统级边缘缩放，仅 macOS 使用；最小/最大尺寸由
    expand() 设置的 setMinimumSize/setMaximumSize 约束。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self.setCursor(Qt.SizeFDiagCursor)
        self.setToolTip(tr("拖动调整看板大小"))
        self._start_size = QSize()
        self._start_global = QPoint()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        c = AppTheme.colors()
        color = QColor(c["text_secondary"])
        color.setAlpha(150)
        pen = QPen(color, 1.3)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        w, h = self.width(), self.height()
        for i in (1, 5, 9):
            painter.drawLine(QPointF(i, h - 1), QPointF(w - 1, i))
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._start_size = self.window().size()
            self._start_global = event.globalPosition().toPoint()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.LeftButton:
            delta = event.globalPosition().toPoint() - self._start_global
            win = self.window()
            win.resize(self._start_size.width() + delta.x(),
                       self._start_size.height() + delta.y())
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            event.accept()


class MainWindow(QWidget):
    """无边框置顶主窗口"""

    zoom_state_changed = Signal(bool)
    undo_shortcut = Signal()      # Windows/Linux：Ctrl+Z 触发撤销
    redo_shortcut = Signal()      # Windows/Linux：Ctrl+Y 触发重做
    new_card_shortcut = Signal()  # Windows/Linux：Ctrl+N 触发快速新建卡片
    shortcuts_requested = Signal()  # ? 呼出快捷键速查（全平台）
    signal_about_to_expand = Signal()  # 展开动画启动前（控制器延迟构建看板）

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

        # 展开尺寸持久化防抖：系统缩放循环按帧触发 resizeEvent，
        # 停顿（或折叠）后才写 QSettings，避免逐帧写注册表
        self._size_save_timer = QTimer(self)
        self._size_save_timer.setSingleShot(True)
        self._size_save_timer.setInterval(AppConfig.SIZE_SAVE_DEBOUNCE_MS)
        self._size_save_timer.timeout.connect(self._flush_expanded_size)

        # Windows 边缘缩放状态（仅展开态启用；macOS 不启用）
        self._resize_active = False
        # 上次设置的边缘光标（None=系统默认）；仅结果变化才调平台接口
        self._last_edge_cursor: Qt.CursorShape | None = None
        if AppConfig.IS_WINDOWS:
            self.setMouseTracking(True)
            # 展开态轻量光标轮询：系统缩放期间 Qt 收不到鼠标事件、
            # 结束后 hover/move 链可能不重建，轮询保证光标始终按鼠标真实
            # 位置回正（刷新函数内含展开态/窗口内守卫）。随展开/折叠与
            # 显隐启停——桌宠折叠态/隐藏态不再每 150ms 空转唤醒事件循环
            self._edge_cursor_timer = QTimer(self)
            self._edge_cursor_timer.setInterval(AppConfig.EDGE_CURSOR_POLL_MS)
            self._edge_cursor_timer.timeout.connect(self._refresh_edge_cursor)

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

        # 全局样式由 AppTheme.apply() 在应用级下发；此处不再设窗口级副本，
        # 否则切换主题时它会在 app 级规则之上再盖一层旧配色的同款 QSS
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

        # Windows/Linux 键盘入口：撤销 / 重做 / 新建卡片（macOS 由全局菜单栏
        # QAction 承担同键，注册会与菜单快捷键双重触发）
        if not AppConfig.IS_MACOS:
            self._undo_shortcut = QShortcut(QKeySequence.Undo, self)
            self._undo_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            self._undo_shortcut.activated.connect(self.undo_shortcut.emit)
            self._redo_shortcut = QShortcut(QKeySequence("Ctrl+Y"), self)
            self._redo_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            self._redo_shortcut.activated.connect(self.redo_shortcut.emit)
            self._new_card_shortcut = QShortcut(QKeySequence.New, self)
            self._new_card_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            self._new_card_shortcut.activated.connect(
                self.new_card_shortcut.emit)

        # ? 呼出快捷键速查（全平台；文本输入中不触发——handler 侧再校验）
        self._help_shortcut = QShortcut(QKeySequence("?"), self)
        self._help_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._help_shortcut.activated.connect(self.shortcuts_requested.emit)

        # macOS 无系统级边缘缩放：右下角自绘把手（仅展开态、非最大化时可见）
        self._resize_grip = _ResizeGrip(self) if AppConfig.IS_MACOS else None
        if self._resize_grip is not None:
            self._resize_grip.hide()

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
        # 动画与视图切换前通知控制器（首展开时同步构建看板控件）
        self.signal_about_to_expand.emit()
        self._set_pet_idle(False)
        self._zoomed = False
        target = self._effective_expanded_size()
        screen = self._current_screen()
        base_geo = self.geometry()
        self._expand_delta = self._visible_expand_delta(base_geo, target, screen)
        self._mode = "expanded"
        self._expanding = True

        self.setFixedSize(QSize(16777215, 16777215))
        min_w, min_h = self._effective_min_size()
        self.setMinimumSize(min_w, min_h)
        self.setMaximumSize(AppConfig.BOARD_MAX_WIDTH, AppConfig.BOARD_MAX_HEIGHT)
        self._stack.setCurrentWidget(self._expanded_view)
        self._animate_size(
            target.width(), target.height(),
            delta=self._expand_delta, base_geo=base_geo)
        # 展开后光标由 EDGE_CURSOR_POLL_MS 轮询持续刷新（动画期守卫空转，
        # 动画结束即正常生效；折叠/隐藏时停止）
        if AppConfig.IS_WINDOWS:
            self._edge_cursor_timer.start()

    def collapse(self) -> None:
        if self._mode == "collapsed" or self._animation_running:
            return
        self._finish_board_rename()    # 提交未完成的重命名
        self._zoomed = False
        base_geo = self.geometry()
        self._mode = "collapsed"
        if AppConfig.IS_WINDOWS:
            self._edge_cursor_timer.stop()   # 折叠态轮询是纯空转

        self.setMaximumSize(16777215, 16777215)
        self.setMinimumSize(0, 0)
        self.setFixedSize(QSize(16777215, 16777215))
        self._animate_size(
            self._collapsed_size.width(), self._collapsed_size.height(),
            delta=-self._expand_delta, base_geo=base_geo)
        self._flush_expanded_size()    # 折叠时冲刷防抖中的展开尺寸

    def _on_esc_pressed(self) -> None:
        if self._mode != "expanded":
            return
        if self._finish_board_rename(cancel=True):
            return    # Esc 先取消重命名，再按一次才折叠
        # 有多选卡片时 Esc 先清空多选（再按才走搜索/折叠）
        clearer = getattr(self._expanded_view, "clear_selection_if_active",
                          None)
        if clearer is not None and clearer():
            return
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
            min_w, min_h = self._effective_min_size()
            self.setMinimumSize(min_w, min_h)
            self.setMaximumSize(AppConfig.BOARD_MAX_WIDTH,
                                AppConfig.BOARD_MAX_HEIGHT)
        self.zoom_state_changed.emit(self._zoomed)
        self._update_grip_visibility()

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
                # 仍停在缩放样式：先吸附修正窗口位置，再按真实位置刷新；
                # singleShot(0) 等 Qt hover 链重建后再校正一次
                # （另有 EDGE_CURSOR_POLL_MS 常驻轮询兜底）
                self._snap_to_screen_edge()
                self._refresh_edge_cursor()
                QTimer.singleShot(0, self._refresh_edge_cursor)
                self._save_position()
                event.accept()
                return
            self._is_dragging = False
            self._snap_to_screen_edge()
            self._save_position()
            self._set_pet_idle(True)
            event.accept()

    def resizeEvent(self, event) -> None:
        """看板态用户调整尺寸时防抖持久化（排除动画/中间态/最大化还原）"""
        if (self._mode == "expanded"
                and not self._animation_running
                and not self._expanding
                and not self._zoomed):
            self._expanded_size = self.size()
            self._size_save_timer.start()
        if self._resize_grip is not None and self._resize_grip.isVisible():
            self._position_resize_grip()
        super().resizeEvent(event)

    def _flush_expanded_size(self) -> None:
        """防抖到点 / 折叠时：把暂存的展开尺寸写盘"""
        AppConfig.save_expanded_size(self._expanded_size)

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
        if AppConfig.IS_WINDOWS:
            self._edge_cursor_timer.stop()   # 隐藏态不再轮询光标
        self._finish_board_rename()    # 隐藏前提交未完成的重命名
        if self._collapsed_view is not None:
            self._set_pet_idle(False)
        # 动画可能被 hide 打断：finished 不再触发，_on_animation_finished
        # 不会执行 → 无论当前模式都强制回到折叠态（切回桌宠视图 + 固定尺寸
        # + 收起缩放把手），避免再次显示时窗口停在展开尺寸、grip 残留
        self._mode = "collapsed"
        if self._collapsed_view is not None:
            self._stack.setCurrentWidget(self._collapsed_view)
            self.setFixedSize(self._collapsed_size)
        self.unsetCursor()
        self._update_grip_visibility()
        super().hideEvent(event)
        if self._visible_cb is not None:
            self._visible_cb(False)

    # ── 动画 ──────────────────────────────────────────────

    def _animate_size(self, target_w: int, target_h: int,
                      delta: QPoint | None = None,
                      base_geo: QRect | None = None) -> None:
        self._animation_running = True
        old = getattr(self, "anim", None)
        if old is not None:
            old.stop()
            old.deleteLater()   # 带 parent 的旧动画不被 GC，逐次展开/折叠会累积
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
        self._update_grip_visibility()

    # ── macOS 缩放把手 ─────────────────────────────────────

    def _update_grip_visibility(self) -> None:
        """缩放把手：仅展开态、非动画中、非最大化时显示"""
        grip = self._resize_grip
        if grip is None:
            return
        show = (self._mode == "expanded" and not self._animation_running
                and not self._expanding and not self._zoomed)
        grip.setVisible(show)
        if show:
            self._position_resize_grip()

    def reapply_texts(self) -> None:
        """语言切换：缩放把手提示刷新（交通灯在 BoardView 上自行刷新）"""
        if self._resize_grip is not None:
            self._resize_grip.setToolTip(tr("拖动调整看板大小"))

    def _position_resize_grip(self) -> None:
        grip = self._resize_grip
        if grip is None:
            return
        grip.move(self.width() - grip.width() - 6,
                  self.height() - grip.height() - 6)

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

    def _effective_min_size(self) -> tuple[int, int]:
        """展开态最小尺寸：小屏上最小值不得超过可用区

        BOARD_MIN_WIDTH(980) 若大于屏幕可用宽（800×600/1024×600 类），
        钳制会让窗口比屏幕还宽、左缘出屏；此时以屏幕为准放下限。
        """
        min_w, min_h = AppConfig.BOARD_MIN_WIDTH, AppConfig.BOARD_MIN_HEIGHT
        screen = self._current_screen()
        if screen:
            geo = screen.availableGeometry()
            margin = AppConfig.SCREEN_MARGIN
            min_w = min(min_w, max(320, geo.width() - margin * 2))
            min_h = min(min_h, max(240, geo.height() - margin * 2))
        return min_w, min_h

    def _effective_expanded_size(self) -> QSize:
        w = self._expanded_size.width()
        h = self._expanded_size.height()
        screen = self._current_screen()
        if screen:
            geo = screen.availableGeometry()
            margin = AppConfig.SCREEN_MARGIN
            w = min(w, geo.width() - margin * 2)
            h = min(h, geo.height() - margin * 2)
        min_w, min_h = self._effective_min_size()
        w = max(min_w, min(w, AppConfig.BOARD_MAX_WIDTH))
        h = max(min_h, min(h, AppConfig.BOARD_MAX_HEIGHT))
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
        """按鼠标真实位置刷新边缘缩放光标（展开态 + Windows 生效）

        所有触发源（悬停 move / 常驻轮询 / 缩放收尾 / 移入窗口）统一走这里：
        - 鼠标不在窗口内、或非展开态/动画中 → 还原系统默认光标
          （窗外全局坐标会被 _edge_at 误判成边缘，必须先守卫）
        - 仅当结果与上次不同才调用 setCursor/unsetCursor（幂等缓存，
          避免 150ms 轮询反复触发平台光标更新）
        """
        cursor: Qt.CursorShape | None = None    # None = 还原系统默认
        if (AppConfig.IS_WINDOWS and self._mouse_in_expanded()
                and self.frameGeometry().contains(QCursor.pos())):
            edge = self._edge_at(self.mapFromGlobal(QCursor.pos()))
            if edge is not None:
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
        if cursor is None:
            # 还原默认：仅当缓存或实际光标仍停在其他样式时才 unset
            # （外部手动 setCursor 后缓存可能为 None，需按实际形状判定）
            if (self._last_edge_cursor is not None
                    or self.cursor().shape() != Qt.ArrowCursor):
                self._last_edge_cursor = None
                self.unsetCursor()
        elif cursor != self._last_edge_cursor:
            self._last_edge_cursor = cursor
            self.setCursor(cursor)

    def enterEvent(self, event) -> None:
        """移入窗口立即按位置刷新光标（不等首次 move）"""
        self._refresh_edge_cursor()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        """移出窗口即还原默认光标（常驻轮询的窗口内守卫同样兜底）"""
        self._last_edge_cursor = None
        self.unsetCursor()
        super().leaveEvent(event)

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
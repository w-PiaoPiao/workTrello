"""全局快捷键：应用失焦时也能唤起快速添加（Windows RegisterHotKey）

为什么需要单独一个隐藏窗口：RegisterHotKey 必须绑定一个 HWND，且
WM_HOTKEY 只会投递给该窗口。挂在主窗口上有两个坑——主窗口在"窗口置顶"
切换时会 setWindowFlags 重建原生窗口（句柄变了，热键静默失效），折叠/隐藏
时也可能被销毁。这里用一个永不显示的 QWidget 承载句柄，与窗口形态解耦。

平台：仅 Windows 实现。macOS 的全局热键要走 Carbon RegisterEventHotKey 或
辅助功能权限，本应用不引第三方库，暂不提供；其他平台一律 is_available()
=False，调用方据此隐藏设置项。

不做全局键盘钩子：RegisterHotKey 是系统提供的、可被系统回收的独占注册，
比低级钩子（WH_KEYBOARD_LL）更省资源，也不会被安全软件当成键盘记录器。
"""

from __future__ import annotations

import ctypes
import logging
import sys

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QWidget

logger = logging.getLogger(__name__)

WM_HOTKEY = 0x0312

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000   # 长按不重复触发（Win7+）

_VK_DIGIT0 = 0x30      # '0'..'9'
_VK_A = 0x41           # 'A'..'Z'
_VK_F1 = 0x70          # F1..F24


class _HotkeyMsg(ctypes.Structure):
    """Win32 MSG（只取用到的字段；消息只读 wParam/message）"""

    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", ctypes.c_uint),
        ("pt_x", ctypes.c_long),
        ("pt_y", ctypes.c_long),
    ]


def _native_register(hwnd: int, hotkey_id: int, mods: int, vk: int) -> bool:
    """RegisterHotKey 薄封装（测试替换点：真机注册会占用系统热键）"""
    user32 = ctypes.windll.user32
    return bool(user32.RegisterHotKey(ctypes.c_void_p(hwnd), hotkey_id,
                                      mods, vk))


def _native_unregister(hwnd: int, hotkey_id: int) -> None:
    user32 = ctypes.windll.user32
    user32.UnregisterHotKey(ctypes.c_void_p(hwnd), hotkey_id)


def sequence_to_native(sequence: QKeySequence) -> tuple[int, int] | None:
    """QKeySequence → (修饰键, 虚拟键码)；不支持的组合返回 None

    只覆盖字母/数字/F1-F24：全局热键的用途是"随手唤起"，留给用户的是
    一个固定组合，不需要支持方向键/标点这类在文本输入里常用的键。
    """
    if sequence.isEmpty():
        return None
    combo = sequence[0]
    key = combo.key()
    mods = 0
    if combo.keyboardModifiers() & Qt.ControlModifier:
        mods |= MOD_CONTROL
    if combo.keyboardModifiers() & Qt.AltModifier:
        mods |= MOD_ALT
    if combo.keyboardModifiers() & Qt.ShiftModifier:
        mods |= MOD_SHIFT
    if combo.keyboardModifiers() & Qt.MetaModifier:
        mods |= MOD_WIN
    if not mods:
        return None    # 无修饰键的全局热键会吞掉正常打字

    if Qt.Key_A <= key <= Qt.Key_Z:
        vk = _VK_A + (key - Qt.Key_A)
    elif Qt.Key_0 <= key <= Qt.Key_9:
        vk = _VK_DIGIT0 + (key - Qt.Key_0)
    elif Qt.Key_F1 <= key <= Qt.Key_F24:
        vk = _VK_F1 + (key - Qt.Key_F1)
    else:
        return None
    return mods | MOD_NOREPEAT, vk


class _WinHotkeyFilter(QAbstractNativeEventFilter):
    """WM_HOTKEY → 回调（只认本服务注册的那个 id）"""

    def __init__(self, hotkey_id: int, on_hit) -> None:
        super().__init__()
        self._id = hotkey_id
        self._on_hit = on_hit

    def nativeEventFilter(self, eventType, message):   # noqa: N802 — Qt 命名
        try:
            addr = int(message)
        except (TypeError, ValueError):
            return False, 0
        if not addr:
            return False, 0
        try:
            msg = ctypes.cast(addr, ctypes.POINTER(_HotkeyMsg)).contents
        except (ctypes.ArgumentError, ValueError):
            return False, 0
        if msg.message == WM_HOTKEY and int(msg.wParam) == self._id:
            self._on_hit()
        return False, 0   # 不拦截：继续走 Qt 自己的分发


class GlobalHotkeyService(QObject):
    """全局热键注册器（同一时刻只持有一个组合）"""

    triggered = Signal()

    # 进程内唯一 id：只允许本服务注册一个热键，id 用固定值即可
    _HOTKEY_ID = 0x5042        # "PB"
    # 默认组合：Ctrl+Alt+N（避开常见软件的 Ctrl+Shift+N/Alt+N）
    DEFAULT_SEQUENCE = "Ctrl+Alt+N"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._host: QWidget | None = None
        self._filter: _WinHotkeyFilter | None = None
        self._registered = False

    @staticmethod
    def is_available() -> bool:
        """当前平台是否支持全局热键（仅 Windows）"""
        return sys.platform == "win32"

    def is_registered(self) -> bool:
        return self._registered

    def _ensure_host(self) -> int:
        """承载 WM_HOTKEY 的隐藏窗口句柄（惰性创建，永不显示）"""
        if self._host is None:
            self._host = QWidget()
            self._host.setWindowTitle("petboard-hotkey")
            self._host.resize(0, 0)
        return int(self._host.winId())

    def register(self, sequence) -> bool:
        """注册全局热键；已被别的程序占用时返回 False（不抛异常）"""
        if not self.is_available():
            return False
        self.unregister()
        if isinstance(sequence, str):
            sequence = QKeySequence(sequence)
        native = sequence_to_native(sequence)
        if native is None:
            logger.warning("不支持的全局热键组合: %s", sequence.toString())
            return False
        mods, vk = native
        try:
            hwnd = self._ensure_host()
        except Exception as e:   # noqa: BLE001 — 原生窗口创建失败不应中断启动
            logger.warning("全局热键宿主窗口创建失败: %s", e)
            return False
        try:
            ok = _native_register(hwnd, self._HOTKEY_ID, mods, vk)
        except OSError as e:
            logger.warning("全局热键注册异常: %s", e)
            return False
        if not ok:
            logger.info("全局热键已被占用: %s", QKeySequence(sequence).toString())
            return False
        self._filter = _WinHotkeyFilter(self._HOTKEY_ID, self.triggered.emit)
        app = QApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self._filter)
        self._registered = True
        return True

    def unregister(self) -> None:
        if not self._registered:
            return
        self._registered = False
        app = QApplication.instance()
        if app is not None and self._filter is not None:
            app.removeNativeEventFilter(self._filter)
        self._filter = None
        if self._host is not None:
            try:
                _native_unregister(int(self._host.winId()), self._HOTKEY_ID)
            except (OSError, RuntimeError) as e:
                logger.warning("全局热键注销失败: %s", e)

    def dispose(self) -> None:
        """退出时释放（注销热键并销毁宿主窗口）"""
        self.unregister()
        if self._host is not None:
            self._host.deleteLater()
            self._host = None

"""
macOS 动态激活策略

LSUIElement/accessory 应用（本应用打包后不占 Dock）的窗口即使拿到
键盘焦点，也不会接管顶部菜单栏——菜单栏仍停留在上一个 regular 应用，
表现为"切回桌宠/看板后菜单栏没跟着切回来"。

解法：动态切换 NSApplication 激活策略——
- 本应用变为 Active（点击本应用窗口 / 托盘"显示"）→ 临时切到
  regular：菜单栏、Dock、Cmd+Tab 表现与普通应用一致；
- 变为 Inactive（点了其他应用）→ 切回 accessory：Dock 图标消失，
  桌宠/看板继续悬浮（hidesOnDeactivate 已关闭）。

所有调用经 ctypes 走 objc_msgSend，失败只记日志，不影响运行。
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication

from app.config import AppConfig

logger = logging.getLogger(__name__)

# NSApplicationActivationPolicy
_REGULAR = 0
_ACCESSORY = 1

_objc_lib = None
_sel_cache: dict[str, int] = {}
_current_policy: int | None = None


def _objc():
    global _objc_lib
    if _objc_lib is None:
        lib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        lib.sel_registerName.restype = ctypes.c_void_p
        lib.sel_registerName.argtypes = [ctypes.c_char_p]
        lib.objc_getClass.restype = ctypes.c_void_p
        lib.objc_getClass.argtypes = [ctypes.c_char_p]
        _objc_lib = lib
    return _objc_lib


def _send(obj, sel_name: str, *args, argtypes=(),
          restype=ctypes.c_void_p):
    lib = _objc()
    sel = _sel_cache.get(sel_name)
    if sel is None:
        sel = lib.sel_registerName(sel_name.encode())
        _sel_cache[sel_name] = sel
    fn = lib.objc_msgSend
    fn.restype = restype
    fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, *argtypes]
    return fn(obj, sel, *args)


def _nsapp():
    lib = _objc()
    return _send(lib.objc_getClass(b"NSApplication"), "sharedApplication")


def set_activation_policy(policy: int, activate: bool = False) -> bool:
    """设置 NSApp 激活策略；无原生 App（如离屏测试）时返回 False"""
    global _current_policy
    if _current_policy == policy and not activate:
        return True
    try:
        nsapp = _nsapp()
        if not nsapp:
            return False
        _send(nsapp, "setActivationPolicy:", policy,
              argtypes=[ctypes.c_long], restype=None)
        if activate:
            # 已处于激活态时切策略不会自动接管菜单栏，需再激活一次
            _send(nsapp, "activateIgnoringOtherApps:", True,
                  argtypes=[ctypes.c_bool], restype=None)
        _current_policy = policy
        return True
    except Exception:
        logger.exception("设置 NSApp 激活策略失败")
        return False


def activate_app() -> None:
    """让本应用接管菜单栏并前置（accessory 面板点击不会自动激活）"""
    set_activation_policy(_REGULAR, activate=True)


def set_native_appearance(dark: bool) -> bool:
    """让原生窗口部件（对话框标题栏/文件对话框）跟随应用主题深浅色"""
    try:
        lib = _objc()
        name = b"NSAppearanceNameDarkAqua" if dark else b"NSAppearanceNameAqua"
        ns_str = _send(lib.objc_getClass(b"NSString"), "stringWithUTF8String:",
                       ctypes.c_char_p(name), argtypes=[ctypes.c_char_p])
        if not ns_str:
            return False
        appearance = _send(lib.objc_getClass(b"NSAppearance"),
                           "appearanceNamed:", ns_str,
                           argtypes=[ctypes.c_void_p])
        if not appearance:
            return False
        nsapp = _nsapp()
        if not nsapp:
            return False
        _send(nsapp, "setAppearance:", appearance,
              argtypes=[ctypes.c_void_p], restype=None)
        return True
    except Exception:
        logger.exception("设置原生外观失败")
        return False


class _ActivationFilter(QObject):
    """任何鼠标按下都视为用户要与本应用交互 → 接管菜单栏"""

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.MouseButtonPress:
            activate_app()
        return False


def enable_dynamic_activation(qapp: QApplication) -> None:
    """接入 QApplication：状态变化 + 鼠标按下双路径驱动激活策略"""
    if not AppConfig.IS_MACOS:
        return

    def _on_state_changed(state) -> None:
        if state == Qt.ApplicationState.ApplicationActive:
            set_activation_policy(_REGULAR, activate=True)
        elif state == Qt.ApplicationState.ApplicationInactive:
            set_activation_policy(_ACCESSORY)

    qapp.applicationStateChanged.connect(_on_state_changed)
    qapp.installEventFilter(_ActivationFilter(qapp))
    set_activation_policy(_ACCESSORY)   # 启动即不占 Dock（对齐打包配置）

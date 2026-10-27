"""Windows 进程完整性级别（UIPI 的前置判断）

低完整性进程对高完整性窗口的调用会被 UIPI 静默拦掉，`Shell_NotifyIcon`
（托盘图标）就是其中之一：调用失败，但 Qt 的 `QSystemTrayIcon.isVisible()`
仍返回 True——它只知道"我要显示"，不知道外壳拒绝了。

本应用是常驻托盘的小挂件，"以为自己有图标"的代价是整个窗口藏进一个
并不存在的图标里，用户只能去任务管理器救回来。这里提供一个可复用的
判断：当前进程是否达到 Medium（Windows 常规用户进程的默认级别）。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys

MEDIUM_RID = 0x2000        # SECURITY_MANDATORY_MEDIUM_RID
_TOKEN_QUERY = 0x0008
_TOKEN_INTEGRITY_LEVEL = 25


def integrity_rid() -> int | None:
    """当前进程令牌的完整性级别 RID（Low=0x1000 / Medium=0x2000 / High=0x3000）

    非 Windows、或令牌查询失败时返回 None（调用方按"不确定"处理）。
    """
    if sys.platform != "win32":
        return None
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        # 显式声明签名：x64 上 HANDLE 是 64 位，用默认 restype(c_int)
        # 会被截断成无效句柄
        k32.GetCurrentProcess.restype = wt.HANDLE
        adv.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD,
                                         ctypes.POINTER(wt.HANDLE)]
        adv.OpenProcessToken.restype = wt.BOOL
        adv.GetTokenInformation.argtypes = [wt.HANDLE, ctypes.c_int,
                                            ctypes.c_void_p, wt.DWORD,
                                            ctypes.POINTER(wt.DWORD)]
        adv.GetTokenInformation.restype = wt.BOOL

        token = wt.HANDLE()
        if not adv.OpenProcessToken(k32.GetCurrentProcess(), _TOKEN_QUERY,
                                    ctypes.byref(token)):
            return None
        buf = ctypes.create_string_buffer(64)      # TOKEN_MANDATORY_LABEL
        size = wt.DWORD(64)
        if not adv.GetTokenInformation(token, _TOKEN_INTEGRITY_LEVEL,
                                       buf, 64, ctypes.byref(size)):
            return None
        sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        if not sid:
            return None
        # SID 布局：Revision(1) + SubAuthorityCount(1) + Authority(6)
        # + SubAuthority[](4 each)，完整性级别是最后一个子项
        count = ctypes.cast(sid + 1,
                            ctypes.POINTER(ctypes.c_ubyte)).contents.value
        if count < 1:
            return None
        return ctypes.cast(sid + 8 + (count - 1) * 4,
                           ctypes.POINTER(wt.DWORD)).contents.value
    except (OSError, AttributeError, ValueError):
        return None


def at_least_medium() -> bool:
    """当前进程是否达到 Medium 完整性（查询不到时按"是"处理，不误伤）"""
    rid = integrity_rid()
    return rid is None or rid >= MEDIUM_RID

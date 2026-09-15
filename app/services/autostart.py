"""
开机自启动登记：Windows 注册表 Run 键 / macOS LaunchAgent / Linux autostart

三个平台共用同一接口（is_enabled / enable / disable / sync），调用方只面对
get_autostart()。所有方法失败一律返回 False 而不抛异常——自启动是附加功能，
企业策略或权限拒绝写入系统启动项时，不该把设置页一起带崩。

登记项一律落在**当前用户**范围（HKCU / ~/Library/LaunchAgents / ~/.config）：
写系统级位置需要管理员权限，而应用本身是便携的单用户工具。
"""

from __future__ import annotations

import logging
import os
import plistlib
import shlex
import subprocess
import sys
from pathlib import Path

from app.config import AppConfig

logger = logging.getLogger(__name__)

# 登记标识：同时是注册表值名 / plist 文件名 / .desktop 文件名。用 ASCII 且
# 不随界面语言变化——它是系统里的稳定身份，改一次就会留下一条摘不掉的旧
# 启动项（任务管理器里还会多出一条永远失败的记录）
AUTOSTART_ID = "PetBoard"


def launch_command() -> list[str]:
    """启动本应用的命令行（argv 形式）

    打包版 sys.executable 就是应用本体；源码运行时得补上 main.py 的绝对
    路径，否则开机只会拉起一个空转的 Python 解释器。Windows 下优先用
    pythonw.exe：本应用常驻托盘，python.exe 每次开机都弹一个黑色控制台
    窗口，且关掉它等于关掉应用。
    """
    if getattr(sys, "frozen", False):
        return [sys.executable]
    exe = Path(sys.executable)
    if sys.platform == "win32":
        pyw = exe.with_name("pythonw.exe")
        if pyw.exists():
            exe = pyw
    entry = Path(__file__).resolve().parents[2] / "main.py"
    return [str(exe), str(entry)]


class _Autostart:
    """平台实现共用的接口与"登记过期即自愈"的通用逻辑"""

    def _registered(self) -> str | None:
        """系统里已登记的命令；未登记（或读不出来）返回 None"""
        raise NotImplementedError

    def _command(self) -> str:
        """当前应登记的命令（与 _registered 同一种可比较的表示）"""
        raise NotImplementedError

    def is_enabled(self) -> bool:
        return self._registered() is not None

    def enable(self) -> bool:
        raise NotImplementedError

    def disable(self) -> bool:
        raise NotImplementedError

    def sync(self) -> bool:
        """已登记但命令过期（换了 exe 目录 / 换了 Python）时就地刷新

        打包版解压到新目录、或用另一个解释器重跑源码后，旧命令指向不存在
        的路径：启动项还在，却永远起不来，用户只能手动关掉再打开。启动时
        自愈一次，省掉这一步。返回是否真的改写过。
        """
        registered = self._registered()
        if registered is None or registered == self._command():
            return False
        return self.enable()


class _WindowsAutostart(_Autostart):
    """HKCU\\...\\Run 下的一条 REG_SZ（当前用户生效，无需管理员）"""

    KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

    def __init__(self, registry=None):
        # 依赖注入仅为测试：Linux/macOS 上 import winreg 直接 ImportError，
        # 假模块让注册表实现能在任何平台被验证
        if registry is None:
            import winreg
            registry = winreg
        self._reg = registry

    @staticmethod
    def _format(argv: list[str]) -> str:
        """按 Windows 命令行规则加引号（路径含空格时缺引号会被截断）"""
        return subprocess.list2cmdline(argv)

    def _command(self) -> str:
        return self._format(launch_command())

    def _registered(self) -> str | None:
        reg = self._reg
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, self.KEY) as key:
                value, _kind = reg.QueryValueEx(key, AUTOSTART_ID)
        except OSError:
            return None      # 键或值不存在
        return str(value)

    def enable(self) -> bool:
        reg = self._reg
        try:
            with reg.CreateKey(reg.HKEY_CURRENT_USER, self.KEY) as key:
                reg.SetValueEx(key, AUTOSTART_ID, 0, reg.REG_SZ,
                               self._command())
        except OSError as e:
            logger.warning("写入开机自启动失败: %s", e)
            return False
        return True

    def disable(self) -> bool:
        reg = self._reg
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, self.KEY, 0,
                             reg.KEY_SET_VALUE) as key:
                reg.DeleteValue(key, AUTOSTART_ID)
        except FileNotFoundError:
            return True          # 本来就没有：目标状态已达成
        except OSError as e:
            logger.warning("移除开机自启动失败: %s", e)
            return False
        return True


class _MacAutostart(_Autostart):
    """~/Library/LaunchAgents/<id>.plist（登录时由 launchd 拉起）"""

    def __init__(self, path: Path | None = None):
        self._path = path or (Path.home() / "Library" / "LaunchAgents"
                              / f"com.{AUTOSTART_ID.lower()}.plist")

    @staticmethod
    def _join(argv: list[str]) -> str:
        """argv → 单串（仅用于"登记是否过期"的比较）"""
        return "\x00".join(argv)

    def _command(self) -> str:
        return self._join(launch_command())

    def _registered(self) -> str | None:
        try:
            with self._path.open("rb") as fh:
                doc = plistlib.load(fh)
        except (OSError, ValueError):
            return None
        args = doc.get("ProgramArguments") if isinstance(doc, dict) else None
        if not isinstance(args, list) or not args:
            return None
        return self._join([str(a) for a in args])

    def enable(self) -> bool:
        doc = {
            "Label": f"com.{AUTOSTART_ID.lower()}",
            "ProgramArguments": launch_command(),
            "RunAtLoad": True,
            "ProcessType": "Interactive",
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("wb") as fh:
                plistlib.dump(doc, fh)
        except OSError as e:
            logger.warning("写入开机自启动失败: %s", e)
            return False
        return True

    def disable(self) -> bool:
        try:
            self._path.unlink()
        except FileNotFoundError:
            return True
        except OSError as e:
            logger.warning("移除开机自启动失败: %s", e)
            return False
        return True


class _LinuxAutostart(_Autostart):
    """XDG autostart：~/.config/autostart/<id>.desktop"""

    def __init__(self, path: Path | None = None):
        base = os.environ.get("XDG_CONFIG_HOME") or str(
            Path.home() / ".config")
        self._path = path or (Path(base) / "autostart"
                              / f"{AUTOSTART_ID.lower()}.desktop")

    def _command(self) -> str:
        return " ".join(shlex.quote(p) for p in launch_command())

    def _registered(self) -> str | None:
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError:
            return None
        for line in text.splitlines():
            if line.startswith("Exec="):
                return line[len("Exec="):].strip()
        return None

    def enable(self) -> bool:
        text = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={AppConfig.APP_NAME}\n"
            f"Exec={self._command()}\n"
            "Terminal=false\n"
            "X-GNOME-Autostart-enabled=true\n"
        )
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(text, encoding="utf-8")
        except OSError as e:
            logger.warning("写入开机自启动失败: %s", e)
            return False
        return True

    def disable(self) -> bool:
        try:
            self._path.unlink()
        except FileNotFoundError:
            return True
        except OSError as e:
            logger.warning("移除开机自启动失败: %s", e)
            return False
        return True


def get_autostart() -> _Autostart | None:
    """按平台取实现；实现不可用时返回 None（调用方按"设置失败"处理）"""
    try:
        if sys.platform == "win32":
            return _WindowsAutostart()
        if sys.platform == "darwin":
            return _MacAutostart()
        return _LinuxAutostart()
    except Exception as e:      # noqa: BLE001 — 缺模块/无权限都不该崩设置页
        logger.warning("开机自启动功能不可用: %s", e)
        return None

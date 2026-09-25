# -*- coding: utf-8 -*-
"""
开机自启动测试：命令构造 + 三个平台实现

自启动会写系统启动项，测试**绝不碰真实位置**：Windows 走注入的内存假
winreg，macOS/Linux 实现按显式传入的临时路径落盘。命令构造用 patch 造出
"打包版/源码运行"两种形态。
"""

from __future__ import annotations

import os
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PET_BOARD_DATA_DIR"] = tempfile.mkdtemp()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.autostart import (
    AUTOSTART_ID,
    _LinuxAutostart,
    _MacAutostart,
    _WindowsAutostart,
    launch_command,
)


class _FakeKey:
    """winreg 键句柄（只需支持 with）"""

    def __init__(self, values: dict):
        self.values = values

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeWinreg:
    """内存注册表：够 _WindowsAutostart 用的那五个接口"""

    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE = 0x0002
    REG_SZ = 1

    def __init__(self, keys: dict | None = None):
        self.keys = {k: dict(v) for k, v in (keys or {}).items()}   # 键路径 → 值表

    def OpenKey(self, root, path, *args):
        if path not in self.keys:
            raise FileNotFoundError(path)
        return _FakeKey(self.keys[path])

    def CreateKey(self, root, path):
        return _FakeKey(self.keys.setdefault(path, {}))

    def QueryValueEx(self, key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        return key.values[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, kind, value):
        key.values[name] = value

    def DeleteValue(self, key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        del key.values[name]


class _DeniedWinreg(_FakeWinreg):
    """企业策略/权限拒绝写入"""

    def CreateKey(self, root, path):
        raise PermissionError("拒绝访问")


class LaunchCommandTest(unittest.TestCase):
    def test_frozen_uses_executable_only(self):
        """打包版：sys.executable 就是应用本体，不带多余参数"""
        with patch.object(sys, "frozen", True, create=True):
            self.assertEqual(launch_command(), [sys.executable])

    def test_source_run_appends_entry_script(self):
        """源码运行：补上 main.py 绝对路径（否则开机只拉起一个空转解释器）"""
        with patch.object(sys, "frozen", False, create=True):
            argv = launch_command()
        self.assertEqual(len(argv), 2)
        self.assertTrue(argv[1].endswith("main.py"))
        self.assertTrue(Path(argv[1]).is_file())

    @unittest.skipUnless(sys.platform == "win32", "pythonw 仅 Windows")
    def test_windows_prefers_pythonw(self):
        """Windows 源码运行优先 pythonw.exe：不然每次开机弹黑控制台"""
        with patch.object(sys, "frozen", False, create=True):
            self.assertTrue(launch_command()[0].endswith("pythonw.exe"))


class WindowsAutostartTest(unittest.TestCase):
    """HKCU\\...\\Run 下的一条 REG_SZ"""

    def setUp(self):
        self.reg = _FakeWinreg()
        self.svc = _WindowsAutostart(self.reg)

    def _stored(self):
        return self.reg.keys[_WindowsAutostart.KEY].get(AUTOSTART_ID)

    def test_default_off(self):
        self.assertFalse(self.svc.is_enabled())

    def test_enable_disable_roundtrip(self):
        self.assertTrue(self.svc.enable())
        self.assertTrue(self.svc.is_enabled())
        self.assertEqual(self._stored(), self.svc._command())
        self.assertIn("main.py", self._stored())   # 带上入口脚本，不是空转解释器
        self.assertTrue(self.svc.disable())
        self.assertFalse(self.svc.is_enabled())

    def test_disable_when_absent_is_ok(self):
        """本来就没开：关一次不该算失败（目标状态已达成）"""
        self.assertTrue(self.svc.disable())

    def test_command_quotes_paths_with_spaces(self):
        """含空格的路径必须带引号，否则开机命令行会被拆成两段"""
        with patch("app.services.autostart.launch_command",
                   return_value=[r"C:\Program Files\Pet Board\app.exe"]):
            self.assertEqual(self.svc._command(),
                             r'"C:\Program Files\Pet Board\app.exe"')

    def test_sync_rewrites_stale_command(self):
        """登记的命令已过期（换了 exe 目录）→ 自愈改写"""
        self.svc.enable()
        self.reg.keys[_WindowsAutostart.KEY][AUTOSTART_ID] = '"D:\\old\\a.exe"'
        self.assertTrue(self.svc.sync())
        self.assertIn("main.py", self._stored())

    def test_sync_noop_when_absent_or_current(self):
        self.assertFalse(self.svc.sync())        # 未登记：不凭空创建启动项
        self.svc.enable()
        self.assertFalse(self.svc.sync())        # 已是最新：不重复写

    def test_enable_denied_returns_false(self):
        """写注册表被拒：返回 False 而不是抛异常（设置页要能提示并回滚）"""
        svc = _WindowsAutostart(_DeniedWinreg())
        self.assertFalse(svc.enable())
        self.assertFalse(svc.is_enabled())


class MacAutostartTest(unittest.TestCase):
    """~/Library/LaunchAgents/<id>.plist（测试写临时路径）"""

    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / f"{AUTOSTART_ID.lower()}.plist"
        self.svc = _MacAutostart(self.path)

    def test_enable_disable_roundtrip(self):
        self.assertFalse(self.svc.is_enabled())
        self.assertTrue(self.svc.enable())
        self.assertTrue(self.svc.is_enabled())
        with self.path.open("rb") as fh:
            doc = plistlib.load(fh)
        self.assertEqual(doc["ProgramArguments"], launch_command())
        self.assertTrue(doc["RunAtLoad"])
        self.assertTrue(self.svc.disable())
        self.assertFalse(self.svc.is_enabled())

    def test_disable_when_absent_is_ok(self):
        self.assertTrue(self.svc.disable())

    def test_corrupt_plist_reads_as_disabled(self):
        """plist 损坏（手工编辑坏）按未开启处理，不抛异常"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{ 不是 plist", encoding="utf-8")
        self.assertFalse(self.svc.is_enabled())

    def test_sync_rewrites_stale_arguments(self):
        self.svc.enable()
        self.path.write_bytes(plistlib.dumps(
            {"Label": "x", "ProgramArguments": ["/old/app"]}))
        self.assertTrue(self.svc.sync())
        with self.path.open("rb") as fh:
            self.assertEqual(plistlib.load(fh)["ProgramArguments"],
                             launch_command())


class LinuxAutostartTest(unittest.TestCase):
    """XDG autostart：~/.config/autostart/<id>.desktop"""

    def setUp(self):
        base = Path(tempfile.mkdtemp())
        self.path = base / f"{AUTOSTART_ID.lower()}.desktop"
        self.svc = _LinuxAutostart(self.path)

    def test_enable_disable_roundtrip(self):
        self.assertFalse(self.svc.is_enabled())
        self.assertTrue(self.svc.enable())
        self.assertTrue(self.svc.is_enabled())
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("[Desktop Entry]", text)
        self.assertIn("Terminal=false", text)
        self.assertIn("X-GNOME-Autostart-enabled=true", text)
        self.assertIn(f"Exec={self.svc._command()}", text)
        self.assertTrue(self.svc.disable())
        self.assertFalse(self.svc.is_enabled())

    def test_sync_rewrites_stale_exec(self):
        self.svc.enable()
        self.path.write_text("[Desktop Entry]\nExec=/old/app\n",
                             encoding="utf-8")
        self.assertTrue(self.svc.sync())
        self.assertIn("main.py", self.path.read_text(encoding="utf-8"))

    def test_file_without_exec_counts_as_disabled(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("[Desktop Entry]\nName=x\n", encoding="utf-8")
        self.assertFalse(self.svc.is_enabled())


class FactoryTest(unittest.TestCase):
    def test_get_autostart_returns_platform_impl(self):
        from app.services.autostart import get_autostart
        svc = get_autostart()
        self.assertIsNotNone(svc)
        self.assertTrue(hasattr(svc, "enable") and hasattr(svc, "disable"))

    def test_unavailable_impl_returns_none(self):
        """实现不可用（缺模块/无权限）返回 None，调用方按"设置失败"处理"""
        from app.services import autostart as mod
        with patch.object(mod.sys, "platform", "win32"), \
             patch.object(mod, "_WindowsAutostart",
                          side_effect=ImportError("no winreg")):
            self.assertIsNone(mod.get_autostart())


if __name__ == "__main__":
    unittest.main()

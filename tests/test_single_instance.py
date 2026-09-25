# -*- coding: utf-8 -*-
"""
单实例唤醒通道测试：第二实例 wake() → 首实例收到 show 请求（Qt 信号）

用法：QT_QPA_PLATFORM=offscreen python tests/test_single_instance.py
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PET_BOARD_DATA_DIR"] = tempfile.mkdtemp()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint  # noqa: F401  (占位避免 import 空置)
from PySide6.QtTest import QTest

_qapp = None

from app.services.single_instance import InstanceWaker


def _app():
    global _qapp
    if _qapp is None:
        from PySide6.QtWidgets import QApplication
        _qapp = QApplication.instance() or QApplication([])
    return _qapp


@unittest.skipUnless(hasattr(__import__("socket"), "AF_UNIX"),
                     "本平台无 AF_UNIX，唤醒通道降级为弹窗")
class InstanceWakerTest(unittest.TestCase):
    def setUp(self):
        _app()
        self.tmp = tempfile.TemporaryDirectory()
        self.waker = InstanceWaker(Path(self.tmp.name))
        self.received = []
        self.waker.signal_show_requested.connect(
            lambda: self.received.append(1))

    def tearDown(self):
        self.tmp.cleanup()

    def test_wake_delivers_show_request(self):
        """第二实例 wake → 首实例信号触发（跨线程排队到主线程）"""
        self.waker.start()
        self.assertTrue(self.waker.wake())
        QTest.qWait(300)               # 等监听线程 accept + 信号排队
        self.assertEqual(self.received, [1])

    def test_wake_repeated_requests(self):
        """重复双击：每次都送达（监听循环持续 accept）"""
        self.waker.start()
        for _ in range(3):
            self.assertTrue(self.waker.wake())
        QTest.qWait(400)
        self.assertEqual(len(self.received), 3)

    def test_wake_without_listener_returns_false(self):
        """首实例未运行（无监听）：wake 失败，调用方退回提示框路径"""
        self.assertFalse(self.waker.wake())
        self.assertEqual(self.received, [])

    def test_stale_socket_file_cleaned_on_start(self):
        """崩溃残留的 socket 文件不妨碍重启后重新监听"""
        stale = Path(self.tmp.name) / "instance.sock"
        stale.write_bytes(b"")            # 伪造残留
        self.waker.start()                # start 内部 unlink 后重绑
        self.assertTrue(self.waker.wake())
        QTest.qWait(300)
        self.assertEqual(self.received, [1])


if __name__ == "__main__":
    unittest.main(verbosity=2)

# -*- coding: utf-8 -*-
"""托盘可用性判断：低完整性进程注册不了图标，不能再当"托盘可用"

窗口 ✕ 靠 `is_available()` 决定"藏进托盘"还是"折叠/最小化"。若把
"注册不上图标"误判成可用，应用会藏进一个不存在的图标里，只能去任务
管理器救回来。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PET_BOARD_DATA_DIR"] = tempfile.mkdtemp()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.platform.win_integrity import at_least_medium, integrity_rid  # noqa: E402
from app.services import tray_service  # noqa: E402
from app.services.tray_service import TrayService  # noqa: E402

_qapp = None


def _app():
    global _qapp
    if _qapp is None:
        _qapp = QApplication.instance() or QApplication([])
    return _qapp


class TrayAvailabilityTest(unittest.TestCase):
    def setUp(self):
        _app()

    @mock.patch.object(tray_service, "tray_icon_registrable",
                       return_value=False)
    def test_unavailable_when_icon_cannot_register(self, _mock):
        """图标注册不上时 is_available 必须为 False（否则 ✕ 会藏进虚空）"""
        tray = TrayService()
        try:
            self.assertFalse(tray.is_available())
        finally:
            tray.hide()

    def test_registrable_checks_integrity(self):
        """完整性查询要能真的跑通：非 Windows 视为不受限，Windows 返回 RID"""
        rid = integrity_rid()
        if sys.platform == "win32":
            self.assertIsInstance(rid, int)
            self.assertEqual(at_least_medium(), rid >= 0x2000)
        else:
            self.assertIsNone(rid)
            self.assertTrue(at_least_medium())


if __name__ == "__main__":
    unittest.main(verbosity=2)

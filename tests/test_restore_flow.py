# -*- coding: utf-8 -*-
"""恢复流程测试：备份副本本身损坏时不重复固化 / 不重复弹窗

`_offer_restore` 只负责询问与尝试恢复，失败收尾（固化好副本 + 提示 +
空板落盘）由调用方统一做。此前的实现内部也会收尾一次，调用方再补一次，
导致同一事故多存一份好副本、多弹一个错误框。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PET_BOARD_DATA_DIR"] = tempfile.mkdtemp()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QMessageBox

from app.controllers.app_controller import AppController
from app.models.board import BoardStore


class RestoreFlowTest(unittest.TestCase):
    def _controller_with(self, prev_content: str | None) -> AppController:
        tmp = Path(tempfile.mkdtemp())
        path = tmp / "board.json"
        path.write_text("{}", encoding="utf-8")
        prev = tmp / "board.json.prev"
        if prev_content is None:
            prev.unlink(missing_ok=True)
        else:
            prev.write_text(prev_content, encoding="utf-8")
        c = AppController.__new__(AppController)   # 不跑重量级构造
        c._store = BoardStore(path)
        c._window = None
        return c

    def test_failed_restore_returns_none_without_side_effects(self):
        c = self._controller_with("{broken json")
        with patch.object(QMessageBox, "question",
                          return_value=QMessageBox.Yes), \
                patch.object(c, "_preserve_good_copy") as preserve, \
                patch.object(c, "_show_error") as show_error:
            result = c._offer_restore(["corrupted"])
        self.assertIsNone(result)
        preserve.assert_not_called()
        show_error.assert_not_called()

    def test_declined_restore_returns_false_without_side_effects(self):
        c = self._controller_with("{}")
        with patch.object(QMessageBox, "question",
                          return_value=QMessageBox.No), \
                patch.object(c, "_preserve_good_copy") as preserve, \
                patch.object(c, "_show_error") as show_error:
            result = c._offer_restore(["corrupted"])
        self.assertIs(result, False)
        preserve.assert_not_called()
        show_error.assert_not_called()

# -*- coding: utf-8 -*-
"""
json_io 工具单测：非 UTF-8 文件隔离、恢复不污染 .prev 槽位

用法：python tests/test_json_io.py
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["PET_BOARD_DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import json_io


class NonUtf8FileTest(unittest.TestCase):
    """回归：非 UTF-8 字节不得让 load_json_doc 冒出 UnicodeDecodeError"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_non_utf8_file_quarantined(self):
        """解码异常曾发生在 read_text 内部（只捕 OSError），直接冒到调用方

        启动路径上会让控制器构造崩溃（走"启动失败"兜底框），而不是按
        设计隔离备份 + 空数据启动。
        """
        bad = self.dir / "bad.json"
        # GBK 编码的汉字不是合法 UTF-8 字节序列
        bad.write_bytes('{"name": "测试"}'.encode("gbk"))

        problems = []
        doc = json_io.load_json_doc(
            bad, on_problem=lambda kind, _p, _d: problems.append(kind))

        self.assertEqual(doc, {})
        self.assertEqual(problems, ["corrupted"])
        self.assertTrue(list(self.dir.glob("bad.json.corrupt.*.bak")))

    def test_valid_file_unaffected(self):
        ok = self.dir / "ok.json"
        ok.write_text(json.dumps({"v": 1}), encoding="utf-8")
        self.assertEqual(json_io.load_json_doc(ok), {"v": 1})


class RestoreDoesNotPollutePrevTest(unittest.TestCase):
    """回归：restore_from_backup 不得把坏主文件轮转进 .prev 槽位"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_prev_slot_survives_restore(self):
        store = self.dir / "b.json"
        store.write_text("{ broken", encoding="utf-8")     # 坏主文件
        prev = self.dir / "b.json.prev"
        prev.write_text(json.dumps({"v": "prev"}), encoding="utf-8")
        snap = self.dir / "b.json.snap.1.bak"
        snap.write_text(json.dumps({"v": "snap"}), encoding="utf-8")

        self.assertTrue(json_io.restore_from_backup(store, snap))

        # 主文件 = 恢复内容；.prev 仍是原来的好副本（未被坏文件覆盖）
        self.assertEqual(json.loads(store.read_text("utf-8")), {"v": "snap"})
        self.assertEqual(json.loads(prev.read_text("utf-8")), {"v": "prev"})


if __name__ == "__main__":
    unittest.main(verbosity=2)

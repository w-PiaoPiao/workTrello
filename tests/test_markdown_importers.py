# -*- coding: utf-8 -*-
"""
Markdown 轻渲染与外部格式导入（Trello JSON / Markdown）单元测试

纯函数测试，不依赖 Qt 事件循环。
用法：python tests/test_markdown_importers.py
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["PET_BOARD_DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.board import Card
from app.models.importers import board_from_markdown, board_from_trello
from app.models.markdown_lite import render_markdown


class MarkdownLiteTest(unittest.TestCase):
    def test_headings_bold_strike_code(self):
        html = render_markdown("# 大标题\n## 小标题\n**粗** *斜* ~~删~~ `code`")
        self.assertIn("<b><big>大标题</big></b>", html)
        self.assertIn("<b>小标题</b>", html)
        self.assertIn("<b>粗</b>", html)
        self.assertIn("<i>斜</i>", html)
        self.assertIn("<s>删</s>", html)
        self.assertIn("<code>code</code>", html)

    def test_todo_and_bullets(self):
        html = render_markdown("- [ ] 待办项\n- [x] 已办项\n- 普通项")
        self.assertIn("☐ 待办项", html)
        self.assertIn("☑ <s>已办项</s>", html)
        self.assertIn("• 普通项", html)

    def test_links_and_escape(self):
        html = render_markdown("[官网](https://example.com) 与 <script>")
        self.assertIn('<a href="https://example.com">官网</a>', html)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_bare_url_and_newlines_preserved(self):
        html = render_markdown("第一行\nhttps://a.b/c\n第三行")
        self.assertIn('<a href="https://a.b/c">', html)
        self.assertIn("第一行<br>", html)

    def test_empty(self):
        self.assertEqual(render_markdown("   "), "")

    def test_quote_styled(self):
        html = render_markdown("> 引用一句", secondary_color="#888888")
        self.assertIn('color:#888888', html)
        self.assertIn("<i>", html)


class TrelloImportTest(unittest.TestCase):
    def _doc(self) -> dict:
        return {
            "name": "Trello 板",
            "lists": [
                {"id": "L1", "name": "To Do", "closed": False},
                {"id": "L2", "name": "Done", "closed": False},
                {"id": "L3", "name": "垃圾桶", "closed": True},
            ],
            "cards": [
                {"id": "C1", "name": "任务一", "desc": "说明文字",
                 "idList": "L1", "closed": False,
                 "due": "2026-09-20T10:00:00.000Z", "dueComplete": False,
                 "labels": [{"color": "red"}, {"color": "sky"},
                            {"color": "unknown"}]},
                {"id": "C2", "name": "已完成卡", "desc": "",
                 "idList": "L2", "closed": False,
                 "due": "2026-09-01T00:00:00.000Z", "dueComplete": True,
                 "labels": []},
                {"id": "C3", "name": "归档卡", "desc": "",
                 "idList": "L1", "closed": True, "labels": []},
                {"id": "C4", "name": "孤儿卡", "desc": "",
                 "idList": "L3", "closed": False, "labels": []},
            ],
            "checklists": [
                {"idCard": "C1", "name": "步骤",
                 "checkItems": [{"name": "第一步", "state": "complete"},
                                {"name": "第二步", "state": "incomplete"}]},
            ],
        }

    def test_basic_mapping(self):
        board, report = board_from_trello(self._doc())
        self.assertEqual(board.name, "Trello 板")
        self.assertEqual([l.title for l in board.lists], ["To Do", "Done"])
        self.assertEqual(board.lists[0].cards[0].title, "任务一")
        c1 = board.lists[0].cards[0]
        self.assertEqual(c1.notes, "说明文字")
        self.assertEqual(c1.due_date, "2026-09-20")
        # 标签映射：red→red, sky→blue, unknown 丢弃
        self.assertEqual(c1.labels, ["red", "blue"])
        self.assertIn("列表 2", report)

    def test_checklist_and_done(self):
        board, _ = board_from_trello(self._doc())
        c1 = board.lists[0].cards[0]
        self.assertEqual(c1.checklist,
                         [{"text": "第一步", "done": True},
                          {"text": "第二步", "done": False}])
        c2 = board.lists[1].cards[0]
        self.assertTrue(c2.done)

    def test_closed_card_archived_and_closed_list_dropped(self):
        board, _ = board_from_trello(self._doc())
        archived = [c for l in board.lists for c in l.cards if c.archived]
        self.assertEqual([c.title for c in archived], ["归档卡"])
        # 孤儿卡（挂在 closed 列表）不丢数据，落到最后一个有效列表
        titles = [c.title for l in board.lists for c in l.cards]
        self.assertIn("孤儿卡", titles)

    def test_invalid_doc_raises(self):
        with self.assertRaises(ValueError):
            board_from_trello({"foo": 1})
        with self.assertRaises(ValueError):
            board_from_trello("not a dict")   # type: ignore[arg-type]


class MarkdownImportTest(unittest.TestCase):
    def test_app_export_format_roundtrip(self):
        text = "\n".join([
            "# 看板导出（2026-09-13）",
            "",
            "## 待办",
            "",
            "- [ ] 买牛奶",
            "- [x] 交周报",
            "      备注第一行",
            "      备注第二行",
            "",
            "## 进行中",
            "",
            "- [ ] 写代码",
        ])
        board, report = board_from_markdown(text, fallback_title="恢复")
        self.assertEqual([l.title for l in board.lists], ["待办", "进行中"])
        todo = board.lists[0]
        self.assertEqual([c.title for c in todo.cards], ["买牛奶", "交周报"])
        self.assertFalse(todo.cards[0].done)
        self.assertTrue(todo.cards[1].done)
        self.assertEqual(todo.cards[1].notes, "备注第一行\n备注第二行")

    def test_generic_task_list(self):
        text = "## 今日\n* 普通星号条目\n- [ ] 方括号条目"
        board, _ = board_from_markdown(text)
        self.assertEqual([c.title for c in board.lists[0].cards],
                         ["普通星号条目", "方括号条目"])

    def test_unrecognized_raises(self):
        with self.assertRaises(ValueError):
            board_from_markdown("随便一段没有结构的文字")


if __name__ == "__main__":
    unittest.main(verbosity=2)

# -*- coding: utf-8 -*-
"""速记语法解析测试：日期 / 优先级 / 标签 / 混合 / 未命中保留

速记语法见 app/models/quick_syntax.py；测试固定 today 以覆盖星期映射
的边界（周一当天、周六说"周一"等）。
"""

import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("PET_BOARD_DATA_DIR", tempfile.mkdtemp())

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.quick_syntax import parse_quick_input

MONDAY = date(2026, 9, 7)      # 周一：区分"周一"与"下周一"
SATURDAY = date(2026, 9, 12)   # 周六


class RelativeDayTest(unittest.TestCase):
    def test_plain_title_untouched(self):
        title, fields = parse_quick_input("写周报", today=MONDAY)
        self.assertEqual(title, "写周报")
        self.assertEqual(fields, {})

    def test_relative_words(self):
        cases = {"今天": 0, "今日": 0, "明天": 1, "明日": 1, "后天": 2}
        for word, offset in cases.items():
            _, f = parse_quick_input(f"{word}汇报", today=MONDAY)
            self.assertEqual(f["due_date"],
                             (MONDAY + timedelta(days=offset)).isoformat(),
                             word)

    def test_n_days_later(self):
        _, f = parse_quick_input("3天后复盘", today=MONDAY)
        self.assertEqual(f["due_date"], "2026-09-10")
        title, _ = parse_quick_input("复盘 3 天后", today=MONDAY)
        self.assertEqual(title, "复盘")

    def test_weekday_nearest_future_includes_today(self):
        # 周一说"周一" = 今天
        _, f = parse_quick_input("周一例会", today=MONDAY)
        self.assertEqual(f["due_date"], "2026-09-07")
        # 周六说"周一" = 未来最近的周一
        _, f = parse_quick_input("周一开会", today=SATURDAY)
        self.assertEqual(f["due_date"], "2026-09-14")
        # 周六说"周五" = 下周五
        _, f = parse_quick_input("周五交报告", today=SATURDAY)
        self.assertEqual(f["due_date"], "2026-09-18")
        # 周六说"周日/周日" = 明天
        _, f = parse_quick_input("周日散步", today=SATURDAY)
        self.assertEqual(f["due_date"], "2026-09-13")

    def test_next_weekday_is_next_calendar_week(self):
        # 周一说"下周一" = 下周一（而非今天）
        _, f = parse_quick_input("下周一路演", today=MONDAY)
        self.assertEqual(f["due_date"], "2026-09-14")
        self.assertEqual(parse_quick_input("路演", today=MONDAY)[0], "路演")
        title, f = parse_quick_input("下周二冲刺", today=MONDAY)
        self.assertEqual(f["due_date"], "2026-09-15")
        self.assertEqual(title, "冲刺")
        # "下周五" 不被 "周X" 抢先
        _, f = parse_quick_input("下周五聚餐", today=MONDAY)
        self.assertEqual(f["due_date"], "2026-09-18")

    def test_full_and_month_day_dates(self):
        _, f = parse_quick_input("上线 2026-10-01", today=MONDAY)
        self.assertEqual(f["due_date"], "2026-10-01")
        _, f = parse_quick_input("9月20日交房", today=MONDAY)
        self.assertEqual(f["due_date"], "2026-09-20")
        _, f = parse_quick_input("9/20 体检", today=MONDAY)
        self.assertEqual(f["due_date"], "2026-09-20")
        # 无效日期：token 原样保留，不产出字段
        title, f = parse_quick_input("2月30日Birthday", today=MONDAY)
        self.assertEqual(f, {})
        self.assertIn("2月30日", title)


class PriorityAndTagTest(unittest.TestCase):
    def test_priority(self):
        for token, value in (("!P1", 1), ("!p2", 2), ("!P3", 3)):
            title, f = parse_quick_input(f"修bug {token}", today=MONDAY)
            self.assertEqual(f["priority"], value, token)
            self.assertEqual(title, "修bug")

    def test_hash_tag_by_chinese_name_prefix(self):
        _, f = parse_quick_input("海报 #红", today=MONDAY)
        self.assertEqual(f["labels"], ["red"])
        _, f = parse_quick_input("海报 #红色", today=MONDAY)
        self.assertEqual(f["labels"], ["red"])
        _, f = parse_quick_input("海报 #blue", today=MONDAY)
        self.assertEqual(f["labels"], ["blue"])

    def test_hash_tag_dedup_and_unknown_kept(self):
        _, f = parse_quick_input("任务 #红 #红色", today=MONDAY)
        self.assertEqual(f["labels"], ["red"])
        # 未命中色名的 # 片段原样保留
        title, f = parse_quick_input("学习 #话题标签", today=MONDAY)
        self.assertEqual(f, {})
        self.assertIn("#话题标签", title)


class CombinedTest(unittest.TestCase):
    def test_all_fields(self):
        title, f = parse_quick_input("周五评审前完成初稿 !P1 #蓝 #红",
                                     today=SATURDAY)
        self.assertEqual(title, "评审前完成初稿")
        self.assertEqual(f["due_date"], "2026-09-18")
        self.assertEqual(f["priority"], 1)
        self.assertEqual(f["labels"], ["blue", "red"])

    def test_first_due_date_wins(self):
        _, f = parse_quick_input("明天做 今天也行", today=MONDAY)
        self.assertEqual(f["due_date"], "2026-09-08")

    def test_all_tokens_yields_empty_title(self):
        # 整行全是语法 token：标题为空，由调用方回退原文
        title, f = parse_quick_input("#红 !P1 今天", today=MONDAY)
        self.assertEqual(title, "")
        self.assertEqual(f["due_date"], "2026-09-07")
        self.assertEqual(f["priority"], 1)

    def test_weekday_word_is_consumed_from_title(self):
        # 设计决策：标题里的星期词按日期语法消费（速记以录入速度优先），
        # 极端情形（"周五"是人名）请走编辑对话框设置字段
        title, _ = parse_quick_input("和周五谈需求", today=MONDAY)
        self.assertEqual(title, "和 谈需求")


if __name__ == "__main__":
    unittest.main(verbosity=2)

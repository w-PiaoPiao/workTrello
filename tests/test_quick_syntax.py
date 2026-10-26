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


class EnglishShorthandTest(unittest.TestCase):
    """英文速记：与界面语言无关，恒可用"""

    def setUp(self):
        self.today = date(2026, 9, 12)   # 周六

    def test_relative_words(self):
        t, f = parse_quick_input("standup tomorrow", self.today)
        self.assertEqual(t, "standup")
        self.assertEqual(f["due_date"], "2026-09-13")
        t, f = parse_quick_input("today review", self.today)
        self.assertEqual(f["due_date"], "2026-09-12")
        t, f = parse_quick_input("day after tomorrow ship", self.today)
        self.assertEqual(f["due_date"], "2026-09-14")

    def test_in_n_days(self):
        t, f = parse_quick_input("sync in 3 days", self.today)
        self.assertEqual(t, "sync")
        self.assertEqual(f["due_date"], "2026-09-15")

    def test_weekday_and_next(self):
        # 周六(9/12)：fri → 下周五 9/18
        t, f = parse_quick_input("demo fri", self.today)
        self.assertEqual(f["due_date"], "2026-09-18")
        # next mon → 下周一 9/14
        t, f = parse_quick_input("kickoff next monday", self.today)
        self.assertEqual(t, "kickoff")
        self.assertEqual(f["due_date"], "2026-09-14")
        # 全名 saturday → 今天（含今天最近）
        t, f = parse_quick_input("saturday fun", self.today)
        self.assertEqual(f["due_date"], "2026-09-12")

    def test_no_false_positive_on_plain_english(self):
        t, f = parse_quick_input("read saturate docs", self.today)
        # "saturate" 不应被当作 saturday 命中
        self.assertNotIn("due_date", f)
        self.assertEqual(t, "read saturate docs")

    def test_uppercase_weekday_does_not_crash(self):
        # 回归：正则 IGNORECASE 但 group 保留原大小写，曾 KeyError("Mon")
        for text in ("Monday meeting", "Next Monday", "NEXT MONDAY"):
            t, f = parse_quick_input(text, self.today)
            self.assertIn("due_date", f)
        t, f = parse_quick_input("Monday meeting", self.today)
        self.assertEqual(t, "meeting")
        self.assertEqual(f["due_date"], "2026-09-14")

    def test_ambiguous_abbreviation_needs_whole_line(self):
        # sat/sun/wed 是常见英文单词，不能静默从正文里吞掉
        for text in ("I sat down", "sun exposure", "we wed in June"):
            t, f = parse_quick_input(text, self.today)
            self.assertEqual(t, text)
            self.assertEqual(f, {})
        # 整行缩写仍识别；mon/tue/thu/fri 可夹在句中
        self.assertEqual(parse_quick_input("wed", self.today)[1]["due_date"],
                         "2026-09-16")
        self.assertEqual(parse_quick_input("demo thu", self.today)[1]["due_date"],
                         "2026-09-17")

    def test_priority_and_label_english(self):
        t, f = parse_quick_input("fix bug !P1 #red", self.today)
        self.assertEqual(t, "fix bug")
        self.assertEqual(f["priority"], 1)
        self.assertEqual(f["labels"], ["red"])


class SlashDateBoundaryTest(unittest.TestCase):
    """回归：斜杠 M/D 与分数同形，紧贴汉字的分数不再被吞成截止日"""

    def test_fraction_tight_text_not_due(self):
        title, f = parse_quick_input("进度1/2的人", today=MONDAY)
        self.assertNotIn("due_date", f)
        self.assertIn("1/2", title)

    def test_fraction_space_separated_still_ambiguous_kept_tight_only(self):
        # 紧贴场景（正文分数）被排除；位于行首/行尾的斜杠日期仍然识别
        _, f = parse_quick_input("9/20 体检", today=MONDAY)
        self.assertEqual(f["due_date"], "2026-09-20")

    def test_fraction_mid_sentence_not_due(self):
        """回归："完成 3/4 后交付" 的分数不能被静默吞成截止日"""
        title, f = parse_quick_input("完成 3/4 后交付", today=MONDAY)
        self.assertNotIn("due_date", f)
        self.assertIn("3/4", title)

    def test_month_day_tight_with_text_still_parsed(self):
        # 今年已过 → 滚到明年：写"3月4日"指即将到来的 3 月 4 日，
        # 按今年解析会让卡片创建即"已逾期"
        _, f = parse_quick_input("项目3月4日截止", today=MONDAY)
        self.assertEqual(f["due_date"], "2027-03-04")

    def test_past_month_day_rolls_to_next_year(self):
        _, f = parse_quick_input("1月5日交材料", today=MONDAY)
        self.assertEqual(f["due_date"], "2027-01-05")


class WordBoundaryTest(unittest.TestCase):
    """回归：前缀词与英文词边界"""

    def test_last_and_every_weekday_kept_as_text(self):
        for s in ("上周三开的会", "每周三例会"):
            title, f = parse_quick_input(s, today=MONDAY)
            self.assertNotIn("due_date", f)
            self.assertEqual(title, s)

    def test_english_relative_word_needs_boundary(self):
        title, f = parse_quick_input("Todays standup notes", today=MONDAY)
        self.assertNotIn("due_date", f)
        self.assertEqual(title, "Todays standup notes")


class ExplicitYearAndLeapTest(unittest.TestCase):
    """回归：显式年份 / 平年 2 月 29 日"""

    def test_explicit_year_is_respected(self):
        """「2027年3月5日」按 2027 年解析，不滚动也不残留年份"""
        title, f = parse_quick_input("2027年3月5日交材料", today=MONDAY)
        self.assertEqual(f["due_date"], "2027-03-05")
        self.assertEqual(title, "交材料")

    def test_explicit_year_kept_when_equals_next_year(self):
        # 今年已过的日期写了显式明年：按写出的年份，不重复滚动
        title, f = parse_quick_input("2027年1月5日交材料", today=MONDAY)
        self.assertEqual(f["due_date"], "2027-01-05")
        self.assertEqual(title, "交材料")

    def test_feb29_in_plain_year_rolls_to_next_leap_year(self):
        """平年（2027 无 2/29）写「2月29日」滚到下一个闰年而非放弃解析"""
        title, f = parse_quick_input("2月29日交材料", today=date(2027, 6, 1))
        self.assertEqual(f["due_date"], "2028-02-29")
        self.assertEqual(title, "交材料")

    def test_impossible_date_stays_text(self):
        # 2月30日哪年都不存在：不解析、原文保留
        title, f = parse_quick_input("2月30日交材料", today=MONDAY)
        self.assertNotIn("due_date", f)
        self.assertEqual(title, "2月30日交材料")


if __name__ == "__main__":
    unittest.main(verbosity=2)

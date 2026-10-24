"""快速录入速记语法：在标题行内直接写截止日 / 优先级 / 标签

适用场景：快速添加、批量添加等"一行文字即一张卡"的录入路径——此前给
卡片设截止日要打开编辑对话框点日期选择器，速记把高频字段压进标题一行
写完（编辑对话框保持明确字段输入，不应用语法）。

语法（解析出的片段从标题剔除；中英两种语法恒可用，与界面语言无关）：
- 截止日（中）：今天 明天 后天 | N天后 | 周X/星期X/礼拜X（未来最近一天，
  含今天；"上周X/每周X"等前缀词不解析，保留原文）| 下周X（下周对应日）|
  M月D日 / M/D（今年已过则明年）| YYYY-MM-DD
- 截止日（英）：today / tomorrow / day after tomorrow | in N days |
  next mon…（缩写或全名）| mon/tue/…/fri…（未来最近一天）| M/D | YYYY-MM-DD
- 优先级：!P1 !P2 !P3（大小写均可，1=高 3=低）
- 标签：#红 #红色 #blue（LABEL_NAMES 中文名可只写前缀，或英文 key），
  去重

未命中的 token 原样保留在标题里（用户可能真的想写 # 号、"周五"是人名）。
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from app.config import AppConfig

_WEEKDAY_CH = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4,
               "六": 5, "日": 6, "天": 6}

# 相对日词 → 偏移天数
_RELATIVE_DAYS = {"今天": 0, "今日": 0, "明天": 1, "明日": 1, "后天": 2}
# 英文相对词（长词优先："day after tomorrow" 先于 "tomorrow" 判定）
_RELATIVE_DAYS_EN = {"day after tomorrow": 2, "today": 0, "tomorrow": 1}
_WEEKDAY_EN = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4,
               "sat": 5, "sun": 6}

_RE_N_DAYS = re.compile(r"(?<![0-9])(\d{1,3})\s*天后")
_RE_IN_N_DAYS = re.compile(r"\bin\s+(\d{1,3})\s*days?\b", re.IGNORECASE)
_RE_FULL_DATE = re.compile(r"(?<![0-9])(\d{4})-(\d{1,2})-(\d{1,2})(?![0-9])")
# "M月D日" 已是强意图信号，允许紧贴汉字（"项目3月4日截止"）；
# 斜杠形式与分数/比例同形（"进度1/2的人"），要求前面是行首或空白，
# 且斜杠两侧不写空格——避免把正文里的分数静默吞成截止日
# 可选年份组：用户显式写的年份（"2026年3月5日"）按该年解析且不滚动；
# 不识别年份时该组为 None，只剩「2026年」残留在标题里、日期被静默滚一年
_RE_MD = re.compile(
    r"(?<![0-9])(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?(?![0-9])")
# 斜杠形式还要求位于行首或行尾（"日期习惯位置"）：句中斜杠一律当
# 正文——"完成 3/4 后交付"的分数宁可漏识别（原文可见），也不能被
# 静默吞掉（卡片凭空多出逾期截止日）
_RE_MD_SLASH = re.compile(r"(?<!\S)(\d{1,2})/(\d{1,2})(?![0-9])")
_RE_PRIORITY = re.compile(r"(?<![A-Za-z])![pP]([123])\b")
_RE_TAG = re.compile(r"#([\u4e00-\u9fa5A-Za-z]+)")
_RE_NEXT_WEEKDAY = re.compile(r"下(?:周|星期|礼拜)([一二三四五六日天])")
# "上周三/每周三/本周三"等前缀词不是"未来最近一天"语义（前者指过去，
# 后者是周期性）：负向后视排除，保留原文不解析
_RE_WEEKDAY = re.compile(
    r"(?<![上每单双本这])(?:周|星期|礼拜)([一二三四五六日天])")
# 英文相对词带词边界："Todays standup" 不能被 today 切掉词尾
_RE_RELATIVE_EN = re.compile(
    r"\b(day after tomorrow|tomorrow|today)\b", re.IGNORECASE)
# 英文周几：三字母缩写或全名（monday/tuesday/…/sunday），尾 \b 防
# "saturate" 之类普通单词误命中
_RE_NEXT_WEEKDAY_EN = re.compile(
    r"\bnext\s+(mon|tue|wed|thu|fri|sat|sun)[a-z]*\b", re.IGNORECASE)
_RE_WEEKDAY_EN = re.compile(
    r"\b(mon|tue|wed|thu|fri|sat|sun)(?:day|sday|nesday|rsday|urday)?\b",
    re.IGNORECASE)


def _iso(d: date) -> str:
    return d.isoformat()


def _cut(text: str, m: re.Match) -> str:
    """剔除命中片段并以空格衔接，防止前后词粘连"""
    return text[:m.start()] + " " + text[m.end():]


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _future_year_date(month: int, day: int, today: date) -> date | None:
    """M月D日 / M/D：今年该日已过则滚到明年

    用户写"1月5日交材料"指的是即将到来的 1 月 5 日，不是八个多月前
    的过去日期（按今年解析会让卡片创建即"已逾期"并进今日聚焦）。
    今年不存在该日期（平年 2/29）时尝试明年；两年都不存在（2/30）
    才放弃解析。
    """
    d = _safe_date(today.year, month, day)
    if d is None:
        return _safe_date(today.year + 1, month, day)
    if d < today:
        return _safe_date(today.year + 1, month, day) or d
    return d


def _parse_due(text: str, today: date) -> tuple[str, str | None]:
    """截止日语法：返回 (剩余文本, ISO 日期或 None)；长词优先、首个生效"""
    # 相对词按文本位置取最先出现（"明天做 今天也行"→明天生效，而非 dict 序）
    hits = [(text.find(w), w, offset)
            for w, offset in _RELATIVE_DAYS.items() if w in text]
    if hits:
        _, word, offset = min(hits, key=lambda x: x[0])
        return text.replace(word, " ", 1), _iso(
            today + timedelta(days=offset))
    m = _RE_N_DAYS.search(text)
    if m:
        return _cut(text, m), _iso(today + timedelta(days=int(m.group(1))))
    m = _RE_FULL_DATE.search(text)
    if m:
        d = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return (_cut(text, m), _iso(d)) if d else (text, None)
    # "下周X" 必须先于 "周X" 匹配（后者是前者的子串）
    m = _RE_NEXT_WEEKDAY.search(text)
    if m:
        monday = today - timedelta(days=today.weekday())
        return _cut(text, m), _iso(
            monday + timedelta(days=7 + _WEEKDAY_CH[m.group(1)]))
    m = _RE_MD.search(text)
    if m:
        if m.group(1):   # 显式年份：按该年解析，不滚动
            d = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        else:
            d = _future_year_date(int(m.group(2)), int(m.group(3)), today)
        return (_cut(text, m), _iso(d)) if d else (text, None)
    m = _RE_MD_SLASH.search(text)
    if m:
        at_head = not text[:m.start()].strip()
        at_tail = not text[m.end():].strip()
        if at_head or at_tail:   # 句中斜杠是分数（"完成 3/4 后交付"）
            d = _future_year_date(int(m.group(1)), int(m.group(2)), today)
            return (_cut(text, m), _iso(d)) if d else (text, None)
    m = _RE_WEEKDAY.search(text)
    if m:
        ahead = (_WEEKDAY_CH[m.group(1)] - today.weekday()) % 7
        return _cut(text, m), _iso(today + timedelta(days=ahead))
    # ── 英文速记兜底（与界面语言无关：中英两种语法恒可用）──
    # 全部正则带 IGNORECASE，直接匹配原文：不用 lower 副本定位——
    # 个别字符（土耳其 İ）lower 后长度变化，索引错位会切错原文
    m = _RE_RELATIVE_EN.search(text)
    if m:
        offset = _RELATIVE_DAYS_EN[m.group(1).lower()]
        return _cut(text, m), _iso(today + timedelta(days=offset))
    m = _RE_IN_N_DAYS.search(text)
    if m:
        return _cut(text, m), _iso(today + timedelta(days=int(m.group(1))))
    m = _RE_NEXT_WEEKDAY_EN.search(text)
    if m:
        monday = today - timedelta(days=today.weekday())
        return _cut(text, m), _iso(
            monday + timedelta(days=7 + _WEEKDAY_EN[m.group(1)]))
    m = _RE_WEEKDAY_EN.search(text)
    if m:
        ahead = (_WEEKDAY_EN[m.group(1)] - today.weekday()) % 7
        return _cut(text, m), _iso(today + timedelta(days=ahead))
    return text, None


def _parse_priority(text: str) -> tuple[str, int | None]:
    m = _RE_PRIORITY.search(text)
    if m:
        return _cut(text, m), int(m.group(1))
    return text, None


def _parse_tags(text: str) -> tuple[str, list[str]]:
    """标签语法：中文色名可只写前缀（#红 → 红色）或直接英文 key（#blue）"""
    labels: list[str] = []

    def _sub(m: re.Match) -> str:
        token = m.group(1)
        hit: str | None = None
        for key, name in AppConfig.LABEL_NAMES.items():
            if token and (token == name or name.startswith(token)):
                hit = key
                break
        if hit is None and token in AppConfig.LABEL_COLORS:
            hit = token
        if hit is None:
            return m.group(0)   # 未命中：原样保留（可能只是话题标签）
        if hit not in labels:
            labels.append(hit)
        return " "

    return _RE_TAG.sub(_sub, text), labels


def parse_quick_input(text: str,
                      today: date | None = None) -> tuple[str, dict]:
    """解析速记语法 → (纯标题, 字段片段)

    fields 只含命中的字段（due_date / priority / labels），可直接
    Card(title=..., **fields) 使用；标题为空（整行全是语法 token）时
    由调用方决定回退。today 供测试注入固定日期。
    """
    today = today or date.today()
    fields: dict = {}
    rest = text
    rest, due = _parse_due(rest, today)
    if due:
        fields["due_date"] = due
    rest, priority = _parse_priority(rest)
    if priority is not None:
        fields["priority"] = priority
    rest, labels = _parse_tags(rest)
    if labels:
        fields["labels"] = labels
    return " ".join(rest.split()), fields

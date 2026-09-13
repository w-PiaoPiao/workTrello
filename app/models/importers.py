"""外部格式导入：Trello 看板 JSON 导出 → Board、Markdown → Board

Trello 导出格式（JSON）：顶层 name/lists/cards/checklists，cards 内
idList 关联列表、labels 为对象数组、due 为 ISO 时间戳。这里做"无损
尽力"映射： closed 列表/卡片丢弃与归档的取舍——closed 卡片进归档
（保留数据），closed 列表直接丢弃（Trello 删列即 closed，多数是垃圾）。

Markdown 导入兼容本应用自己的导出格式（## 列表 + - [x] 卡片 + 缩进
备注行），也兼容常见任务清单写法（普通 - 条目视为未完成卡片）。

两个解析器都是纯函数（str/dict → Board），不依赖 Qt，可独立单测。
"""

from __future__ import annotations

import re
from datetime import datetime

from app.models.board import Board, BoardList, Card, normalize_checklist

# Trello 标签色 → 本应用色板 key（超集颜色就近归并）
_TRELLO_COLOR_MAP = {
    "green": "green", "lime": "green",
    "yellow": "orange", "orange": "orange",
    "red": "red", "pink": "red",
    "purple": "purple",
    "blue": "blue", "sky": "blue",
    "black": "teal",
}

_RE_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_RE_MD_TODO_DONE = re.compile(r"^[-*+]\s+\[[xX✓]\]\s+(.*)$")
_RE_MD_TODO_OPEN = re.compile(r"^[-*+]\s+\[\s?\]\s+(.*)$")
_RE_MD_BULLET = re.compile(r"^[-*+]\s+(.*)$")
_RE_MD_INDENT = re.compile(r"^(?:\s{4,}|\t)(.+)$")


def _parse_trello_due(raw) -> str | None:
    """Trello due 是完整 ISO 时间戳（可能带毫秒与 Z），截取日期段"""
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")) \
            .date().isoformat()
    except ValueError:
        return raw[:10]


def board_from_trello(doc: dict) -> tuple[Board, str]:
    """Trello 导出 JSON → (Board, 报告文本)；结构不合规抛 ValueError"""
    if not isinstance(doc, dict):
        raise ValueError("文件不是 Trello 看板导出格式")
    if "lists" not in doc or "cards" not in doc:
        raise ValueError("缺少 lists/cards 字段，不是 Trello 看板导出格式")

    board_name = str(doc.get("name", "") or "").strip()

    # 清单按卡片归组（Trello 顶层 checklists 内 idCard 关联）
    checks_by_card: dict[str, list[dict]] = {}
    raw_checks = doc.get("checklists", [])
    if isinstance(raw_checks, list):
        for cl in raw_checks:
            if not isinstance(cl, dict):
                continue
            cid = str(cl.get("idCard", "") or "")
            items = cl.get("checkItems", [])
            if not cid or not isinstance(items, list):
                continue
            checks_by_card.setdefault(cid, []).append(
                {"name": str(cl.get("name", "") or ""), "items": items})

    lists_by_id: dict[str, BoardList] = {}
    order: list[str] = []
    raw_lists = doc.get("lists", [])
    if isinstance(raw_lists, list):
        for lst in raw_lists:
            if not isinstance(lst, dict) or lst.get("closed"):
                continue
            lid = str(lst.get("id", "") or "")
            title = str(lst.get("name", "") or "").strip() or "未命名列表"
            bl = BoardList(title=title, id=lid)
            lists_by_id[lid] = bl
            order.append(lid)

    raw_cards = doc.get("cards", [])
    if isinstance(raw_cards, list):
        for entry in raw_cards:
            if not isinstance(entry, dict):
                continue
            cid = str(entry.get("id", "") or "")
            title = str(entry.get("name", "") or "").strip()
            if not title:
                continue
            labels: list[str] = []
            raw_labels = entry.get("labels", [])
            if isinstance(raw_labels, list):
                for lb in raw_labels:
                    if isinstance(lb, dict):
                        key = _TRELLO_COLOR_MAP.get(str(lb.get("color", "") or ""))
                        if key and key not in labels:
                            labels.append(key)
            checklist: list[dict] = []
            card_checks = checks_by_card.get(cid, [])
            for cl in card_checks:
                items = cl["items"]
                if not isinstance(items, list):
                    continue
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    text = str(it.get("name", "") or "").strip()
                    if not text:
                        continue
                    if len(card_checks) > 1 and cl["name"]:
                        text = f"{cl['name']} · {text}"
                    checklist.append({
                        "text": text,
                        "done": str(it.get("state", "")) == "complete",
                    })
            card = Card(
                title=title,
                id=cid or None,
                notes=str(entry.get("desc", "") or ""),
                labels=labels,
                due_date=_parse_trello_due(entry.get("due")),
                done=bool(entry.get("dueComplete")),
                archived=bool(entry.get("closed")),
                checklist=normalize_checklist(checklist),
            )
            target = lists_by_id.get(str(entry.get("idList", "") or ""))
            if target is None:
                # 卡片挂在已 closed 的列表下：丢进最后一个有效列表保数据
                target = lists_by_id.get(order[-1]) if order else None
            if target is None:
                target = BoardList(title="待办")
                lists_by_id[target.id] = target
                order.append(target.id)
            target.cards.append(card)

    board = Board(lists=[lists_by_id[lid] for lid in order if lid in lists_by_id],
                  name=board_name)
    total = sum(len(l.cards) for l in board.lists)
    archived = sum(1 for l in board.lists for c in l.cards if c.archived)
    report = f"列表 {len(board.lists)} · 卡片 {total}（含归档 {archived}）"
    return board, report


def board_from_markdown(text: str, fallback_title: str = "") -> tuple[Board, str]:
    """Markdown 任务清单 → (Board, 报告文本)

    识别规则（宽松，兼容常见写法）：
    - `#` 一级标题 → 看板名（不建列表；本应用导出格式的文档标题）
    - `##` 及以下标题行 → 新列表
    - `- [x]` → 已完成卡片；`- [ ]` / 普通 `- 文本` → 未完成卡片
    - 缩进 ≥4 空格（或 Tab）且非列表行 → 追加到上一张卡的备注
    """
    lists: list[BoardList] = []
    board_title = fallback_title
    current: BoardList | None = None
    current_card: Card | None = None
    for raw in text.replace("\r\n", "\n").split("\n"):
        if not raw.strip():
            continue
        m = _RE_MD_HEADING.match(raw)
        if m:
            title = m.group(2).strip()
            if title:
                if len(m.group(1)) == 1 and not lists:
                    # 文档一级标题 → 看板名（首个 # 且尚无列表时）
                    board_title = title
                    continue
                current = BoardList(title=title)
                lists.append(current)
                current_card = None
            continue
        m = _RE_MD_TODO_DONE.match(raw)
        if m:
            card = Card(title=m.group(1).strip(), done=True)
            _append_card(lists, current, card)
            current, current_card = _last(lists), card
            continue
        m = _RE_MD_TODO_OPEN.match(raw)
        if m:
            card = Card(title=m.group(1).strip())
            _append_card(lists, current, card)
            current, current_card = _last(lists), card
            continue
        m = _RE_MD_INDENT.match(raw)
        if m and current_card is not None:
            note_line = m.group(1).strip()
            if note_line:
                current_card.notes = (current_card.notes + "\n"
                                      if current_card.notes else "") + note_line
            continue
        m = _RE_MD_BULLET.match(raw)
        if m:
            card = Card(title=m.group(1).strip())
            _append_card(lists, current, card)
            current, current_card = _last(lists), card
            continue
        # 其他普通文本行：无上下文时忽略
    if not lists:
        raise ValueError("未识别到列表标题（## …）或任务条目（- [ ] …）")
    board = Board(lists=lists, name=board_title)
    total = sum(len(l.cards) for l in board.lists)
    return board, f"列表 {len(lists)} · 卡片 {total}"


def _last(lists: list[BoardList]) -> BoardList | None:
    return lists[-1] if lists else None


def _append_card(lists: list[BoardList], current: BoardList | None,
                 card: Card) -> None:
    if current is None:
        current = BoardList(title="待办")
        lists.append(current)
    current.cards.append(card)

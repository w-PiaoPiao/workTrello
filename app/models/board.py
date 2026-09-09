"""
看板数据模型：Board（看板）/ BoardList（列表）/ Card（卡片）

序列化约定：
- 所有对象都有 to_dict() / from_dict()，from_dict 对缺失字段容错
- id 使用 uuid4 hex（无连字符）

BoardStore 与 Board 的关系：BoardStore 持有"内存缓存 + 落盘"，
Board 是看板聚合（lists 列表与跨列表查找/统计），不依赖存储。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

LOCAL_TZ = datetime.now().astimezone().tzinfo


def _now_iso() -> str:
    return datetime.now(LOCAL_TZ).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class Card:
    """看板卡片"""

    title: str
    id: str = field(default_factory=_new_id)
    notes: str = ""                     # 备注正文
    labels: list[str] = field(default_factory=list)   # 标签色 key 列表
    due_date: str | None = None         # ISO 日期 YYYY-MM-DD，None 表示未设置
    done: bool = False                  # 勾选完成
    created_at: str = field(default_factory=_now_iso)
    starred: bool = False               # ⭐ 加入今日聚焦
    pomodoros: int = 0                  # 完成的番茄钟数
    done_at: str | None = None          # 勾选完成的时刻（周统计用）
    archived: bool = False              # 归档（不出现在看板）
    repeat: str = "never"               # never | daily | weekly（完成时自动滚到下一周期）
    priority: int = 0                   # 0=无 1=高 2=中 3=低（今日聚焦内排序用）

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "notes": self.notes,
            "labels": list(self.labels),
            "due_date": self.due_date,
            "done": self.done,
            "created_at": self.created_at,
            "starred": self.starred,
            "pomodoros": self.pomodoros,
            "done_at": self.done_at,
            "archived": self.archived,
            "repeat": self.repeat,
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Card":
        title = str(data.get("title", "")).strip()
        if not title:
            raise ValueError("卡片标题为空")
        labels_raw = data.get("labels", [])
        labels = ([str(x) for x in labels_raw if isinstance(x, str)]
                  if isinstance(labels_raw, list) else [])
        due = data.get("due_date")
        if not isinstance(due, str) or not due:
            due = None
        try:
            pomodoros = int(data.get("pomodoros", 0) or 0)
        except (TypeError, ValueError):
            pomodoros = 0
        done_at = data.get("done_at")
        repeat = data.get("repeat", "never")
        if repeat not in ("never", "daily", "weekly"):
            repeat = "never"
        try:
            priority = int(data.get("priority", 0) or 0)
        except (TypeError, ValueError):
            priority = 0
        return cls(
            title=title,
            id=str(data.get("id") or _new_id()),
            notes=str(data.get("notes", "")),
            labels=labels,
            due_date=due,
            done=bool(data.get("done", False)),
            created_at=str(data.get("created_at") or _now_iso()),
            starred=bool(data.get("starred", False)),
            pomodoros=max(0, pomodoros),
            done_at=str(done_at) if done_at else None,
            archived=bool(data.get("archived", False)),
            repeat=repeat,
            priority=max(0, min(3, priority)),
        )

    def set_done(self, done: bool) -> None:
        """勾选完成/取消：done_at 自动维护（今日统计 / 彩蛋 / 周统计共用）"""
        self.done = done
        if done and not self.done_at:
            self.done_at = _now_iso()
        elif not done:
            self.done_at = None

    def apply(self, data: dict) -> None:
        """用表单结果 dict 就地更新字段（CardDialog.result_card 的输出）"""
        self.title = str(data.get("title", self.title)).strip()
        self.notes = str(data.get("notes", self.notes)).strip()
        self.labels = list(data.get("labels", self.labels))
        self.due_date = data.get("due_date", self.due_date)
        self.starred = bool(data.get("starred", self.starred))
        repeat = data.get("repeat", self.repeat)
        self.repeat = repeat if repeat in ("never", "daily", "weekly") \
            else "never"
        try:
            priority = int(data.get("priority", self.priority) or 0)
        except (TypeError, ValueError):
            priority = self.priority
        self.priority = max(0, min(3, priority))
        self.set_done(bool(data.get("done", self.done)))

    # ── 统一谓词（今日聚焦 / 截止统计 / 视图展示共用，避免多处各自解析）──

    def due_delta(self, today: date) -> int | None:
        """截止日与 today 相差天数（逾期为负、当天为 0）；
        未设置日期或日期字符串非法时返回 None"""
        if not self.due_date:
            return None
        try:
            return (date.fromisoformat(self.due_date) - today).days
        except ValueError:
            return None

    def in_today_focus(self, today: date) -> bool:
        """今日聚焦谓词：未归档、未完成，且（星标 或 截止日不晚于 today）"""
        if self.done or self.archived:
            return False
        if self.starred:
            return True
        delta = self.due_delta(today)
        return delta is not None and delta <= 0

    def roll_repeat(self) -> bool:
        """重复任务完成时滚动：截止日推进到下一周期（逾期补完则推进到
        不早于今天），并复位 done/done_at。返回是否发生了滚动。"""
        if self.repeat not in ("daily", "weekly") or not self.due_date:
            return False
        try:
            due = date.fromisoformat(self.due_date)
        except ValueError:
            return False
        step = 1 if self.repeat == "daily" else 7
        due += timedelta(days=step)
        today = date.today()
        while due < today:
            due += timedelta(days=step)
        self.due_date = due.isoformat()
        self.done = False
        self.done_at = None
        return True


@dataclass
class BoardList:
    """看板列表（Trello 的 list）"""

    title: str
    id: str = field(default_factory=_new_id)
    cards: list[Card] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "cards": [c.to_dict() for c in self.cards],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BoardList":
        title = str(data.get("title", "")).strip() or "未命名列表"
        cards_raw = data.get("cards", [])
        cards: list[Card] = []
        if isinstance(cards_raw, list):
            for entry in cards_raw:
                if not isinstance(entry, dict):
                    continue
                try:
                    cards.append(Card.from_dict(entry))
                except (ValueError, TypeError, AttributeError):
                    continue
        return cls(
            title=title,
            id=str(data.get("id") or _new_id()),
            cards=cards,
        )


class Board:
    """看板聚合：列表集合 + 跨列表查找/统计"""

    def __init__(self, lists: list[BoardList] | None = None):
        self.lists: list[BoardList] = lists if lists is not None else []

    def to_dict(self) -> dict:
        return {"lists": [lst.to_dict() for lst in self.lists]}

    @classmethod
    def from_dict(cls, data: dict) -> "Board":
        lists_raw = data.get("lists", [])
        lists: list[BoardList] = []
        if isinstance(lists_raw, list):
            for entry in lists_raw:
                if not isinstance(entry, dict):
                    continue
                lists.append(BoardList.from_dict(entry))
        if not lists:
            lists = [
                BoardList(title="待办"),
                BoardList(title="进行中"),
                BoardList(title="已完成"),
            ]
        return cls(lists=lists)

    def find_list(self, list_id: str) -> BoardList | None:
        for lst in self.lists:
            if lst.id == list_id:
                return lst
        return None

    def find_card(self, card_id: str) -> tuple[BoardList | None, Card | None]:
        """按卡片 id 查找，返回 (所在列表, 卡片)；未找到返回 (None, None)"""
        for lst in self.lists:
            for card in lst.cards:
                if card.id == card_id:
                    return lst, card
        return None, None

    def remove_card(self, card_id: str) -> Card | None:
        """按卡片 id 移除并返回卡片；未找到返回 None"""
        for lst in self.lists:
            for i, c in enumerate(lst.cards):
                if c.id == card_id:
                    return lst.cards.pop(i)
        return None

    def remove_list(self, list_id: str) -> BoardList | None:
        """按列表 id 移除并返回列表；未找到返回 None"""
        for i, lst in enumerate(self.lists):
            if lst.id == list_id:
                return self.lists.pop(i)
        return None

    def total_cards(self) -> int:
        return sum(1 for lst in self.lists for c in lst.cards
                   if not c.archived)

    def done_cards(self) -> int:
        return sum(1 for lst in self.lists for c in lst.cards
                   if c.done and not c.archived)

    def due_counts(self, today: date) -> tuple[int, int]:
        """截止提醒统计：返回 (已逾期未完成数, 今日截止未完成数)

        只统计未归档且未完成卡片；无效日期字符串忽略。
        """
        overdue = due_today = 0
        for lst in self.lists:
            for c in lst.cards:
                if c.done or c.archived:
                    continue
                delta = c.due_delta(today)
                if delta is None:
                    continue
                if delta < 0:
                    overdue += 1
                elif delta == 0:
                    due_today += 1
        return overdue, due_today

    def today_done_count(self, today: date | None = None) -> int:
        """当日勾选完成的卡片数（done_at 落在 today，含归档；彩蛋统计用）"""
        today = today or date.today()
        n = 0
        for lst in self.lists:
            for c in lst.cards:
                if not c.done or not c.done_at:
                    continue
                try:
                    if datetime.fromisoformat(c.done_at).date() == today:
                        n += 1
                except ValueError:
                    continue
        return n

    def today_focus_cards(self, today: date) -> list[Card]:
        """今日聚焦集合：未归档、未完成，且（星标 或 截止日<=today）"""
        return [c for lst in self.lists for c in lst.cards
                if c.in_today_focus(today)]

    def archived_cards(self) -> list[tuple["BoardList", Card]]:
        """归档卡片及其所属列表"""
        return [(lst, c) for lst in self.lists for c in lst.cards
                if c.archived]

    def weekly_done_count(self, now: datetime | None = None) -> int:
        """最近 7 天内勾选完成的卡片数（含归档）"""
        now = now or datetime.now(LOCAL_TZ)
        if now.tzinfo is None:
            now = now.astimezone()      # 统一为带时区，避免与 done_at 比较出错
        cutoff = now - timedelta(days=7)
        n = 0
        for lst in self.lists:
            for c in lst.cards:
                if not c.done or not c.done_at:
                    continue
                try:
                    if datetime.fromisoformat(c.done_at) >= cutoff:
                        n += 1
                except ValueError:
                    continue
        return n


class BoardStore:
    """看板数据存储（内存缓存 + 落盘；防抖调度由控制器负责）"""

    def __init__(self, path):
        self._path = path
        self._board: Board | None = None
        self._dirty = False
        self.problems: list[tuple[str, str]] = []
        self.last_backup_path: Path | None = None

    @property
    def path(self) -> Path:
        return self._path

    # ── 加载 ──────────────────────────────────────────────

    def load(self) -> Board:
        """加载看板（首次读盘，之后返回缓存）"""
        if self._board is None:
            from app.models.json_io import load_json_doc
            doc = load_json_doc(
                self._path,
                on_problem=lambda kind, _p, detail: self.problems.append(
                    (kind, detail)),
            )
            self._board = Board.from_dict(doc)
        return self._board

    def reload(self) -> Board:
        """丢弃内存缓存，从磁盘重新加载（用于从备份恢复后）"""
        self._board = None
        return self.load()

    # ── 写入 ──────────────────────────────────────────────

    def mark_dirty(self) -> None:
        self._dirty = True

    def replace_board(self, board: Board) -> None:
        """整体替换内存看板并置脏（撤销恢复用）"""
        self._board = board
        self._dirty = True

    def flush(self) -> None:
        """落盘（脏标记时才写）"""
        if not self._dirty or self._board is None:
            return
        from app.models.json_io import atomic_write_json
        doc = {
            "app": "桌宠看板",
            "version": 1,
            "saved_at": _now_iso(),
            "lists": [lst.to_dict() for lst in self._board.lists],
        }
        atomic_write_json(self._path, doc)
        self._dirty = False

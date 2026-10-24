"""卡片模板：把一张卡片存成模板，之后一键生成同款新卡

**存在哪**：数据目录下的 `templates.json`，与看板文档解耦。模板是跨看板
复用的"卡片形状"，塞进某块看板的文档会让切看板时模板跟着消失；也避免动
board 文档 schema（版本迁移的成本远高于收益）。

**不带附件**：模板里的附件路径指向某张卡的 `attachments/<卡 id>/` 目录，
而那个目录只被"仍引用它的卡片"保护（退出时回收孤儿目录）——模板长期没有
实例卡时文件会被回收，模板就悄悄坏了。模板只保存"卡片长什么样"，需要附件
时把文件拖进列现挂即可（拖文件建卡已支持）。
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.config import AppConfig
from app.models import json_io
from app.models.board import Card

logger = logging.getLogger(__name__)

TEMPLATE_FILE = "templates.json"
TEMPLATE_KEEP = 60        # 模板数量上限（超出丢弃最旧的，防文件无限膨胀）

# 可模板化的字段：标题由用户每次现填，done/due_date/attachments/created_at
# 属于"某一次的具体状态"而非"卡片的形状"，一概不进模板
_TEMPLATE_FIELDS = ("notes", "labels", "priority", "repeat",
                    "repeat_interval", "checklist", "starred")


def templates_path() -> Path:
    return AppConfig.DATA_DIR / TEMPLATE_FILE


def load_templates() -> list[dict]:
    """全部模板（[{name, data}]，按存入顺序）；损坏时返回空表

    走与看板同一套 JSON 容错：文件损坏会被隔离备份而不是让应用启动失败。
    """
    path = templates_path()
    if not path.exists():
        return []
    doc = json_io.load_json_doc(path)
    raw = doc.get("templates") if isinstance(doc, dict) else None
    if not isinstance(raw, list):
        return []
    out = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        data = entry.get("data")
        if name and isinstance(data, dict):
            out.append({"name": name, "data": data})
    return out


def save_template(name: str, card: Card) -> bool:
    """把一张卡片存成模板（同名覆盖）；返回是否写入成功"""
    name = name.strip()
    if not name:
        return False
    data = {key: getattr(card, key) for key in _TEMPLATE_FIELDS}
    entries = [e for e in load_templates() if e["name"] != name]
    entries.append({"name": name, "data": data})
    del entries[:-TEMPLATE_KEEP]
    try:
        json_io.atomic_write_json(templates_path(),
                                  {"templates": entries}, durable=True)
    # StoreError 不是 OSError 子类：只捕 OSError 会让写失败（磁盘满/权限）
    # 冒泡进 Qt 槽，"返回 False + 调用方提示"的契约失效
    except (OSError, json_io.StoreError) as e:
        logger.warning("模板写入失败: %s", e)
        return False
    return True


def delete_template(name: str) -> bool:
    entries = load_templates()
    kept = [e for e in entries if e["name"] != name]
    if len(kept) == len(entries):
        return False
    try:
        json_io.atomic_write_json(templates_path(),
                                  {"templates": kept}, durable=True)
    except (OSError, json_io.StoreError) as e:
        logger.warning("模板写入失败: %s", e)
        return False
    return True


def card_from_template(name: str, title: str) -> Card | None:
    """按模板名造一张新卡（标题由调用方给）；模板不存在返回 None"""
    for entry in load_templates():
        if entry["name"] != name:
            continue
        data = dict(entry["data"])
        data["title"] = title
        try:
            return Card.from_dict({**data, "id": None})
        except Exception as e:   # noqa: BLE001 — 单条模板损坏不该炸开
            logger.warning("模板还原失败 %s: %s", name, e)
            return None
    return None

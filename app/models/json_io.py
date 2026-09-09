"""
JSON 文件读写公共工具

- load_json_doc：读取 dict 数据（损坏自动隔离备份并返回空文档）
- atomic_write_json：原子写入（先写 .tmp 并 fsync 落盘，再 replace）
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

CORRUPT_BACKUP_KEEP = 5
SNAPSHOT_KEEP = 10       # 启动快照保留份数


def backup_ext(tag: str = "corrupt") -> str:
    """带时间戳的备份文件后缀。

    tag 标记备份类型：corrupt=损坏隔离副本，good=好副本固化保留。
    """
    return f".{tag}." + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".bak"


class StoreError(Exception):
    """数据存储异常"""


def load_json_doc(
    path: Path,
    on_problem: Callable[[str, Path, str], None] | None = None,
) -> dict:
    """从 JSON 文件加载 dict 数据

    - 文件不存在 → 空文档
    - 解析错误/顶层不是 dict → 隔离备份后返回空文档
    """
    if not path.exists():
        return {}

    raw: str | None = None
    read_error: Exception | None = None
    for attempt in range(3):
        try:
            raw = path.read_text("utf-8")
            break
        except OSError as e:
            read_error = e
            time.sleep(0.1 * (attempt + 1))

    if raw is None:
        logger.error("数据文件读取失败，以空数据启动: %s (%s)", path, read_error)
        if on_problem:
            on_problem("unreadable", path, str(read_error))
        return {}

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.warning("JSON 解析失败 (%s)，隔离备份文件: %s", e, path)
        backup_corrupted(path)
        if on_problem:
            on_problem("corrupted", path, str(e))
        return {}

    if not isinstance(data, dict):
        logger.warning("数据格式错误，期望对象，实际 %s", type(data).__name__)
        backup_corrupted(path)
        if on_problem:
            on_problem("corrupted", path, f"顶层不是对象: {type(data).__name__}")
        return {}

    return data


def atomic_write_json(path: Path, data, *, indent: int = 2,
                      ensure_ascii: bool = False) -> None:
    """原子写入 JSON 文件（写 .tmp → fsync → replace）

    写入成功后把上一次的内容轮转为 <文件名>.prev，作为最近一份好副本，
    供数据文件损坏时恢复。
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    prev_path = path.with_name(path.name + ".prev")
    tmp_path = path.with_suffix(".tmp")
    try:
        content = json.dumps(data, ensure_ascii=ensure_ascii, indent=indent)
        with tmp_path.open("w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            path.replace(prev_path)
        tmp_path.replace(path)
    except OSError as e:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("清理临时文件失败: %s", tmp_path)
        raise StoreError(f"保存失败 ({path.name}): {e}") from e


def backup_corrupted(path: Path) -> Path | None:
    """备份损坏文件为带时间戳的隔离副本（保留最近 N 份）"""
    bak_path = path.with_name(path.name + backup_ext())
    try:
        shutil.copy2(path, bak_path)
    except OSError as e:
        logger.error("备份损坏文件失败: %s", e)
        return None
    try:
        backups = sorted(path.parent.glob(f"{path.name}.corrupt.*.bak"))
        for old in backups[:-CORRUPT_BACKUP_KEEP]:
            old.unlink(missing_ok=True)
    except OSError as e:
        logger.warning("清理历史损坏备份失败: %s", e)
    logger.info("已备份损坏文件到 %s", bak_path)
    return bak_path


def latest_backup(path: Path) -> Path | None:
    """最近的损坏隔离备份（board.json.corrupt.<时间戳>.bak）"""
    backups = sorted(path.parent.glob(f"{path.name}.corrupt.*.bak"))
    return backups[-1] if backups else None


def good_prev_copy(path: Path) -> Path | None:
    """最近一次成功写入轮转出的好副本（<文件名>.prev），不存在返回 None"""
    prev = path.with_name(path.name + ".prev")
    return prev if prev.exists() else None


def doc_has_cards(path: Path) -> bool:
    """文档是否含任意卡片（空默认板 / 空文件不算有数据）"""
    try:
        with path.open("r", encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    lists = doc.get("lists") if isinstance(doc, dict) else None
    if not isinstance(lists, list):
        return False
    return any(isinstance(lst, dict)
               and isinstance(lst.get("cards"), list)
               and bool(lst["cards"])
               for lst in lists)


def _snapshot_paths(path: Path) -> list[Path]:
    """现有启动快照（按名称时间升序）"""
    return sorted(path.parent.glob(f"{path.name}.snap.*.bak"))


def snapshot_board(path: Path, keep: int = SNAPSHOT_KEEP) -> Path | None:
    """启动快照：把当前含数据的文件复制为 <名>.snap.<时间戳>.bak

    每次正常启动调用一次，为上一份数据留档——即使数据文件随后被异常
    覆盖（如旧版 exe 非原子写入空板），快照链仍可一键恢复。
    空板/默认板不产生快照（不污染恢复候选）；保留最近 keep 份。
    """
    if not path.exists() or not doc_has_cards(path):
        return None
    snap = path.with_name(path.name + backup_ext("snap"))
    try:
        shutil.copy2(path, snap)
    except OSError as e:
        logger.warning("创建启动快照失败: %s (%s)", snap, e)
        return None
    try:
        for old in _snapshot_paths(path)[:-keep]:
            old.unlink(missing_ok=True)
    except OSError as e:
        logger.warning("清理历史启动快照失败: %s", e)
    logger.info("已创建启动快照: %s", snap)
    return snap


def nonempty_snapshots(path: Path) -> list[Path]:
    """按时间倒序返回含数据的启动快照（供空板恢复引导选用）"""
    return [p for p in reversed(_snapshot_paths(path)) if doc_has_cards(p)]


def restore_from_backup(store_path: Path, backup: Path) -> bool:
    """把备份文件复制回数据文件路径并触发写盘。

    仅在备份本身可解析时恢复；成功后备份文件继续保留，
    直到下一次正常 flush 由 .prev 轮转机制接管。
    """
    try:
        with backup.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("备份内容不是对象")
    except (OSError, json.JSONDecodeError, ValueError) as e:
        logger.error("备份文件无法解析，恢复失败: %s (%s)", backup, e)
        return False
    try:
        atomic_write_json(store_path, data)
    except StoreError as e:
        logger.error("恢复落盘失败: %s", e)
        return False
    logger.info("已从备份恢复数据: %s", backup)
    return True

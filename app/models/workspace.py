"""多看板工作区：看板集合索引（workspace.json）+ 每看板一个 JSON 文件

布局：
    <DATA_DIR>/workspace.json      索引：看板元信息列表 + 当前看板 id
    <DATA_DIR>/boards/<id>.json    每块看板一个文件（与旧 board.json 同构，
                                   额外带 id/name 字段）
    <DATA_DIR>/board.json          旧版单看板文件（仅迁移读取，不删除——
                                   多留一份旧格式数据是无成本的安全网）

单看板时代的防抖落盘 / .prev 轮转 / 启动快照机制全部按"文件"运作
（json_io.* 均接收 path），天然适配多文件；本类只负责索引与文件定位，
内存缓存与脏标记仍在 BoardStore（控制器为当前看板持有一个实例）。
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.models.board import Board
from app.models.json_io import (atomic_write_json, backup_ext,
                                load_json_doc, rotate_backups)

logger = logging.getLogger(__name__)

WORKSPACE_FILE = "workspace.json"
BOARDS_DIR_NAME = "boards"
LEGACY_BOARD_FILE = "board.json"

WORKSPACE_VERSION = 1

# 看板 id 白名单：uuid4().hex 形式（32 位十六进制）。索引文件被手工
# 改坏/串改时，"../x" 之类的 id 不得借 board_path 越出 boards/ 目录
_BOARD_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _valid_board_id(bid: str) -> bool:
    return bool(_BOARD_ID_RE.match(bid))


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class BoardMeta:
    """看板元信息（索引持久化内容；名字空串 = 界面显示默认名）"""
    id: str
    name: str = ""
    created_at: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name,
                "created_at": self.created_at}

    @classmethod
    def from_dict(cls, data: dict) -> "BoardMeta | None":
        if not isinstance(data, dict):
            return None
        bid = str(data.get("id", "") or "").strip()
        if not bid or not _valid_board_id(bid):
            return None
        return cls(id=bid, name=str(data.get("name", "") or ""),
                   created_at=str(data.get("created_at", "") or ""))


class Workspace:
    """看板集合：加载/迁移、增删改名、当前看板切换（索引即时落盘）"""

    def __init__(self, data_dir: Path):
        self._data_dir = data_dir
        self.boards: list[BoardMeta] = []
        self.current_id: str = ""

    # ── 路径 ──────────────────────────────────────────────

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    @property
    def workspace_path(self) -> Path:
        return self._data_dir / WORKSPACE_FILE

    @property
    def boards_dir(self) -> Path:
        return self._data_dir / BOARDS_DIR_NAME

    def board_path(self, board_id: str) -> Path:
        if not _valid_board_id(board_id):
            raise ValueError(f"非法看板 id: {board_id!r}")
        return self.boards_dir / f"{board_id}.json"

    # ── 加载（含旧单看板迁移） ────────────────────────────

    def load(self) -> None:
        """加载索引；索引无效时先从 boards/ 目录重建，再兜底迁移/默认板"""
        doc = load_json_doc(self.workspace_path)
        metas: list[BoardMeta] = []
        version_ok = doc.get("version") == WORKSPACE_VERSION
        if isinstance(doc.get("boards"), list) and version_ok:
            for entry in doc["boards"]:
                meta = BoardMeta.from_dict(entry)
                if meta is not None:
                    metas.append(meta)
        elif doc:
            # 索引存在但版本不匹配/字段错乱：先留档再重建，避免接下来的
            # _save() 把唯一线索覆盖掉（彻底损坏的场景 load_json_doc 已
            # 自动隔离 .corrupt 备份，无需重复留档）
            self._archive_workspace_index()

        self._ensure_dirs()
        if metas:
            self.boards = metas
            current = str(doc.get("current", "") or "")
            self.current_id = (current if self._meta(current) is not None
                               else metas[0].id)
            # 索引外孤儿收编：create_board 先写板文件后写索引，这个
            # 崩溃窗口留下的板文件不该丢
            if self._absorb_orphans():
                self._save()
            return

        # 无有效索引：扫描 boards/ 目录重建（数据仍在，索引失联不能
        # 让全部看板"消失"）
        rebuilt = self._rebuild_from_boards_dir()
        if rebuilt:
            self.boards = rebuilt
            self.current_id = rebuilt[0].id
            logger.warning("工作区索引无效，已从 %s 重建出 %d 块看板",
                           self.boards_dir, len(rebuilt))
            self._save()
            return

        # boards/ 目录也为空：迁移旧单看板文件，或创建默认看板
        legacy = self._data_dir / LEGACY_BOARD_FILE
        if legacy.exists():
            board = Board.from_dict(load_json_doc(legacy))
            meta = BoardMeta(id=_new_board_id(),
                             name=str(board.name or ""),
                             created_at=_now_iso())
            self._write_board_file(meta, board)
            self.boards = [meta]
            self.current_id = meta.id
            self._save()
            logger.info("已迁移旧看板数据到 %s", self.board_path(meta.id))
            return

        meta = BoardMeta(id=_new_board_id(), name="", created_at=_now_iso())
        self._write_board_file(meta, Board.from_dict({}))
        self.boards = [meta]
        self.current_id = meta.id
        self._save()

    def _archive_workspace_index(self) -> None:
        """把可疑的原索引复制为 .wsbak.<时间戳>.bak（保留最近 3 份）"""
        bak = self.workspace_path.with_name(
            self.workspace_path.name + backup_ext("wsbak"))
        try:
            shutil.copy2(self.workspace_path, bak)
            rotate_backups(self.workspace_path, "wsbak", 3)
            logger.warning("可疑的工作区索引已留档: %s", bak.name)
        except OSError as e:
            logger.warning("工作区索引留档失败: %s (%s)", bak, e)

    def _board_files(self) -> list[Path]:
        """boards/ 目录下全部看板文件（按文件名排序；目录异常视为空）"""
        try:
            return sorted(self.boards_dir.glob("*.json"))
        except OSError as e:
            logger.warning("扫描看板目录失败: %s (%s)", self.boards_dir, e)
            return []

    def _rebuild_from_boards_dir(self) -> list[BoardMeta]:
        """扫描 boards/ 目录逐个解析，按 mtime 升序（近似创建顺序）重建候选"""
        found: list[tuple[float, BoardMeta]] = []
        for path in self._board_files():
            bid = path.stem
            if not _valid_board_id(bid):
                continue
            doc = load_json_doc(path)
            if not doc:
                continue   # 损坏文件已被 load_json_doc 隔离，跳过
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            found.append((mtime, BoardMeta(
                id=bid,
                name=str(doc.get("name", "") or ""),
                created_at=str(doc.get("saved_at", "") or ""))))
        found.sort(key=lambda item: item[0])
        return [meta for _, meta in found]

    def _absorb_orphans(self) -> bool:
        """把索引未登记的合法板文件收编进索引（返回是否有变更）"""
        absorbed = False
        for path in self._board_files():
            bid = path.stem
            if not _valid_board_id(bid) or self._meta(bid) is not None:
                continue
            doc = load_json_doc(path)
            if not doc:
                continue
            self.boards.append(BoardMeta(
                id=bid,
                name=str(doc.get("name", "") or ""),
                created_at=str(doc.get("saved_at", "") or "")))
            absorbed = True
            logger.warning("发现索引外看板文件，已收编: %s", path.name)
        return absorbed

    def _ensure_dirs(self) -> None:
        try:
            self.boards_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error("创建看板目录失败: %s (%s)", self.boards_dir, e)

    # ── 查询 ──────────────────────────────────────────────

    def _meta(self, board_id: str) -> BoardMeta | None:
        for meta in self.boards:
            if meta.id == board_id:
                return meta
        return None

    def meta(self, board_id: str) -> BoardMeta | None:
        return self._meta(board_id)

    def current_meta(self) -> BoardMeta | None:
        return self._meta(self.current_id)

    # ── 变更 ──────────────────────────────────────────────

    def set_current(self, board_id: str) -> bool:
        """切换当前看板（索引即时落盘）；未知 id 拒绝"""
        if self._meta(board_id) is None:
            return False
        if board_id != self.current_id:
            self.current_id = board_id
            self._save()
        return True

    def create_board(self, name: str = "",
                     board: Board | None = None) -> BoardMeta:
        """新建看板（board 供导入路径传入完整数据）并置为当前"""
        if board is None:
            # 默认三列（待办/进行中/已完成），与首次启动的默认看板一致
            board = Board.from_dict({})
            board.name = name
        meta = BoardMeta(id=_new_board_id(), name=name,
                         created_at=_now_iso())
        self._ensure_dirs()
        self._write_board_file(meta, board)
        self.boards.append(meta)
        self.current_id = meta.id
        self._save()
        return meta

    def rename_board(self, board_id: str, name: str) -> bool:
        """重命名看板：索引与看板文件同步更新"""
        meta = self._meta(board_id)
        if meta is None:
            return False
        meta.name = name.strip()
        # 看板文件里的 name 字段一并改掉（文件可能尚未有 id/name 旧结构，
        # 读回再写最稳）；文件缺失时只改索引，下次 flush 自然补上
        path = self.board_path(board_id)
        if path.exists():
            from app.models.json_io import load_json_doc as _load
            doc = _load(path)
            if doc:
                doc["name"] = meta.name
                try:
                    atomic_write_json(path, doc, indent=None)
                except Exception as e:   # noqa: BLE001 — 改名失败不致命
                    logger.warning("重命名看板文件同步失败: %s", e)
        self._save()
        return True

    def delete_board(self, board_id: str) -> bool:
        """删除看板（文件 + 同族备份）；至少保留一块看板"""
        if len(self.boards) <= 1 or self._meta(board_id) is None:
            return False
        meta = self._meta(board_id)
        self.boards.remove(meta)
        if self.current_id == board_id:
            self.current_id = self.boards[0].id
        path = self.board_path(board_id)
        try:
            if path.exists():
                path.unlink()
            # 同族备份（.prev/.snap/corrupt/good）一并清理，不留孤儿
            for pattern in (f"{path.name}.prev",
                            f"{path.name}.snap.*.bak",
                            f"{path.name}.corrupt.*.bak",
                            f"{path.name}.good.*.bak"):
                for old in self.boards_dir.glob(pattern):
                    old.unlink(missing_ok=True)
        except OSError as e:
            logger.warning("删除看板文件失败: %s (%s)", path, e)
        self._save()
        return True

    def order(self) -> list[str]:
        return [m.id for m in self.boards]

    # ── 落盘 ──────────────────────────────────────────────

    def _write_board_file(self, meta: BoardMeta, board: Board) -> None:
        doc = {
            "app": "桌宠看板",
            "version": 2,
            "id": meta.id,
            "name": meta.name,
            "saved_at": _now_iso(),
            "lists": [lst.to_dict() for lst in board.lists],
        }
        atomic_write_json(self.board_path(meta.id), doc, indent=None)

    def _save(self) -> None:
        doc = {
            "version": WORKSPACE_VERSION,
            "current": self.current_id,
            "boards": [m.to_dict() for m in self.boards],
        }
        try:
            atomic_write_json(self.workspace_path, doc, indent=None)
        except Exception as e:   # noqa: BLE001 — 索引写失败不阻断会话
            logger.error("工作区索引保存失败: %s", e)

    # 供测试与导入外部数据使用：浅拷贝文件进工作区
    def import_board_file(self, source: Path, name: str = "") -> BoardMeta | None:
        """把一个已存在的看板 JSON 文件复制进工作区（极少用；导入走
        create_board(board=...)，此处仅供运维/测试）"""
        from app.models.json_io import load_json_doc as _load
        doc = _load(source)
        if not doc:
            return None
        meta = self.create_board(name=name, board=Board.from_dict(doc))
        return meta


def _new_board_id() -> str:
    import uuid
    return uuid.uuid4().hex


def legacy_migration_available(data_dir: Path) -> bool:
    """旧单看板文件是否存在（仅测试/诊断用）"""
    return (data_dir / LEGACY_BOARD_FILE).exists()


def remove_legacy_file(data_dir: Path) -> None:
    """删除旧单看板文件（迁移确认无误后的可选清理；默认不调用）"""
    target = data_dir / LEGACY_BOARD_FILE
    try:
        if target.exists():
            target.unlink()
    except OSError as e:
        logger.warning("清理旧看板文件失败: %s (%s)", target, e)

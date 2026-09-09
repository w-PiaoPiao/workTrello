"""
应用配置常量

所有配置集中在单处，方便管理和修改。
"""

import os
import sys
from pathlib import Path

from PySide6.QtCore import QSettings


def _settings() -> QSettings:
    return QSettings(AppConfig.APP_ORG, AppConfig.APP_NAME)


class AppConfig:
    """应用全局配置"""

    # 应用信息
    APP_NAME = "桌宠看板"
    APP_VERSION = "0.1.0"
    APP_ORG = "Personal"

    @classmethod
    def settings(cls) -> QSettings:
        """应用级 QSettings（统一组织/应用名）"""
        return _settings()

    # ── 窗口状态持久化键与存取（集中在 config，避免散落写 QSettings）──
    KEY_EXPANDED_SIZE = "window/expanded_size"
    KEY_WINDOW_POS = "window/pos"
    KEY_THEME_MODE = "theme/mode"
    KEY_ANIMATION_ENABLED = "window/pet_animation"
    KEY_ALWAYS_ON_TOP = "window/always_on_top"
    KEY_EMPTY_BOARD_ACK = "board/empty_board_ack"

    @classmethod
    def get_expanded_size(cls):
        """上次展开尺寸（QSize 或 None）"""
        return _settings().value(cls.KEY_EXPANDED_SIZE)

    @classmethod
    def save_expanded_size(cls, size) -> None:
        _settings().setValue(cls.KEY_EXPANDED_SIZE, size)

    @classmethod
    def get_window_pos(cls):
        """上次窗口位置（QPoint 或 None）"""
        return _settings().value(cls.KEY_WINDOW_POS)

    @classmethod
    def save_window_pos(cls, pos) -> None:
        _settings().setValue(cls.KEY_WINDOW_POS, pos)

    @classmethod
    def get_theme_mode(cls) -> str:
        """上次主题模式；缺省 "system" 表示跟随系统深浅色"""
        return _settings().value(cls.KEY_THEME_MODE, "system")

    @classmethod
    def save_theme_mode(cls, mode: str) -> None:
        _settings().setValue(cls.KEY_THEME_MODE, mode)

    @classmethod
    def get_animation_enabled(cls) -> bool:
        """桌宠空闲动画开关（默认开启）"""
        return _settings().value(cls.KEY_ANIMATION_ENABLED, True, type=bool)

    @classmethod
    def save_animation_enabled(cls, enabled: bool) -> None:
        _settings().setValue(cls.KEY_ANIMATION_ENABLED, enabled)

    @classmethod
    def get_always_on_top(cls) -> bool:
        """窗口置顶开关（默认开启；桌宠右键/托盘菜单可切换）"""
        return _settings().value(cls.KEY_ALWAYS_ON_TOP, True, type=bool)

    @classmethod
    def save_always_on_top(cls, on: bool) -> None:
        _settings().setValue(cls.KEY_ALWAYS_ON_TOP, on)

    @classmethod
    def get_empty_board_ack(cls) -> bool:
        """用户是否已确认过"看板为空"（空板恢复引导不再追问）"""
        return bool(_settings().value(cls.KEY_EMPTY_BOARD_ACK, False,
                                      type=bool))

    @classmethod
    def set_empty_board_ack(cls, on: bool = True) -> None:
        _settings().setValue(cls.KEY_EMPTY_BOARD_ACK, on)

    @classmethod
    def clear_empty_board_ack(cls) -> None:
        _settings().remove(cls.KEY_EMPTY_BOARD_ACK)

    # ── 数据路径 ──────────────────────────────────────────────
    _env_override = os.environ.get("PET_BOARD_DATA_DIR")
    if _env_override:
        DATA_DIR = Path(_env_override)
    else:
        try:
            import appdirs
            DATA_DIR = Path(appdirs.user_data_dir(APP_NAME, False))
        except ImportError:
            # 兜底（如 PyInstaller frozen 环境缺 appdirs）：仍落到系统
            # 数据目录，绝不回退 __file__ 旁路径——frozen 下 __file__ 指向
            # 每次启动都重建的 _MEIPASS 临时目录，数据会随进程消失
            if sys.platform == "win32":
                base = os.environ.get("LOCALAPPDATA") or str(Path.home())
            elif sys.platform == "darwin":
                base = str(Path.home() / "Library" / "Application Support")
            else:
                base = os.environ.get("XDG_DATA_HOME") \
                    or str(Path.home() / ".local" / "share")
            DATA_DIR = Path(base) / APP_NAME

    BOARD_FILE = "board.json"

    @classmethod
    def board_path(cls) -> Path:
        """看板数据文件路径（确保目录存在）"""
        cls.DATA_DIR.mkdir(parents=True, exist_ok=True)
        return cls.DATA_DIR / cls.BOARD_FILE

    # ── 窗口尺寸与位置 ────────────────────────────────────────
    # 折叠态（桌宠）
    PET_WIDTH = 132
    PET_HEIGHT = 132
    PET_BADGE_SIZE = 22
    PET_CLICK_THRESHOLD = 8
    PET_CANVAS_MARGIN = 14      # 绘制边距（容纳漂浮/跳跃超程，避免裁剪）
    PET_FLOAT_MS = 1600
    PET_FLOAT_DELTA = 5
    PET_BREATH_MS = 1800
    PET_BREATH_RATIO = 0.05
    PET_JUMP_HEIGHT = 12
    PET_IDLE_ACTION_MIN_MS = 6000
    PET_IDLE_ACTION_MAX_MS = 14000

    # 展开态（看板）
    BOARD_WIDTH = 1080
    BOARD_HEIGHT = 640
    BOARD_MIN_WIDTH = 760
    BOARD_MIN_HEIGHT = 480
    BOARD_MAX_WIDTH = 1920
    BOARD_MAX_HEIGHT = 1200
    LIST_WIDTH = 272            # 单个看板列表宽度
    ANIMATION_MS = 240          # 折叠/展开动画时长
    SCREEN_MARGIN = 20
    RESIZE_MARGIN = 6           # Windows 边缘拖拽缩放的命中宽度（像素）

    # ── 交互 ──────────────────────────────────────────────────────
    CARD_DRAG_THRESHOLD = 10    # 卡片按下后超过该位移才判定为拖拽（像素）
    LIST_DRAG_THRESHOLD = 10    # 列表头按下后超过该位移才判定为整列拖拽（像素）
    CARD_DELETE_BTN_H = 14      # 卡片右上角悬浮删除按钮的命中尺寸
    EDGE_CURSOR_POLL_MS = 150   # Windows 展开态光标轮询间隔（系统缩放后事件链可能断裂）

    # ── 桌宠随机小动作动画时长（ms）───────────────────────────────
    TILT_PHASE_MS = (260, 320, 300)      # 歪头三段（歪出/歪回/回正）
    JUMP_UP_MS = 200                     # 起跳
    JUMP_DOWN_MS = 180                   # 落地
    SQUASH_MS = 110                      # 压扁
    SQUASH_RECOVER_MS = 160              # 回弹
    HOVER_UP_MS = 160                    # 悬停放大
    HOVER_DOWN_MS = 200                  # 悬停还原

    # ── 行为选项 ──────────────────────────────────────────────
    NOTIFICATION_DURATION_MS = 2000
    SAVE_DEBOUNCE_MS = 500
    SIZE_SAVE_DEBOUNCE_MS = 200       # 窗口尺寸持久化防抖（系统缩放循环按帧触发 resizeEvent）
    SEARCH_DEBOUNCE_MS = 150             # 搜索框逐键过滤防抖间隔（输入停顿后才刷新）
    DUE_CHECK_INTERVAL_MS = 60 * 60 * 1000   # 截止提醒检查间隔（1 小时）
    UNDO_LIMIT = 20                          # 撤销快照保留步数
    POMODORO_MINUTES = 25                    # 番茄钟时长（分钟）

    # ── 颜色（浅色）──────────────────────────────────────────
    COLORS = {
        "bg_primary": "#EEF2F7",
        "bg_panel": "rgba(255, 255, 255, 0.86)",
        "bg_card": "#FFFFFF",
        "bg_hover": "#E7F0FB",
        "text_primary": "#1D2733",
        "text_secondary": "#5C6B7A",
        "text_disabled": "#A5B0BC",
        "accent": "#2F6BFF",
        "accent_hover": "#1F58E0",
        "accent_soft": "rgba(47, 107, 255, 0.12)",
        "success": "#1FA971",
        "danger": "#E5484D",
        "warning": "#F5A623",
        "border": "rgba(29, 39, 51, 0.10)",
        "board_bg_start": "#5B8DEF",
        "board_bg_mid": "#7C6CE8",
        "board_bg_end": "#B46BE8",
    }

    # ── 颜色（深色）──────────────────────────────────────────
    DARK_COLORS = {
        "bg_primary": "#16181D",
        "bg_panel": "rgba(35, 38, 46, 0.92)",
        "bg_card": "#262A33",
        "bg_hover": "#333947",
        "text_primary": "#E6EAF0",
        "text_secondary": "#9AA5B4",
        "text_disabled": "#5C6672",
        "accent": "#5B8DEF",
        "accent_hover": "#7CA6F5",
        "accent_soft": "rgba(91, 141, 239, 0.22)",
        "success": "#3FCF8E",
        "danger": "#FF6B6E",
        "warning": "#FFB65C",
        "border": "rgba(230, 234, 240, 0.12)",
        "board_bg_start": "#1B2233",
        "board_bg_mid": "#221B36",
        "board_bg_end": "#2E1B3A",
    }

    # ── 卡片标签色板（key → (背景, 文字)）────────────────────
    LABEL_COLORS = {
        "blue":   ("#DAE8FF", "#1D4ED8"),
        "green":  ("#D7F5E0", "#15803D"),
        "orange": ("#FFE8CC", "#C2570A"),
        "red":    ("#FFDADA", "#B91C1C"),
        "purple": ("#EBDBFF", "#6D28D9"),
        "teal":   ("#D2F2F0", "#0F766E"),
    }

    # ── 列表（Trello 列）配色点缀 ───────────────────────────
    LIST_ACCENTS = ["#2F6BFF", "#1FA971", "#F5A623", "#E5484D",
                    "#8B5CF6", "#0EA5A5", "#EC4899"]

    # ── 平台检测 ──────────────────────────────────────────────
    IS_WINDOWS = sys.platform == "win32"
    IS_MACOS = sys.platform == "darwin"

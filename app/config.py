"""
应用配置常量

所有配置集中在单处，方便管理和修改。
"""

import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import QSettings


# 按 env key 缓存的 QSettings 单例（PET_BOARD_DATA_DIR 进程内不变，
# key 维度切分即可保住测试隔离）。此前每次读写都新建 QSettings：
# 构造涉及路径解析，析构还会触发整份配置 sync 写盘，而读写是高频路径
# （窗口位置/尺寸、折叠列、提醒日志每分钟都在碰）。
_settings_cache: dict[str, QSettings] = {}


def _settings() -> QSettings:
    """应用设置（组织/应用名定位 QSettings）

    数据目录被 PET_BOARD_DATA_DIR 改写时（测试隔离、便携模式），设置一并
    落到该目录下的 INI，不再写系统位置。此前只隔离了数据目录、设置仍写
    真实注册表，测试跑一遍就会覆盖用户的真实偏好（曾把看板尺寸改成测试值、
    把冒烟测试的提醒记录写进用户注册表）。

    注：QSettings.setDefaultFormat(IniFormat) 在 Windows 上对
    QSettings(org, app) 这种两参构造无效（实测 fileName 仍指向注册表），
    故这里显式传 INI 路径。
    """
    override = os.environ.get("PET_BOARD_DATA_DIR") or ""
    inst = _settings_cache.get(override)
    if inst is None:
        if override:
            ini = Path(override) / "settings.ini"
            try:
                ini.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
            inst = QSettings(str(ini), QSettings.IniFormat)
        else:
            inst = QSettings(AppConfig.APP_ORG, AppConfig.APP_NAME)
        _settings_cache[override] = inst
    return inst


class AppConfig:
    """应用全局配置"""

    # 应用信息
    APP_NAME = "桌宠看板"
    APP_VERSION = "0.1.3"
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
    KEY_PET_SKIN = "pet/skin"
    KEY_REMIND_LOG = "remind/log"
    KEY_COLLAPSED_LISTS = "board/collapsed_lists"
    KEY_CARD_DIALOG_SIZE = "dialog/card_size"

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

    # ── 桌宠皮肤 ────────────────────────────────────────────

    @classmethod
    def get_pet_skin(cls) -> str:
        """上次选择的桌宠皮肤 key（非法/缺失回退默认"milk"）"""
        key = str(_settings().value(cls.KEY_PET_SKIN, "milk"))
        return key if key in cls.PET_SKINS else "milk"

    @classmethod
    def save_pet_skin(cls, key: str) -> None:
        _settings().setValue(cls.KEY_PET_SKIN, key)

    # ── 截止提醒日志（逐卡每天一次，防重启/防轰炸）─────────────

    @classmethod
    def get_remind_log(cls) -> dict[str, list[str]]:
        """{日期 ISO: [card_id:due:kind, ...]}，损坏时回退空表"""
        raw = _settings().value(cls.KEY_REMIND_LOG, "")
        try:
            val = json.loads(raw) if isinstance(raw, str) else {}
        except (TypeError, ValueError):
            return {}
        if not isinstance(val, dict):
            return {}
        return {str(k): [str(x) for x in v if isinstance(x, str)]
                for k, v in val.items() if isinstance(v, list)}

    @classmethod
    def save_remind_log(cls, log: dict[str, list[str]]) -> None:
        _settings().setValue(cls.KEY_REMIND_LOG,
                             json.dumps(log, ensure_ascii=False))

    # ── 折叠列表（会话之间保持列折叠状态） ──────────────────

    @classmethod
    def get_collapsed_lists(cls) -> set[str]:
        raw = _settings().value(cls.KEY_COLLAPSED_LISTS, "")
        try:
            val = json.loads(raw) if isinstance(raw, str) else []
        except (TypeError, ValueError):
            return set()
        if not isinstance(val, list):
            return set()
        return {str(x) for x in val if isinstance(x, str)}

    @classmethod
    def save_collapsed_lists(cls, ids: set[str]) -> None:
        _settings().setValue(cls.KEY_COLLAPSED_LISTS,
                             json.dumps(sorted(ids), ensure_ascii=False))

    # ── 卡片对话框尺寸（会话之间保持用户调过的宽高）──────────

    @classmethod
    def get_card_dialog_size(cls):
        """上次的卡片对话框尺寸（QSize 或 None）"""
        return _settings().value(cls.KEY_CARD_DIALOG_SIZE)

    @classmethod
    def save_card_dialog_size(cls, size) -> None:
        _settings().setValue(cls.KEY_CARD_DIALOG_SIZE, size)

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
    # 最小宽度需容纳工具栏全部控件（含搜索框最小宽），否则窄窗口下
    # 右侧按钮会被挤出窗口
    BOARD_MIN_WIDTH = 980
    BOARD_MIN_HEIGHT = 480
    BOARD_MAX_WIDTH = 1920
    BOARD_MAX_HEIGHT = 1200
    LIST_WIDTH = 272            # 单个看板列表宽度
    ANIMATION_MS = 240          # 折叠/展开动画时长
    SCREEN_MARGIN = 20
    RESIZE_MARGIN = 6           # Windows 边缘拖拽缩放的命中宽度（像素）

    # 卡片对话框（新建/编辑卡片）
    # 宽高均可调并记住上次值；此前宽度被 setFixedWidth 锁死、高度可拉伸，
    # 只能纵向拉、不能横向拉。最小宽需容纳一行标签色块（6 个 chip）
    CARD_DIALOG_WIDTH = 460
    CARD_DIALOG_HEIGHT = 520
    CARD_DIALOG_MIN_WIDTH = 380
    CARD_DIALOG_MIN_HEIGHT = 320

    # ── 过渡动画（毫秒）──────────────────────────────────────────
    # 全部经 app/views/motion.py 下发："暂停动画"开关一关即整体退化为瞬时切换
    COLLAPSE_ANIM_MS = 200      # 列折叠/展开（只动高度，状态机在终点一次刷齐）
    CARD_ANIM_MS = 140          # 卡片新增淡入
    CARD_EXIT_ANIM_MS = 120     # 卡片删除淡出
    POPOVER_ANIM_MS = 130       # 弹层淡入（备注浮层 / 今日清单）
    HOVER_ANIM_MS = 110         # 悬停控件淡入（卡片右上角删除按钮）
    # 单次刷新最多为多少张卡播放增删动画：超出则直接切换。
    # 实测同时动画 60 张卡约 1.5ms/帧（占 60fps 预算 9%），搜索/过滤这类
    # 批量增删必须设上限，否则逐键输入会把多轮动画叠在一起。
    ANIM_BATCH_LIMIT = 8

    # ── 交互 ──────────────────────────────────────────────────────
    CARD_DRAG_THRESHOLD = 10    # 卡片按下后超过该位移才判定为拖拽（像素）
    LIST_DRAG_THRESHOLD = 10    # 列表头按下后超过该位移才判定为整列拖拽（像素）
    CARD_DELETE_BTN_H = 20      # 卡片右上角悬浮删除按钮的命中尺寸
    CARD_META_BADGE_MAX = 4     # 卡片信息行徽章数硬上限（实际显示几个另按卡片
    #                             实宽决定，装不下折成 …，见 _fit_meta_badges）
    EDGE_CURSOR_POLL_MS = 150   # Windows 展开态光标轮询间隔（系统缩放后事件链可能断裂）
    DRAG_AUTO_SCROLL_EDGE_PX = 44    # 卡片拖拽距视口该距离内触发自动滚动（像素）
    DRAG_AUTO_SCROLL_STEP = 20       # 自动滚动每帧位移（像素）
    DRAG_AUTO_SCROLL_MS = 30         # 自动滚动帧间隔（毫秒）

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
    DUE_CHECK_INTERVAL_MS = 60 * 1000    # 截止提醒检查间隔（1 分钟，桌宠常驻成本极低）
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
        # 工具栏「玻璃白」：白色半透明面浮在渐变背景上（浅色主题）
        "glass": "rgba(255, 255, 255, 0.70)",
        "glass_hover": "rgba(255, 255, 255, 0.92)",
        # 备注悬浮预览：底色必须不透明——半透明时下方卡片文字会透上来，
        # 与备注正文叠成重影导致读不清（bg_panel 是给看板列的半透明色，
        # 不能复用）。边框比 border 重一档，白底浮层压在白卡上才有边界。
        "popover_bg": "#FFFFFF",
        "popover_border": "rgba(29, 39, 51, 0.26)",
        "popover_shadow": "#0F1621",
        # 面板内中性遮罩（列计数、卡片删除钮等叠在列/卡面上）
        "mask": "rgba(128, 128, 128, 0.15)",
        "mask_hover": "rgba(128, 128, 128, 0.30)",
        # 滚动条把手
        "scroll_handle": "rgba(128, 128, 128, 0.35)",
        "scroll_handle_hover": "rgba(128, 128, 128, 0.55)",
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
        # 工具栏「玻璃白」：深色下为低透明白，靠提亮而非变灰区分层级
        "glass": "rgba(255, 255, 255, 0.12)",
        "glass_hover": "rgba(255, 255, 255, 0.20)",
        # 备注悬浮预览：底色比 bg_card 亮一档，深色下也"浮"在卡片之上
        "popover_bg": "#2E323C",
        "popover_border": "rgba(230, 234, 240, 0.22)",
        "popover_shadow": "#000000",
        "mask": "rgba(128, 128, 128, 0.15)",
        "mask_hover": "rgba(128, 128, 128, 0.30)",
        "scroll_handle": "rgba(128, 128, 128, 0.35)",
        "scroll_handle_hover": "rgba(128, 128, 128, 0.55)",
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

    # ── 标签色中文名（卡片色条 / 对话框色块的悬浮提示）──────────
    LABEL_NAMES = {
        "blue": "蓝色",
        "green": "绿色",
        "orange": "橙色",
        "red": "红色",
        "purple": "紫色",
        "teal": "青色",
    }

    # ── 重复周期（卡片完成时自动滚动截止日期）─────────────────
    REPEAT_NAMES = {"never": "", "daily": "每日", "weekly": "每周"}

    # ── 优先级（0=无 1=高 2=中 3=低）──────────────────────
    PRIORITY_NAMES = {0: "", 1: "高", 2: "中", 3: "低"}
    PRIORITY_MARKS = {0: "", 1: "P1", 2: "P2", 3: "P3"}

    # ── 皮肤中文名（桌宠右键"换皮肤"）───────────────────────
    SKIN_NAMES = {
        "milk": "奶糖",
        "snow": "雪团",
        "choco": "可可",
        "midnight": "子夜",
    }

    # ── 列表（Trello 列）配色点缀 ───────────────────────────
    LIST_ACCENTS = ["#2F6BFF", "#1FA971", "#F5A623", "#E5484D",
                    "#8B5CF6", "#0EA5A5", "#EC4899"]

    # ── 桌宠皮肤（key → 配色；全部 RGBA 元组，QColor(*value) 使用）──
    PET_SKINS = {
        "milk": {   # 默认：奶白 + 黄油耳朵
            "name": "奶糖",
            "body_top": (255, 247, 234, 255),
            "body_bottom": (255, 227, 194, 255),
            "outline": (138, 90, 43, 255),
            "ear_inner": (255, 184, 77, 255),
            "belly": (255, 255, 255, 130),
            "blush": (255, 150, 140, 90),
            "eye": (59, 42, 26, 255),
            "sweat": (150, 205, 255, 200),
        },
        "snow": {   # 雪兔：白 + 粉耳
            "name": "雪团",
            "body_top": (255, 255, 255, 255),
            "body_bottom": (238, 244, 250, 255),
            "outline": (122, 138, 158, 255),
            "ear_inner": (255, 190, 210, 255),
            "belly": (255, 255, 255, 200),
            "blush": (255, 170, 190, 100),
            "eye": (52, 66, 82, 255),
            "sweat": (130, 185, 235, 200),
        },
        "choco": {  # 可可：焦糖棕
            "name": "可可",
            "body_top": (214, 171, 132, 255),
            "body_bottom": (174, 124, 88, 255),
            "outline": (94, 60, 36, 255),
            "ear_inner": (255, 205, 158, 255),
            "belly": (255, 238, 220, 180),
            "blush": (240, 130, 110, 100),
            "eye": (48, 30, 18, 255),
            "sweat": (150, 205, 255, 200),
        },
        "midnight": {   # 子夜：黑猫 + 琥珀眼
            "name": "子夜",
            "body_top": (64, 70, 92, 255),
            "body_bottom": (44, 48, 66, 255),
            "outline": (20, 24, 36, 255),
            "ear_inner": (255, 196, 110, 255),
            "belly": (120, 128, 150, 140),
            "blush": (255, 140, 130, 80),
            "eye": (255, 205, 100, 255),
            "sweat": (140, 195, 255, 230),
        },
    }

    # ── 平台检测 ──────────────────────────────────────────────
    IS_WINDOWS = sys.platform == "win32"
    IS_MACOS = sys.platform == "darwin"

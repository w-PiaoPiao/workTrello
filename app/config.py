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
    APP_VERSION = "0.3.0"
    APP_ORG = "Personal"

    @classmethod
    def settings(cls) -> QSettings:
        """应用级 QSettings（统一组织/应用名）"""
        return _settings()

    # ── 窗口状态持久化键与存取（集中在 config，避免散落写 QSettings）──
    KEY_EXPANDED_SIZE = "window/expanded_size"
    KEY_WINDOW_POS = "window/pos"
    KEY_THEME_MODE = "theme/mode"
    KEY_LANGUAGE = "app/language"
    KEY_ANIMATION_ENABLED = "window/pet_animation"
    KEY_ALWAYS_ON_TOP = "window/always_on_top"
    KEY_EMPTY_BOARD_ACK = "board/empty_board_ack"
    KEY_PET_SKIN = "pet/skin"
    KEY_REMIND_LOG = "remind/log"
    KEY_COLLAPSED_LISTS = "board/collapsed_lists"
    KEY_CARD_DIALOG_SIZE = "dialog/card_size"
    KEY_LAST_WORKDIR = "dialog/last_workdir"
    KEY_AUTOSTART = "app/autostart"
    KEY_DEFAULT_VIEW = "window/default_view"
    KEY_PET_ENABLED = "window/pet_enabled"
    KEY_UI_SCALE = "ui/scale"
    KEY_UI_SKIN = "ui/skin"

    # ── 界面密度（字号 / 间距 / 圆角统一缩放）──────────────────
    # 此前全应用没有任何字号或密度设置，界面文字固定 10–13px 硬编码在 65 处。
    # 4K/5K 屏 1:1 缩放下 10–11px 已到可读边缘，低视力用户除了改系统缩放
    # （而固定像素布局对系统缩放容忍度差）别无他法。
    UI_SCALES = ("compact", "normal", "comfortable")

    @classmethod
    def get_ui_scale(cls) -> str:
        """界面密度档位；非法值回退 normal

        与 get_theme_mode / get_language 同一白名单范式：偏好被改坏时回退到
        中间档，而不是让界面以一个界面上选不出来的状态呈现。
        """
        val = str(_settings().value(cls.KEY_UI_SCALE, "normal") or "")
        return val if val in cls.UI_SCALES else "normal"

    @classmethod
    def save_ui_scale(cls, scale: str) -> None:
        if scale not in cls.UI_SCALES:
            return
        _settings().setValue(cls.KEY_UI_SCALE, scale)

    # ── 视觉皮肤（与界面密度正交：密度管尺寸，皮肤管配色与造型）──────
    #
    # 每个皮肤 = (浅色色表, 深色色表, 造型 dict)。色表沿用 COLORS /
    # DARK_COLORS 的键集合（tests/test_design_tokens.py 会校验键一致），
    # 造型 dict 覆盖圆角阶梯 / 字重 / 徽章形态 / 骨架绘制方式。
    #
    # 为什么单开一张表而不是替换 COLORS：COLORS / DARK_COLORS 是"默认皮肤"
    # 的别名（多处按名字直接引用），皮肤切换只改 AppTheme 取哪一份，默认
    # 皮肤的表原地不动，现有代码与历史快照语义不变。
    UI_SKIN_DEFAULT = "editorial"

    @classmethod
    def get_ui_skin(cls) -> str:
        """当前视觉皮肤 key；缺省/非法回退默认皮肤

        与 get_ui_scale 同一白名单范式：偏好被手改或旧版本遗留未知值时，
        退回默认皮肤而不是让界面落到一个选不出来的状态。
        """
        val = str(_settings().value(cls.KEY_UI_SKIN, cls.UI_SKIN_DEFAULT) or "")
        return val if val in cls.UI_SKINS else cls.UI_SKIN_DEFAULT

    @classmethod
    def save_ui_skin(cls, key: str) -> None:
        if key not in cls.UI_SKINS:
            return
        _settings().setValue(cls.KEY_UI_SKIN, key)

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
        """上次主题模式；缺省/损坏回退 "system"（跟随系统深浅色）

        与 get_language 等其他偏好同一白名单范式：QSettings 值被改坏时
        设置页主题分段会出现"无选中"且静默回退浅色，这里统一兜底。
        """
        mode = _settings().value(cls.KEY_THEME_MODE, "system")
        return mode if mode in ("system", "light", "dark") else "system"

    @classmethod
    def save_theme_mode(cls, mode: str) -> None:
        _settings().setValue(cls.KEY_THEME_MODE, mode)

    @classmethod
    def get_language(cls) -> str:
        """界面语言（zh/en）；无记录时按系统语言探测（首次启动默认）"""
        val = str(_settings().value(cls.KEY_LANGUAGE, "") or "")
        if val in ("zh", "en"):
            return val
        from app.i18n import detect_language
        return detect_language()

    @classmethod
    def save_language(cls, lang: str) -> None:
        _settings().setValue(cls.KEY_LANGUAGE, lang)

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
    def get_pet_enabled(cls) -> bool:
        """桌宠显示开关（默认开启；关闭后折叠形态不存在，收起即隐藏到托盘）"""
        return _settings().value(cls.KEY_PET_ENABLED, True, type=bool)

    @classmethod
    def save_pet_enabled(cls, on: bool) -> None:
        _settings().setValue(cls.KEY_PET_ENABLED, on)

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

    KEY_REMIND_ADVANCE = "remind/advance_days"

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

    @classmethod
    def get_remind_advance(cls) -> int:
        """截止日提前提醒天数（0=仅当天/逾期；1~3=提前 N 天也开始提醒）"""
        try:
            return max(0, min(3, int(_settings().value(
                cls.KEY_REMIND_ADVANCE, 0) or 0)))
        except (TypeError, ValueError):
            return 0

    @classmethod
    def save_remind_advance(cls, days: int) -> None:
        _settings().setValue(cls.KEY_REMIND_ADVANCE,
                             max(0, min(3, int(days))))

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

    # ── 工作目录选择器的起始位置 ──────────────────────────────

    @classmethod
    def get_last_workdir(cls) -> str:
        """上次选过的工作目录（可能已被移动/拔出，用前须验活）"""
        return str(_settings().value(cls.KEY_LAST_WORKDIR, "") or "")

    @classmethod
    def save_last_workdir(cls, path: str) -> None:
        _settings().setValue(cls.KEY_LAST_WORKDIR, path)

    # ── 开机自启动偏好 ────────────────────────────────────────

    @classmethod
    def get_autostart(cls) -> bool:
        """用户是否要开机自启动（默认关闭：不主动往系统启动项里写东西）"""
        return _settings().value(cls.KEY_AUTOSTART, False, type=bool)

    @classmethod
    def save_autostart(cls, on: bool) -> None:
        _settings().setValue(cls.KEY_AUTOSTART, on)

    # ── 全局快捷键（应用失焦时唤起快速添加；仅 Windows）────────
    KEY_GLOBAL_HOTKEY = "app/global_hotkey"
    GLOBAL_HOTKEY_SEQUENCE = "Ctrl+Alt+N"

    @classmethod
    def get_global_hotkey(cls) -> bool:
        """是否启用全局快捷键（默认关闭：不主动占系统级热键）"""
        return _settings().value(cls.KEY_GLOBAL_HOTKEY, False, type=bool)

    @classmethod
    def save_global_hotkey(cls, on: bool) -> None:
        _settings().setValue(cls.KEY_GLOBAL_HOTKEY, on)

    # ── 默认打开形态（启动 / 从托盘显示以哪种形态出现）────────
    DEFAULT_VIEWS = ("pet", "board")

    @classmethod
    def get_default_view(cls) -> str:
        """默认打开形态："pet" 桌宠（折叠）/"board" 展开看板

        非法值一律回退 pet：偏好损坏时宁可退回最保守的形态，也不能让窗口
        以一个界面上选不出来的状态打开。
        """
        val = str(_settings().value(cls.KEY_DEFAULT_VIEW, "pet") or "")
        return val if val in cls.DEFAULT_VIEWS else "pet"

    @classmethod
    def save_default_view(cls, view: str) -> None:
        if view not in cls.DEFAULT_VIEWS:
            return
        _settings().setValue(cls.KEY_DEFAULT_VIEW, view)

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
    # 单击 vs 拖拽的判定阈值（曼哈顿距离）。原为 8px（每轴约 6px）——落在
    # 「用户还会顺手挪动这个 132px 目标」的正常手抖范围内：想把桌宠挪几个
    # 像素，却触发了整个窗口的形态切换，是本应用最刺眼的一次误触。
    # 与 CARD_DRAG_THRESHOLD(10) 对齐并略高。
    PET_CLICK_THRESHOLD = 12
    PET_CANVAS_MARGIN = 14      # 绘制边距（容纳漂浮/跳跃超程，避免裁剪）
    PET_FLOAT_MS = 1600
    PET_FLOAT_DELTA = 5
    PET_BREATH_MS = 1800
    PET_BREATH_RATIO = 0.05
    PET_JUMP_HEIGHT = 12
    PET_IDLE_ACTION_MIN_MS = 6000
    PET_IDLE_ACTION_MAX_MS = 14000
    PET_IDLE_FPS_TICK_MS = 33   # 空闲漂浮/呼吸的驱动帧间隔（≈30fps）：秒级
    #                             缓动 30fps 视觉无差，常驻重绘开销减半

    # 展开态（看板）
    BOARD_WIDTH = 1080
    BOARD_HEIGHT = 640
    # 最小宽度需容纳工具栏全部控件（含搜索框最小宽），否则窄窗口下
    # 右侧按钮会被挤出窗口
    BOARD_MIN_WIDTH = 980
    BOARD_MIN_HEIGHT = 480
    BOARD_MAX_WIDTH = 1920
    BOARD_MAX_HEIGHT = 1200
    LIST_WIDTH = 272            # 单个看板列表宽度（基准值，用时经 tokens.size 缩放）
    ANIMATION_MS = 240          # 折叠/展开动画时长
    SCREEN_MARGIN = 20
    RESIZE_MARGIN = 6           # Windows 边缘拖拽缩放的命中宽度（像素）

    # 卡片对话框（新建/编辑卡片）
    # 宽高均可调并记住上次值；此前宽度被 setFixedWidth 锁死、高度可拉伸，
    # 只能纵向拉、不能横向拉。最小宽需容纳一行标签色块（6 个 chip）。
    # 默认高按"空卡表单完整放下"取值（表单自然高约 573 + 按钮行与边距约 70），
    # 免得新建卡片一打开就得出滚动条；内容超出时表单区滚动，按钮行固定在底部
    CARD_DIALOG_WIDTH = 460
    CARD_DIALOG_HEIGHT = 660
    CARD_DIALOG_MIN_WIDTH = 380
    CARD_DIALOG_MIN_HEIGHT = 360

    # ── 过渡动画（毫秒）──────────────────────────────────────────
    # 全部经 app/views/motion.py 下发："暂停动画"开关一关即整体退化为瞬时切换
    COLLAPSE_ANIM_MS = 200      # 列折叠/展开（只动高度，状态机在终点一次刷齐）
    CARD_ANIM_MS = 140          # 卡片新增淡入
    CARD_EXIT_ANIM_MS = 120     # 卡片删除淡出
    POPOVER_ANIM_MS = 130       # 弹层淡入（备注浮层 / 今日清单）
    HOVER_ANIM_MS = 110         # 悬停控件淡入（卡片右上角删除按钮）
    SEGMENT_PILL_MS = 160       # 分段控件选中高亮块滑动（设置页主题/语言/形态）
    # 单次刷新最多为多少张卡播放增删动画：超出则直接切换。
    # 实测同时动画 60 张卡约 1.5ms/帧（占 60fps 预算 9%），搜索/过滤这类
    # 批量增删必须设上限，否则逐键输入会把多轮动画叠在一起。
    ANIM_BATCH_LIMIT = 8

    # ── 界面尺寸 token（同语义控件跨界面统一）──────────────────
    # 此前同一种"药丸选择器"在三个对话框里有三套圆角与内边距
    # （6/7/9px 圆角 × 三种 padding），次按钮有四种 padding，
    # 靠人工记忆维持一致不可持续。
    UI_RADIUS_SHELL = 14         # 看板列外壳
    UI_RADIUS_PANEL = 12         # 分区卡片 / 浮层外壳
    UI_RADIUS_CARD = 10          # 卡片
    UI_RADIUS_CONTROL = 8        # 输入框 / 按钮 / 列表行
    UI_RADIUS_CHIP = 6           # 徽章 / 色块 / 键帽
    UI_RADIUS_PILL = 8           # 药丸选择器（分段按钮 / 皮肤 / 优先级）
    # 圆形一律按 size/2 现算（见 tokens.circle_radius），不再写死 17px
    UI_PAD_PRIMARY = "7px 22px"      # 主按钮
    UI_PAD_SECONDARY = "6px 14px"    # 次按钮
    UI_PAD_PILL = "4px 12px"         # 药丸选择器
    UI_PAD_INPUT = "6px 10px"        # 输入框（常规）
    UI_PAD_INPUT_DENSE = "4px 8px"   # 输入框（紧凑：日期 / 数字 / 下拉）
    UI_DIALOG_MARGIN_H = 20      # 对话框内容左右边距
    UI_DIALOG_MARGIN_V = 18      # 对话框内容上下边距

    # ── 字号阶梯（px）──────────────────────────────────────────
    # 此前 65 处 font-size 硬编码在 7 个档位上（10/11/12/13/14/15/17），
    # 同一语义角色在不同界面取不同值（分区标题在设置页 12px、在备注浮层
    # 10px）。收敛成 5 档，并**取消 10px**——它低于两个平台舒适可读的下限。
    UI_FONT_DISPLAY = 17         # 看板标题（应用级标题）
    UI_FONT_TITLE = 15           # 对话框 / 分区标题、日历月份、列标题
    UI_FONT_BODY = 13            # 卡片标题、正文、表单文字
    UI_FONT_LABEL = 12           # 按钮、统计、次要标签
    UI_FONT_CAPTION = 11         # 徽章、提示（最小档，不得再低）

    # ── 间距阶梯（px）──────────────────────────────────────────
    # QSS padding 曾有 24 种取值、setContentsMargins 21 种组合、setSpacing
    # 8 种取值，同类字段高度相差 1~2px。取 8 档覆盖实际用到的全部区间。
    UI_SPACE = (2, 4, 6, 8, 12, 16, 20, 24)

    # ── 交互 ──────────────────────────────────────────────────────
    CARD_DRAG_THRESHOLD = 10    # 卡片按下后超过该位移才判定为拖拽（像素）
    LIST_DRAG_THRESHOLD = 10    # 列表头按下后超过该位移才判定为整列拖拽（像素）
    LIST_DRAG_GHOST_MAX_H = 240  # 整列拖拽拖影最大高度（长列不再整列截图）
    CARD_DELETE_BTN_H = 24      # 卡片右上角悬浮删除按钮的命中尺寸（≥24 可点击标准）
    BOARD_WHEEL_STEP = 180      # 看板区垂直滚轮每格对应的横向滚动量（像素，半列）
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
    REMIND_SNOOZE_MINUTES = 10           # 「稍后提醒」推迟的分钟数
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
        # 提示/占位文字专用。此前这些位置借用了 text_disabled，实测
        # 搜索框占位 1.58:1、空列提示 1.87:1 —— 把"禁用态"的色当
        # "说明性文字"用，是这两处读不清的根因。
        "text_muted": "#5C6B7A",
        "text_disabled": "#A5B0BC",
        # accent 同时用作填充与文字：原 #2F6BFF 白字压上去只有 4.50:1
        # （正好卡在门槛上），当文字压在白底也是 4.50。压深一档后
        # 两个方向分别到 5.40 / 4.80。
        "accent": "#2A60E4",
        "accent_hover": "#2557D6",
        "accent_on": "#FFFFFF",      # 压在 accent 填充上的文字/字形
        "accent_text": "#2A60E4",    # accent 当文字用（徽章 / 链接）
        "accent_soft": "rgba(42, 96, 228, 0.12)",
        "success": "#1FA971",
        "success_soft": "#E2F6EC",
        "success_text": "#177C53",
        "success_on": "#FFFFFF",
        "danger": "#E5484D",
        "danger_soft": "#FDE7E8",
        "danger_text": "#BD3B3F",
        "danger_on": "#FFFFFF",
        "warning": "#F5A623",
        "warning_soft": "#FFF1DA",
        "warning_text": "#946415",
        # 浅色的 warning 填充是亮橙：白字压上去只有 2.03:1，必须用深色字
        "warning_on": "#10131A",
        # 中性软底 + 前景（备注 / 附件这类"无严重性"的徽章）。
        # 单独取前景而不是复用 text_secondary：后者在深色软底上只有 4.30:1。
        "neutral_soft": "#EDF1F5",
        "neutral_text": "#5C6B7A",
        # 焦点环单独取色：accent 直接当焦点环时，深色下压在 glass_hover 上
        # 只有 2.57:1，低于非文本组件 3:1 的门槛。
        "focus_ring": "#2A60E4",
        "border": "rgba(29, 39, 51, 0.14)",
        "board_bg_start": "#EEF2F7",
        "board_bg_mid": "#EBEFF6",
        "board_bg_end": "#E7ECF5",
        # 工具栏「玻璃白」：白色半透明面浮在板底上（浅色主题）
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
        # 空看板引导：板底改为中性后它不再是"叠在渐变上的亮色"，而是压在
        # 近中性底上的说明文字。原 rgba(255,255,255,0.92) 在浅色板底上
        # 只有 2.98:1——新手唯一能看到的一句引导却是最读不清的文字。
        "hint_on_board": "#5C6B7A",
    }

    # ── 颜色（深色）──────────────────────────────────────────
    DARK_COLORS = {
        "bg_primary": "#16181D",
        "bg_panel": "rgba(35, 38, 46, 0.92)",
        "bg_card": "#262A33",
        "bg_hover": "#333947",
        "text_primary": "#E6EAF0",
        "text_secondary": "#9AA5B4",
        "text_muted": "#A7B1BF",
        "text_disabled": "#5C6672",
        # 深色下 accent 保持明亮作填充，但填充上的文字必须反相：
        # 白字压 #5B8DEF 只有 3.23:1，而 #10131A 有 5.75:1。
        # 与 label_style 的深色反相是同一套思路。
        "accent": "#5B8DEF",
        "accent_hover": "#7CA6F5",
        "accent_on": "#10131A",
        "accent_text": "#82ACFF",
        "accent_soft": "rgba(91, 141, 239, 0.22)",
        "success": "#3FCF8E",
        "success_soft": "#2A4843",
        "success_text": "#3FCF8E",
        "success_on": "#10131A",
        "danger": "#FF6B6E",
        "danger_soft": "#4D363E",
        "danger_text": "#FF8284",
        "danger_on": "#10131A",
        "warning": "#FFB65C",
        "warning_soft": "#4D433A",
        "warning_text": "#FFB65C",
        "warning_on": "#10131A",
        "neutral_soft": "#393E48",
        "neutral_text": "#A7B1BF",
        "focus_ring": "#8FB4FF",
        "border": "rgba(230, 234, 240, 0.16)",
        "board_bg_start": "#16181D",
        "board_bg_mid": "#181B22",
        "board_bg_end": "#1B1F27",
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
        "hint_on_board": "#9AA5B4",
    }

    # ── 图片扩展名（附件图标 / 拖入文件的看图判定）────────────
    IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")

    # ── 卡片标签色板（key → (背景, 文字)）────────────────────
    LABEL_COLORS = {
        "blue":   ("#DAE8FF", "#1D4ED8"),
        "green":  ("#D7F5E0", "#147B3A"),
        "orange": ("#FFE8CC", "#AC4D09"),
        "red":    ("#FFDADA", "#B91C1C"),
        "purple": ("#EBDBFF", "#6D28D9"),
        "teal":   ("#D2F2F0", "#0F766E"),
    }

    # ── 卡片标签色板（深色）──────────────────────────────────
    # 此前深色**复用浅色那份 pastel 底**，只把文字统一压成 #10131A：
    # 对比度够（14~16:1），但六种色的选中边框全变成同一个近黑色，选中态
    # 只剩"淡彩填充"一个载体；而且六枚高亮色块成了深色界面上最亮的物体，
    # 标签比卡片标题还抢眼，层级是反的。
    # 改为各自从饱和色派生：软底 = 该色 18% 叠在 bg_card 上，前景提亮到达标。
    DARK_LABEL_COLORS = {
        "blue":   ("#243051", "#7095FF"),
        "green":  ("#233934", "#2FB560"),
        "orange": ("#3E302B", "#F37B25"),
        "red":    ("#40272F", "#FF6161"),
        "purple": ("#332A51", "#B17FFF"),
        "teal":   ("#22383E", "#29B0A5"),
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
    REPEAT_NAMES = {"never": "", "daily": "每日", "weekly": "每周",
                    "weekdays": "工作日", "monthly": "每月", "yearly": "每年",
                    "custom": "自定义"}
    # 对话框/菜单展示顺序（never 首位）
    REPEAT_ORDER = ("never", "daily", "weekly", "weekdays",
                    "monthly", "yearly", "custom")

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
    # 已停用：列头原本按 `crc32(list_id) % len(LIST_ACCENTS)` 取一个色点，
    # 跨进程稳定但**没有任何含义**——用户会把它读成状态，而删掉一列再新建
    # 同名列就会换色。一个稳定的假信号比没有信号更糟。列表相关的视觉区分
    # 改由标题与计数徽章承担。保留常量仅为兼容旧数据/外部引用。
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

    # ══════════════════════════════════════════════════════════
    # 视觉皮肤表
    # ══════════════════════════════════════════════════════════
    #
    # 结构：{key: {"name": 中文名, "light": 色表, "dark": 色表, "shape": 造型}}
    #
    # - 色表**键集合必须与 COLORS 完全一致**：`AppTheme.global_qss` /
    #   `_board_qss` 按固定键名插值，缺一个键就是运行期 KeyError 或空串
    #   （tests/test_ui_skins.py 有护栏）。
    # - 对比度（WCAG AA 4.5:1，11px 徽章是主要受害者）在起草时逐色算过；
    #   tests/test_contrast.py 会把本表全部组合再遍历一遍，不达标即红。
    # - 造型键见 `_skin_shape()`：圆角阶梯 / 字重 / 边框宽度 / 徽章形态 /
    #   骨架自绘方式 / 列表头字体。缺省值集中在 tokens._SHAPE_DEFAULTS。
    UI_SKINS = {
        # ── 默认：瑞士排版 · Editorial ────────────────────────
        # 反差来自**排版**：纸色底 + 墨黑字 + 单一电光蓝，圆角收到 4–6px，
        # 徽章去底条改"细竖线 + 文字"。
        "editorial": {
            "name": "瑞士排版",
            "light": {
                "bg_primary": "#F7F5F0",
                "bg_panel": "#FFFFFF",
                "bg_card": "#FFFFFF",
                "bg_hover": "#EFECE5",
                "text_primary": "#1A1A1A",
                "text_secondary": "#5A5A5A",
                "text_muted": "#5A5A5A",
                "text_disabled": "#A8A49B",
                "accent": "#2547EB",
                "accent_hover": "#1B3AD0",
                "accent_on": "#FFFFFF",
                "accent_text": "#2547EB",
                "accent_soft": "rgba(37, 71, 235, 0.10)",
                "success": "#1F6B45",
                "success_soft": "#E2EFE9",
                "success_text": "#0F6B45",
                "success_on": "#FFFFFF",
                "danger": "#B3261E",
                "danger_soft": "#F8E7E6",
                "danger_text": "#98321F",
                "danger_on": "#FFFFFF",
                "warning": "#8A5A00",
                "warning_soft": "#F1EBE0",
                "warning_text": "#7A5410",
                "warning_on": "#FFFFFF",
                "neutral_soft": "#F0EDE6",
                "neutral_text": "#5A5A5A",
                "focus_ring": "#2547EB",
                "border": "#E4E0D8",
                "board_bg_start": "#F7F5F0",
                "board_bg_mid": "#F7F5F0",
                "board_bg_end": "#F5F2EC",
                "glass": "rgba(255, 255, 255, 0.72)",
                "glass_hover": "rgba(255, 255, 255, 0.94)",
                "popover_bg": "#FFFFFF",
                "popover_border": "#D8D3C8",
                "popover_shadow": "#0F1621",
                "mask": "rgba(90, 80, 60, 0.09)",
                "mask_hover": "rgba(90, 80, 60, 0.18)",
                "scroll_handle": "rgba(120, 112, 96, 0.38)",
                "scroll_handle_hover": "rgba(120, 112, 96, 0.60)",
                "hint_on_board": "#5A5A5A",
            },
            "dark": {
                "bg_primary": "#15171A",
                "bg_panel": "#1D2024",
                "bg_card": "#23272C",
                "bg_hover": "#2B3036",
                "text_primary": "#EDEDEA",
                "text_secondary": "#B4B8BC",
                "text_muted": "#B4B8BC",
                "text_disabled": "#6A6F75",
                "accent": "#9DB8FF",
                "accent_hover": "#B9CCFF",
                "accent_on": "#0F1216",
                "accent_text": "#9DB8FF",
                "accent_soft": "rgba(157, 184, 255, 0.18)",
                "success": "#5FD59B",
                "success_soft": "#1D3529",
                "success_text": "#5FD59B",
                "success_on": "#0F1216",
                "danger": "#FF8A7F",
                "danger_soft": "#3B2321",
                "danger_text": "#FFA096",
                "danger_on": "#0F1216",
                "warning": "#F0C070",
                "warning_soft": "#3A3123",
                "warning_text": "#F5CF8C",
                "warning_on": "#0F1216",
                "neutral_soft": "#2A2E34",
                "neutral_text": "#B4B8BC",
                "focus_ring": "#9DB8FF",
                "border": "#2F343A",
                "board_bg_start": "#15171A",
                "board_bg_mid": "#171A1E",
                "board_bg_end": "#1A1D22",
                "glass": "rgba(255, 255, 255, 0.06)",
                "glass_hover": "rgba(255, 255, 255, 0.13)",
                "popover_bg": "#2A2E34",
                "popover_border": "#3B4148",
                "popover_shadow": "#000000",
                "mask": "rgba(255, 255, 255, 0.09)",
                "mask_hover": "rgba(255, 255, 255, 0.18)",
                "scroll_handle": "rgba(200, 200, 190, 0.30)",
                "scroll_handle_hover": "rgba(200, 200, 190, 0.50)",
                "hint_on_board": "#B4B8BC",
            },
            "shape": {
                "radii": {"shell": 4, "panel": 5, "card": 6, "control": 4, "pill": 4, "chip": 3},
                "border_width": 1,
                "card_weight": 600,
                "list_weight": 800,
                "title_weight": 800,
                "title_letter_spacing": 0,
                "badge_style": "bar",        # 无底：竖线 + 文字
                "board_chrome": "plain",     # plain = 纯色底
                "toolbar_button": "outline", # 透明 + 描边
                "add_button_dashed": True,
            },
        },

        # ── 暖纸手帐 · Warm Paper ────────────────────────────
        # 暖色纸底 + 陶土强调 + 大圆角；列头可选宋体（shape.font_list）。
        "warm": {
            "name": "暖纸手帐",
            "light": {
                "bg_primary": "#F2EDE3",
                "bg_panel": "#FBF8F2",
                "bg_card": "#FFFDF9",
                "bg_hover": "#EDE4D5",
                "text_primary": "#2B2421",
                "text_secondary": "#6B5F58",
                "text_muted": "#6B5F58",
                "text_disabled": "#B2A596",
                "accent": "#9A3412",
                "accent_hover": "#7E2A0E",
                "accent_on": "#FFFAF5",
                "accent_text": "#9A3412",
                "accent_soft": "#F5E9E3",
                "success": "#3F6B3A",
                "success_soft": "#E3EEE0",
                "success_text": "#3F6B3A",
                "success_on": "#FFFAF5",
                "danger": "#A3352C",
                "danger_soft": "#F8E3DF",
                "danger_text": "#98321F",
                "danger_on": "#FFFAF5",
                "warning": "#7D5A12",
                "warning_soft": "#F6EDD9",
                "warning_text": "#7D5A12",
                "warning_on": "#FFFAF5",
                "neutral_soft": "#EFE8DC",
                "neutral_text": "#6B5F58",
                "focus_ring": "#9A3412",
                "border": "#E6DED0",
                "board_bg_start": "#F2EDE3",
                "board_bg_mid": "#F1EBE0",
                "board_bg_end": "#EFE8DC",
                "glass": "rgba(251, 248, 242, 0.86)",
                "glass_hover": "#FBF8F2",
                "popover_bg": "#FFFDF9",
                "popover_border": "#D9CDBA",
                "popover_shadow": "#2B1E14",
                "mask": "rgba(107, 85, 60, 0.09)",
                "mask_hover": "rgba(107, 85, 60, 0.17)",
                "scroll_handle": "rgba(140, 112, 80, 0.38)",
                "scroll_handle_hover": "rgba(140, 112, 80, 0.60)",
                "hint_on_board": "#6B5F58",
            },
            "dark": {
                # 暖黑而不是冷黑：底与卡都带一点红棕，深色下也是"纸"不是"屏幕"
                "bg_primary": "#1A1614",
                "bg_panel": "#241E1A",
                "bg_card": "#2C2521",
                "bg_hover": "#382F29",
                "text_primary": "#F2E9DE",
                "text_secondary": "#C0B0A2",
                "text_muted": "#C0B0A2",
                "text_disabled": "#7A6C60",
                "accent": "#E8A87C",
                "accent_hover": "#F2BC94",
                "accent_on": "#1A1614",
                "accent_text": "#E8A87C",
                "accent_soft": "rgba(232, 168, 124, 0.18)",
                "success": "#A9CF9E",
                "success_soft": "#2A3527",
                "success_text": "#A9CF9E",
                "success_on": "#1A1614",
                "danger": "#F09A8E",
                "danger_soft": "#3E2621",
                "danger_text": "#F5ABA0",
                "danger_on": "#1A1614",
                "warning": "#E9C079",
                "warning_soft": "#3A2F1F",
                "warning_text": "#EFCB8C",
                "warning_on": "#1A1614",
                "neutral_soft": "#332B26",
                "neutral_text": "#C0B0A2",
                "focus_ring": "#E8A87C",
                "border": "#3B322C",
                "board_bg_start": "#1A1614",
                "board_bg_mid": "#1C1815",
                "board_bg_end": "#1F1A17",
                "glass": "rgba(255, 240, 225, 0.07)",
                "glass_hover": "rgba(255, 240, 225, 0.14)",
                "popover_bg": "#332B26",
                "popover_border": "#4A3E36",
                "popover_shadow": "#000000",
                "mask": "rgba(255, 235, 215, 0.09)",
                "mask_hover": "rgba(255, 235, 215, 0.18)",
                "scroll_handle": "rgba(220, 190, 160, 0.32)",
                "scroll_handle_hover": "rgba(220, 190, 160, 0.52)",
                "hint_on_board": "#C0B0A2",
            },
            "shape": {
                "radii": {"shell": 16, "panel": 14, "card": 12, "control": 10, "pill": 10, "chip": 6},
                "border_width": 1,
                "card_weight": 500,
                "list_weight": 700,
                "title_weight": 800,
                "title_letter_spacing": 0,
                # 中文衬线：列头用宋体，和"纸/手帐"同一语言。
                # 衬线只上列头，正文仍是无衬线——长时间阅读正文用宋体会累。
                "font_list_serif": True,
                "badge_style": "pill",
                "board_chrome": "plain",
                "toolbar_button": "glass",
                "add_button_dashed": True,
            },
        },

        # ── 等宽终端 · Terminal ──────────────────────────────
        # 只做深色（终端肤色的意义就在暗底荧光），所以 light 也带暗底。
        # 造型上零圆角 + 1px 线框 + 徽章写成行内文本。
        "terminal": {
            "name": "等宽终端",
            "light": {
                "bg_primary": "#0D110D",
                "bg_panel": "#111711",
                "bg_card": "#0F150F",
                "bg_hover": "#162016",
                "text_primary": "#D7F5DC",
                "text_secondary": "#9FC9A8",
                "text_muted": "#9FC9A8",
                "text_disabled": "#5E7A6A",
                "accent": "#3DFF9B",
                "accent_hover": "#6CFFB6",
                "accent_on": "#0D110D",
                "accent_text": "#3DFF9B",
                "accent_soft": "rgba(61, 255, 155, 0.12)",
                "success": "#3DFF9B",
                "success_soft": "#102A1A",
                "success_text": "#3DFF9B",
                "success_on": "#0D110D",
                "danger": "#FF9C95",
                "danger_soft": "#2A1414",
                "danger_text": "#FF9C95",
                "danger_on": "#0D110D",
                "warning": "#FFDD9E",
                "warning_soft": "#2A2110",
                "warning_text": "#FFDD9E",
                "warning_on": "#0D110D",
                "neutral_soft": "#162016",
                "neutral_text": "#A8D2B0",
                "focus_ring": "#3DFF9B",
                "border": "#203020",
                "board_bg_start": "#0D110D",
                "board_bg_mid": "#0D110D",
                "board_bg_end": "#0E120E",
                "glass": "rgba(61, 255, 155, 0.06)",
                "glass_hover": "rgba(61, 255, 155, 0.12)",
                "popover_bg": "#111711",
                "popover_border": "#2A3F2A",
                "popover_shadow": "#000000",
                "mask": "rgba(61, 255, 155, 0.10)",
                "mask_hover": "rgba(61, 255, 155, 0.20)",
                "scroll_handle": "rgba(61, 255, 155, 0.28)",
                "scroll_handle_hover": "rgba(61, 255, 155, 0.48)",
                "hint_on_board": "#9FC9A8",
            },
            "dark": {
                "bg_primary": "#0B0E0B",
                "bg_panel": "#101510",
                "bg_card": "#0E140E",
                "bg_hover": "#152015",
                "text_primary": "#D7F5DC",
                "text_secondary": "#A8D2B0",
                "text_muted": "#A8D2B0",
                "text_disabled": "#5E7A6A",
                "accent": "#3DFF9B",
                "accent_hover": "#6CFFB6",
                "accent_on": "#0B0E0B",
                "accent_text": "#3DFF9B",
                "accent_soft": "rgba(61, 255, 155, 0.12)",
                "success": "#3DFF9B",
                "success_soft": "#102A1A",
                "success_text": "#3DFF9B",
                "success_on": "#0B0E0B",
                "danger": "#FF9C95",
                "danger_soft": "#2A1414",
                "danger_text": "#FF9C95",
                "danger_on": "#0B0E0B",
                "warning": "#FFDD9E",
                "warning_soft": "#2A2110",
                "warning_text": "#FFDD9E",
                "warning_on": "#0B0E0B",
                "neutral_soft": "#152015",
                "neutral_text": "#A8D2B0",
                "focus_ring": "#3DFF9B",
                "border": "#1E2E1E",
                "board_bg_start": "#0B0E0B",
                "board_bg_mid": "#0C100C",
                "board_bg_end": "#0D110D",
                "glass": "rgba(61, 255, 155, 0.05)",
                "glass_hover": "rgba(61, 255, 155, 0.11)",
                "popover_bg": "#101510",
                "popover_border": "#2A3F2A",
                "popover_shadow": "#000000",
                "mask": "rgba(61, 255, 155, 0.10)",
                "mask_hover": "rgba(61, 255, 155, 0.20)",
                "scroll_handle": "rgba(61, 255, 155, 0.28)",
                "scroll_handle_hover": "rgba(61, 255, 155, 0.48)",
                "hint_on_board": "#A8D2B0",
            },
            "shape": {
                "radii": {"shell": 0, "panel": 0, "card": 0, "control": 0, "pill": 0, "chip": 0},
                "border_width": 1,
                "card_weight": 500,
                "list_weight": 700,
                "title_weight": 700,
                "title_letter_spacing": 0.4,
                "font_mono": True,           # 等宽字体（中文回退无衬线）
                "badge_style": "text",       # 前缀符号 + 文字，无底
                "board_chrome": "grid",      # 板底画 64px 网格
                "toolbar_button": "outline",
                "add_button_dashed": False,  # `> + 添加卡片 _`
                "check_style": "square",
            },
        },

        # ── 新粗野主义 · Neubrutalism ────────────────────────
        # 2.5px 黑描边 + 右下 5px 硬投影 + 高饱和列头色块。
        # Qt 没有 box-shadow：硬投影走 CardWidget/ListColumn 的 paintEvent
        # 自绘（不用 QGraphicsDropShadowEffect，见 motion.py 的性能实测）。
        "brutal": {
            "name": "新粗野主义",
            "light": {
                "bg_primary": "#FFFBEB",
                "bg_panel": "#FFFFFF",
                "bg_card": "#FFFFFF",
                "bg_hover": "#FFF3C4",
                "text_primary": "#111111",
                "text_secondary": "#3D3D3D",
                "text_muted": "#4A4A4A",
                "text_disabled": "#8A8A8A",
                "accent": "#FF4D6D",
                "accent_hover": "#FF6B85",
                "accent_on": "#111111",
                "accent_text": "#B3123A",
                "accent_soft": "#FFD9E1",
                "success": "#3E9B4F",
                "success_soft": "#C8F7C5",
                "success_text": "#1F6B2E",
                "success_on": "#111111",
                "danger": "#E2384F",
                "danger_soft": "#FFD9E1",
                "danger_text": "#A3142B",
                "danger_on": "#111111",
                "warning": "#B58A00",
                "warning_soft": "#FFE480",
                "warning_text": "#6B4E00",
                "warning_on": "#111111",
                "neutral_soft": "#EFEFEF",
                "neutral_text": "#2B2B2B",
                "focus_ring": "#2547EB",
                "border": "#111111",
                "board_bg_start": "#FFFBEB",
                "board_bg_mid": "#FFFBEB",
                "board_bg_end": "#FFF8E1",
                "glass": "rgba(255, 255, 255, 0.78)",
                "glass_hover": "rgba(255, 255, 255, 0.95)",
                "popover_bg": "#FFFFFF",
                "popover_border": "#111111",
                "popover_shadow": "#111111",
                "mask": "rgba(17, 17, 17, 0.08)",
                "mask_hover": "rgba(17, 17, 17, 0.16)",
                "scroll_handle": "rgba(17, 17, 17, 0.35)",
                "scroll_handle_hover": "rgba(17, 17, 17, 0.55)",
                "hint_on_board": "#4A4A4A",
            },
            "dark": {
                # 暗底版：黑描边换成浅描边，硬投影换成亮投影
                "bg_primary": "#141414",
                "bg_panel": "#1D1D1D",
                "bg_card": "#232323",
                "bg_hover": "#2E2E2E",
                "text_primary": "#F5F0E1",
                "text_secondary": "#C9C4B6",
                "text_muted": "#C9C4B6",
                "text_disabled": "#7A7568",
                "accent": "#FF6B85",
                "accent_hover": "#FF8B9F",
                "accent_on": "#141414",
                "accent_text": "#FF8B9F",
                "accent_soft": "#4A2028",
                "success": "#6FDB7E",
                "success_soft": "#1E3A22",
                "success_text": "#8FE89B",
                "success_on": "#141414",
                "danger": "#FF7A8A",
                "danger_soft": "#45222A",
                "danger_text": "#FF9AA5",
                "danger_on": "#141414",
                "warning": "#FFD43B",
                "warning_soft": "#3B3212",
                "warning_text": "#FFE480",
                "warning_on": "#141414",
                "neutral_soft": "#2C2C2C",
                "neutral_text": "#E5E0D2",
                "focus_ring": "#8FB4FF",
                "border": "#EDE8DA",
                "board_bg_start": "#141414",
                "board_bg_mid": "#161616",
                "board_bg_end": "#191919",
                "glass": "rgba(255, 255, 255, 0.07)",
                "glass_hover": "rgba(255, 255, 255, 0.14)",
                "popover_bg": "#232323",
                "popover_border": "#EDE8DA",
                "popover_shadow": "#000000",
                "mask": "rgba(255, 255, 255, 0.10)",
                "mask_hover": "rgba(255, 255, 255, 0.20)",
                "scroll_handle": "rgba(240, 235, 220, 0.30)",
                "scroll_handle_hover": "rgba(240, 235, 220, 0.50)",
                "hint_on_board": "#C9C4B6",
            },
            "shape": {
                "radii": {"shell": 0, "panel": 0, "card": 0, "control": 2, "pill": 2, "chip": 3},
                "border_width": 2.5,
                "card_weight": 600,
                "list_weight": 800,
                "title_weight": 800,
                "title_letter_spacing": 0,
                "badge_style": "outline",    # 1.6px 描边药丸
                "board_chrome": "dots",      # 板底撒点（纸感）
                "toolbar_button": "outline",
                "hard_shadow": 5,            # 右下硬投影偏移（px），0 = 无
                "check_style": "square",
            },
        },

        # ── 玻璃拟态 · Frosted ───────────────────────────────
        # 面板半透明 + 1px 亮边 + 板底冷色渐变。**卡片保持不透明**：
        # notes_popover 的结论是浮层必须不透明否则文字叠影；桌面看板背后
        # 随时是别的窗口，卡片透过去会直接读不清。只有大面板透。
        "frosted": {
            "name": "玻璃拟态",
            "light": {
                "bg_primary": "#E9EEF9",
                "bg_panel": "rgba(255, 255, 255, 0.72)",
                "bg_card": "#FFFFFF",
                "bg_hover": "#F1F4FC",
                "text_primary": "#20232B",
                "text_secondary": "#5B6070",
                "text_muted": "#5B6070",
                "text_disabled": "#A6ABBA",
                "accent": "#5E6AD2",
                "accent_hover": "#4C58C4",
                "accent_on": "#FFFFFF",
                "accent_text": "#4450B8",
                "accent_soft": "rgba(94, 106, 210, 0.13)",
                "success": "#1F9D6B",
                "success_soft": "rgba(31, 157, 107, 0.14)",
                "success_text": "#146C48",
                "success_on": "#FFFFFF",
                "danger": "#D6454B",
                "danger_soft": "rgba(214, 69, 75, 0.12)",
                "danger_text": "#B2343A",
                "danger_on": "#FFFFFF",
                "warning": "#A8710E",
                "warning_soft": "rgba(214, 146, 20, 0.16)",
                "warning_text": "#8A5A08",
                "warning_on": "#FFFFFF",
                "neutral_soft": "rgba(35, 40, 60, 0.07)",
                "neutral_text": "#5B6070",
                "focus_ring": "#5E6AD2",
                "border": "rgba(255, 255, 255, 0.85)",
                "board_bg_start": "#EEF3FF",
                "board_bg_mid": "#F1EFFB",
                "board_bg_end": "#F7EFF8",
                "glass": "rgba(255, 255, 255, 0.55)",
                "glass_hover": "rgba(255, 255, 255, 0.82)",
                "popover_bg": "#FFFFFF",
                "popover_border": "rgba(160, 170, 200, 0.45)",
                "popover_shadow": "#2A3150",
                "mask": "rgba(35, 40, 60, 0.07)",
                "mask_hover": "rgba(35, 40, 60, 0.14)",
                "scroll_handle": "rgba(94, 106, 210, 0.28)",
                "scroll_handle_hover": "rgba(94, 106, 210, 0.48)",
                "hint_on_board": "#5B6070",
            },
            "dark": {
                "bg_primary": "#141824",
                "bg_panel": "rgba(40, 46, 66, 0.72)",
                "bg_card": "#262C3D",
                "bg_hover": "#303749",
                "text_primary": "#EDF0FA",
                "text_secondary": "#B3BBD2",
                "text_muted": "#B3BBD2",
                "text_disabled": "#6C7489",
                "accent": "#93A0FF",
                "accent_hover": "#AFB9FF",
                "accent_on": "#141824",
                "accent_text": "#A9B4FF",
                "accent_soft": "rgba(147, 160, 255, 0.20)",
                "success": "#5FD9A5",
                "success_soft": "rgba(95, 217, 165, 0.18)",
                "success_text": "#7FE3BC",
                "success_on": "#141824",
                "danger": "#FF8589",
                "danger_soft": "rgba(255, 133, 137, 0.16)",
                "danger_text": "#FF9DA0",
                "danger_on": "#141824",
                "warning": "#F0BC66",
                "warning_soft": "rgba(240, 188, 102, 0.16)",
                "warning_text": "#F5CB84",
                "warning_on": "#141824",
                "neutral_soft": "rgba(255, 255, 255, 0.08)",
                "neutral_text": "#B3BBD2",
                "focus_ring": "#93A0FF",
                "border": "rgba(255, 255, 255, 0.14)",
                "board_bg_start": "#11141E",
                "board_bg_mid": "#151926",
                "board_bg_end": "#1A1526",
                "glass": "rgba(255, 255, 255, 0.08)",
                "glass_hover": "rgba(255, 255, 255, 0.15)",
                "popover_bg": "#2A3042",
                "popover_border": "rgba(255, 255, 255, 0.20)",
                "popover_shadow": "#000000",
                "mask": "rgba(255, 255, 255, 0.09)",
                "mask_hover": "rgba(255, 255, 255, 0.18)",
                "scroll_handle": "rgba(200, 208, 240, 0.30)",
                "scroll_handle_hover": "rgba(200, 208, 240, 0.50)",
                "hint_on_board": "#B3BBD2",
            },
            "shape": {
                "radii": {"shell": 18, "panel": 16, "card": 14, "control": 10, "pill": 10, "chip": 8},
                "border_width": 1,
                "card_weight": 500,
                "list_weight": 700,
                "title_weight": 700,
                "title_letter_spacing": 0,
                "badge_style": "pill",
                "board_chrome": "glow",      # 板底三团柔光
                "toolbar_button": "glass",
                "add_button_dashed": True,
            },
        },
    }

    # ── 皮肤色表里的标签色（卡片左缘色条 / 对话框色块）──────────
    # 形状与 LABEL_COLORS 相同（key → (底, 前景)）。默认皮肤沿用主表；
    # 其余皮肤只调整"金色"到各自色系，保证色条不与底色打架。
    SKIN_LABEL_COLORS = {
        "editorial": {
            "light": {"blue": ("#E9EDFD", "#2547EB"), "green": ("#E2EFE9", "#0F6B45"),
                      "orange": ("#F1EBE0", "#7A5410"), "red": ("#F8E7E6", "#98321F"),
                      "purple": ("#ECE7F6", "#5B2BB5"), "teal": ("#E0F0EE", "#0C6B63")},
            "dark": {"blue": ("#242A40", "#9DB8FF"), "green": ("#1E332A", "#5FD59B"),
                     "orange": ("#33291C", "#F0C070"), "red": ("#3B2321", "#FFA096"),
                     "purple": ("#2E2742", "#C4A9FF"), "teal": ("#1C3234", "#5ED9CC")},
        },
        "warm": {
            "light": {"blue": ("#E3E7F2", "#2C4A8C"), "green": ("#E3EEE0", "#3F6B3A"),
                      "orange": ("#F6E3D5", "#9A3412"), "red": ("#F8E3DF", "#98321F"),
                      "purple": ("#EFE3EA", "#7A3557"), "teal": ("#DEEAE5", "#256B5E")},
            "dark": {"blue": ("#232C3E", "#A8BEE8"), "green": ("#2A3527", "#A9CF9E"),
                     "orange": ("#3A2A1E", "#E8A87C"), "red": ("#3E2621", "#F5ABA0"),
                     "purple": ("#33262C", "#DEA8C0"), "teal": ("#20302D", "#8FCDBF")},
        },
        "terminal": {
            "light": {"blue": ("#10202A", "#7FD8FF"), "green": ("#102A1A", "#3DFF9B"),
                      "orange": ("#2A2110", "#FFDD9E"), "red": ("#2A1414", "#FF9C95"),
                      "purple": ("#211A2A", "#D7A8FF"), "teal": ("#0F2422", "#6FE8D6")},
            "dark": {"blue": ("#0F1D26", "#7FD8FF"), "green": ("#102A1A", "#3DFF9B"),
                     "orange": ("#2A2110", "#FFDD9E"), "red": ("#2A1414", "#FF9C95"),
                     "purple": ("#1E1826", "#D7A8FF"), "teal": ("#0E211F", "#6FE8D6")},
        },
        "brutal": {
            "light": {"blue": ("#D6E4FF", "#14338F"), "green": ("#C8F7C5", "#1F6B2E"),
                      "orange": ("#FFE480", "#6B4E00"), "red": ("#FFD9E1", "#A3142B"),
                      "purple": ("#E4D6FF", "#4B2196"), "teal": ("#C9F2EE", "#0A5E58")},
            "dark": {"blue": ("#1E2A46", "#9FBBFF"), "green": ("#1E3A22", "#8FE89B"),
                     "orange": ("#3B3212", "#FFE480"), "red": ("#45222A", "#FF9AA5"),
                     "purple": ("#2E2046", "#C9A8FF"), "teal": ("#173634", "#7FE3DA")},
        },
        "frosted": {
            "light": {"blue": ("#E4E9FF", "#3B49B8"), "green": ("#E2F5ED", "#146C48"),
                      "orange": ("#FBF0DC", "#8A5A08"), "red": ("#FBE6E7", "#B2343A"),
                      "purple": ("#EFE8FC", "#5C34B0"), "teal": ("#DFF3F2", "#0B6660")},
            "dark": {"blue": ("#232B47", "#A9B4FF"), "green": ("#1E3A32", "#7FE3BC"),
                     "orange": ("#3A3020", "#F5CB84"), "red": ("#3E2326", "#FF9DA0"),
                     "purple": ("#2C2444", "#C7AEFF"), "teal": ("#1C3336", "#7FE3DA")},
        },
    }

    # ── 平台检测 ──────────────────────────────────────────────
    IS_WINDOWS = sys.platform == "win32"
    IS_MACOS = sys.platform == "darwin"


def workdir_start_dir(current: str = "") -> str:
    """选择工作目录时的起始目录：卡片当前值 → 上次选过的目录 → 用户主目录

    目录常在外接盘上，不在是常态：每个候选都要 is_dir 验活，否则记忆里一个
    已拔出的盘会让选择器每次都停在打不开的位置，用户得从"此电脑"重新找起。
    """
    for cand in (current, AppConfig.get_last_workdir()):
        if cand and Path(cand).is_dir():
            return cand
    return str(Path.home())

"""设计 token 取值层：字号 / 间距 / 圆角 / 内边距

为什么单独一层
--------------
`config.py` 存的是**基准值**，这里负责把它们变成"当前密度档位下实际该用的
值"。两者分开的原因：QSS 是字符串，生成时就得把数值焊进去，而密度是运行时
可切的偏好——需要一个统一的换算点，否则每处 QSS 都要自己乘系数。

密度档位（`AppConfig.get_ui_scale()`）
--------------------------------------
`compact` 0.92 / `normal` 1.00 / `comfortable` 1.12。阶段 1 先建好换算通道，
配置键与设置项在阶段 7 落地；在那之前 `get_ui_scale()` 缺省返回 `normal`，
因此**本模块当前是恒等变换**，不产生任何视觉变化。

两条硬约束
----------
- **字号有下限**：`font()` 的结果不得低于 `UI_FONT_CAPTION`。紧凑档下
  0.92 × 11 ≈ 10.1，四舍五入回 10px——那正是本计划要消灭的档位。
- **圆形不参与缩放**：圆形按钮的半径是 `size / 2`，跟着尺寸走。写死 17px
  再乘系数，放大后就不再是圆。用 `circle_radius(size)`。
"""

from __future__ import annotations

import re

from app.config import AppConfig

# 密度档位 → 缩放系数。改这里等于改全局疏密。
_SCALE_FACTORS = {
    "compact": 0.92,
    "normal": 1.00,
    "comfortable": 1.12,
}

_DEFAULT_SCALE = "normal"


def scale() -> str:
    """当前密度档位（非法值回退 normal）

    `get_ui_scale` 在阶段 7 之前不存在，故用 getattr 兜底——本模块是 QSS
    生成路径上的依赖，不能因为偏好键还没落地就抛异常。
    """
    getter = getattr(AppConfig, "get_ui_scale", None)
    value = getter() if callable(getter) else _DEFAULT_SCALE
    return value if value in _SCALE_FACTORS else _DEFAULT_SCALE


def factor() -> float:
    """当前密度系数"""
    return _SCALE_FACTORS[scale()]


def _scaled(px: int, minimum: int) -> int:
    """按密度缩放并夹住下限"""
    return max(minimum, round(px * factor()))


# ── 字号 ──────────────────────────────────────────────────────

def font(px: int) -> int:
    """字号（px）→ 当前密度下的字号

    下限锁在 `UI_FONT_CAPTION`：紧凑档也不能把 11px 压回 10px。
    """
    return _scaled(px, AppConfig.UI_FONT_CAPTION)


def font_css(px: int) -> str:
    """字号 → QSS 片段，形如 `font-size: 13px`"""
    return f"font-size: {font(px)}px"


# ── 间距 ──────────────────────────────────────────────────────

def space(px: int) -> int:
    """间距（px）→ 当前密度下的间距

    下限 1px 而不是 2px：下限的用途是挡住退化的 0，不是禁止 1px 发丝级
    内边距——列头计数徽章原本就是 `padding: 1px 7px`，抬到 2px 会让徽章
    凭空高 2px。纯 0 请显式写 `padding: 0 {space(n)}px`。
    """
    return _scaled(px, 1)


def size(px: int) -> int:
    """控件尺寸（px）→ 当前密度下的尺寸

    与 `space()` 分开是因为下限不同：间距可以细到 1px，控件尺寸不能——
    一个 12px 的圆形按钮在任何档位下都点不中。下限 8px。
    """
    return _scaled(px, 8)


def control_size(px: int) -> tuple[int, int]:
    """正方形控件尺寸 → (w, h)，供 setFixedSize 用"""
    s = size(px)
    return s, s


def pad(vertical: int, horizontal: int) -> str:
    """内边距 → QSS 片段，形如 `padding: 6px 10px`"""
    return f"padding: {space(vertical)}px {space(horizontal)}px"


def pad_all(value: int) -> str:
    """四边等值内边距 → QSS 片段"""
    return f"padding: {space(value)}px"


_PRESET_RE = re.compile(r"^(\d+)px(?:\s+(\d+)px)?$")


def pad_preset(preset: str) -> str:
    """AppConfig 的 padding 预设（`"6px 10px"`）→ 当前密度下的 QSS 片段

    预设字符串是给"整份 padding"用的固定搭配，直接插进 QSS 就绕过了密度
    换算——阶段 7 上密度档位后，只有这些位置不跟着缩放，界面对不齐。
    统一从这里过一道。
    """
    m = _PRESET_RE.match(str(preset).strip())
    if not m:
        return f"padding: {preset}"
    v = int(m.group(1))
    if m.group(2) is None:
        return f"padding: {space(v)}px"
    return pad(v, int(m.group(2)))


# ── 圆角 ──────────────────────────────────────────────────────

_RADIUS_NAMES = {
    "shell": AppConfig.UI_RADIUS_SHELL,
    "panel": AppConfig.UI_RADIUS_PANEL,
    "card": AppConfig.UI_RADIUS_CARD,
    "control": AppConfig.UI_RADIUS_CONTROL,
    "chip": AppConfig.UI_RADIUS_CHIP,
    "pill": AppConfig.UI_RADIUS_PILL,
}

# 皮肤造型缺省值：皮肤表里的 shape 只写"和基线不同"的键，其余走这里。
# 单独抽出来是因为造型读取散布在 QSS 生成与自绘两处，缺省散落就会
# 出现"看板方角、设置页圆角"的半套皮肤。
_SKIN_SHAPE_DEFAULTS = {
    "border_width": 1,
    "card_weight": 500,
    "list_weight": 700,
    "title_weight": 700,
    "title_letter_spacing": 0,
    "badge_style": "pill",
    "board_chrome": "plain",
    "toolbar_button": "glass",
    "add_button_dashed": True,
    "hard_shadow": 0,
    "check_style": "rounded",
    "font_mono": False,
    "font_list_serif": False,
    "radii": {},
}


def _current_skin() -> str:
    """当前皮肤（以 AppTheme 的运行时皮肤为唯一真相）

    直接读 AppConfig.get_ui_skin() 会让"预览未保存皮肤"（set_skin(
    touch_pref=False)）的自绘控件与 QSS 用两套圆角。
    """
    try:
        from app.views.theme import AppTheme
        return AppTheme.skin()
    except Exception:   # noqa: BLE001 — 主题模块未就绪（早期导入/测试）
        getter = getattr(AppConfig, "get_ui_skin", None)
        return getter() if callable(getter) else AppConfig.UI_SKIN_DEFAULT


def shape(skin: str | None = None) -> dict:
    """皮肤造型参数（缺省补全）

    `skin=None` 取当前皮肤；未知 key 回落默认皮肤。传空串 `""` 时返回
    **全部缺省值**（皮肤引入之前的造型：圆角全走 AppConfig、徽章药丸、
    玻璃工具栏）——只有需要"基线渲染"的测试会这样调用。
    """
    key = _current_skin() if skin is None else skin
    spec = AppConfig.UI_SKINS.get(key) if key else None
    out = dict(_SKIN_SHAPE_DEFAULTS)
    if spec:
        out.update(spec.get("shape") or {})
    radii = dict(_RADIUS_NAMES)
    radii.update(out.get("radii") or {})
    out["radii"] = radii
    return out


def radius(name: str, skin: str | None = None) -> int:
    """圆角 token 名 → 当前密度与皮肤下的圆角（px）

    名称写错时回退 `control` 而不是抛异常：QSS 生成路径在构造期，
    一个拼写错误不该让整个界面起不来。

    `skin` 缺省时用当前皮肤（`AppConfig.get_ui_skin()`）：绝大多数 QSS
    生成点都在运行期，读偏好即可；只有需要"渲染非当前皮肤"的预览脚本
    才显式传参，测试要基线时传 `""`。
    """
    table = shape(skin)["radii"]
    return _scaled(table.get(name, table["control"]), 0)


def radius_css(name: str, skin: str | None = None) -> str:
    """圆角 token 名 → QSS 片段，形如 `border-radius: 8px`"""
    return f"border-radius: {radius(name, skin)}px"


def circle_radius(size: int) -> int:
    """圆形控件的半径：由尺寸现算，不参与密度缩放

    此前工具栏三个圆形按钮写死 `border-radius: 17px`（对应 34px 直径），
    换尺寸就不再是圆；密度档位改动尺寸时同样会破。
    """
    return max(1, size // 2)


# ── 对话框边距 ────────────────────────────────────────────────

def dialog_margins(bottom_trim: int = 0,
                   top: int | None = None) -> tuple[int, int, int, int]:
    """对话框内容边距 (左, 上, 右, 下)

    此前同一份"20/18/20/16"被写成四种变体（`UI_DIALOG_MARGIN_V`、
    `-2`、`-4`），底边距全靠手感。统一成一处。

    - `bottom_trim`：底边距比上边距收紧多少（按钮行自带间距时用）
    - `top`：上边距的显式覆盖。设置页与快捷键面板原本用 16 而非 18
      （`MARGIN_V - 2`），为了让它们也能走同一个入口而不是留两处裸字面量，
      这里给一个显式口子——**保持原值**比强行统一更重要。
    """
    m_h, m_v = AppConfig.UI_DIALOG_MARGIN_H, AppConfig.UI_DIALOG_MARGIN_V
    top_v = m_v if top is None else top
    return (space(m_h), space(top_v), space(m_h), space(m_v - bottom_trim))

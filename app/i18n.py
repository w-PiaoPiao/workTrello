"""界面多语言：中文（默认/回退原文）+ 英文

设计要点：
- **中文原文即 key**：tr("删除卡片") 在 zh 下原样返回，en 下查字典，
  字典缺失时回退中文原文——新增文案不会因漏翻译而报错，只会暂时
  显示中文，翻译可以渐进补齐。
- **切换广播**：与 AppTheme.register 同一模式。语言是低频切换，
  常驻单例（看板视图/桌宠菜单/托盘/浮窗/交通灯）注册回调即时刷新；
  一次性对话框（卡片编辑/快速添加）每次打开时构造，天然取最新语言。
  动态数据文本（卡片徽章/统计行）由控制器在切换后统一触发一次
  _after_data_change 全板刷新。
- **数据身份不变**：AppConfig.APP_NAME 参与数据目录定位，绝不随
  语言切换；界面显示名走 app_display_name()。
"""

from __future__ import annotations

from PySide6.QtCore import QLocale

from app.config import AppConfig

_zh_en: dict[str, str] = {}


def tr(text: str) -> str:
    """翻译 UI 文案（zh 返回原文；en 查字典，缺失回退原文）"""
    if _lang == "zh":
        return text
    return _zh_en.get(text, text)


# ── 名称字典的显示层（解析层仍用 AppConfig 中文表，语法与语言无关）──

_LABEL_EN = {"blue": "Blue", "green": "Green", "orange": "Orange",
             "red": "Red", "purple": "Purple", "teal": "Teal"}
_REPEAT_EN = {"daily": "Daily", "weekly": "Weekly"}
_PRIORITY_EN = {1: "High", 2: "Medium", 3: "Low"}
_SKIN_EN = {"milk": "Milk", "snow": "Snow", "choco": "Cocoa",
            "midnight": "Midnight"}


def label_display(key: str) -> str:
    """标签 key → 界面显示名（tooltip / 过滤 chip / 编辑对话框）"""
    if _lang == "en":
        return _LABEL_EN.get(key, key)
    return AppConfig.LABEL_NAMES.get(key, key)


def repeat_display(repeat: str) -> str:
    if _lang == "en":
        return _REPEAT_EN.get(repeat, "")
    return AppConfig.REPEAT_NAMES.get(repeat, "")


def priority_name(priority: int) -> str:
    if _lang == "en":
        return _PRIORITY_EN.get(priority, "")
    return AppConfig.PRIORITY_NAMES.get(priority, "")


def skin_display(key: str) -> str:
    """皮肤显示名（桌宠右键菜单 / 设置界面）"""
    if _lang == "en":
        return _SKIN_EN.get(key, AppConfig.SKIN_NAMES.get(key, key))
    return AppConfig.SKIN_NAMES.get(key, key)


def app_display_name() -> str:
    """应用显示名（数据身份 APP_NAME 不参与，仅 UI 展示）"""
    return tr("桌宠看板") if _lang == "en" else AppConfig.APP_NAME


# ── 语言状态与广播 ────────────────────────────────────────

_lang = "zh"
_listeners: list = []

# 完整英文词条表（key = 中文原文）。带 {n} 等占位符的整句由调用方
# .format() 填充；新增文案若漏翻，en 下回退显示中文原文。
_zh_en: dict[str, str] = {
    # ── 通用 ──
    "桌宠看板": "Pet Board",
    "、": ", ",
    "设置": "Settings",
    "完成": "Done",
    "取消": "Cancel",
    "保存": "Save",
    "添加": "Add",
    "关闭": "Close",
    "恢复": "Restore",
    "归档": "Archive",
    "重命名": "Rename",
    "删除列表": "Delete List",
    "导出": "Export",
    "退出": "Quit",
    "外观": "Appearance",
    "语言 / Language": "Language",
    "窗口": "Window",
    "数据": "Data",
    "关于": "About",
    "跟随系统": "System",
    "浅色": "Light",
    "深色": "Dark",
    "主题": "Theme",
    "亮色 / 深色 / 跟随系统外观": "Light / dark / follow system appearance",
    "界面语言": "Interface language",
    "切换后立即生效": "Applies immediately",
    "桌宠": "Pet",
    "待机动画": "Idle animations",
    "漂浮、呼吸、眨眼与全部过渡动效的总开关":
        "Master switch for floating, breathing, blinking and all transitions",
    "皮肤": "Skin",
    "折叠态小家伙的配色": "Colors of your folded companion",
    "窗口置顶": "Always on top",
    "桌宠与看板始终悬浮在其他窗口之上":
        "Keep the pet and board floating above other windows",
    "数据目录": "Data folder",
    "看板数据与自动备份保存在": "Board data and auto backups live in",
    "打开目录": "Open Folder",
    "桌宠形态的轻量任务看板：今日聚焦、番茄钟、归档与导出。":
        "A lightweight pet-shaped task board: today focus, pomodoro, "
        "archive and export.",
    # ── 看板 ──
    "我的看板": "My Board",
    "搜索卡片…": "Search cards…",
    "搜索卡片": "Search cards",
    "今日 {n}": "Today {n}",
    "只显示未完成的：星标 / 已逾期 / 今天截止":
        "Show unfinished only: starred / overdue / due today",
    "+ 添加列表": "+ Add list",
    "+ 添加卡片": "+ Add card",
    "导出为 Markdown / CSV": "Export as Markdown / CSV",
    "查看已归档卡片并恢复": "View archived cards and restore",
    "切换浅色 / 深色主题": "Toggle light / dark theme",
    "折叠为桌宠": "Collapse to pet",
    "设置": "Settings",
    "Markdown（.md）": "Markdown (.md)",
    "CSV（.csv）": "CSV (.csv)",
    "导出备份（.json）": "Export backup (.json)",
    "从备份导入…": "Import backup…",
    "匹配 {n} 张": "{n} matched",
    "标签 {name} · {n} 张": "{name} · {n} cards",
    "{total} 张卡片 · 完成 {done}": "{total} cards · {done} done",
    "看板还是空的\n点击右上角「+ 添加列表」创建第一列":
        "The board is empty\nClick \"+ Add list\" (top right) to create "
        "the first column",
    "还没有卡片，点击下方添加": "No cards yet — add one below",
    "没有匹配的卡片": "No matching cards",
    "过滤/搜索状态下卡片不可拖拽，清除过滤后可拖动":
        "Cards can't be dragged while a filter is active — clear it first",
    "点击清除标签过滤": "Click to clear the label filter",
    "标签：": "Labels: ",
    # ── 卡片 ──
    "点击切换完成状态": "Toggle done",
    "删除卡片": "Delete card",
    "⭐ 加入今日": "⭐ Add to Today",
    "☆ 移出今日": "☆ Remove from Today",
    "⏹ 停止专注": "⏹ Stop focus",
    "▶ 开始专注 25 分钟": "▶ Focus 25 min",
    "已逾期": "Overdue",
    "今天截止": "Due today",
    "明天截止": "Due tomorrow",
    "≡ 有备注": "≡ Notes",
    "折叠 / 展开列表": "Collapse / expand list",
    "列表操作": "List actions",
    "拖动调整看板大小": "Drag to resize the board",
    # ── 卡片对话框 ──
    "编辑卡片": "Edit Card",
    "新建卡片": "New Card",
    "标题": "Title",
    "备注": "Notes",
    "⏱ 插入当前时间": "⏱ Insert time",
    "在备注光标处插入当前时间（如 09-08 14:30）":
        "Insert the current time at the cursor (e.g. 09-08 14:30)",
    "补充说明、链接、清单…\n（记进度时点右上角「⏱ 插入当前时间」）":
        "Details, links, checklists…\nUse \"⏱ Insert time\" to log progress",
    "标签": "Labels",
    "截止日期": "Due date",
    "未设置": "Not set",
    "标记为已完成": "Mark as done",
    "加入今日聚焦": "Add to Today",
    "星标后卡片会出现在「今日聚焦」视图和桌宠角标中":
        "Starred cards appear in the Today focus view and the pet badge",
    "设置日期": "Set date",
    "清除日期": "Clear date",
    "重复": "Repeat",
    "不重复": "Never",
    "每天": "Daily",
    "每周": "Weekly",
    "优先级": "Priority",
    "无": "None",
    "高": "High",
    "中": "Medium",
    "低": "Low",
    # ── 快速/批量添加 ──
    "快速添加卡片": "Quick Add Card",
    "卡片标题（支持速记：明天 / 周五 / !P1 / #红）":
        "Card title (shorthand: tomorrow / fri / !P1 / #red)",
    "例：明天 交周报 !P1 #红": "e.g. tomorrow weekly report !P1 #red",
    "速记：今天/明天/N天后/周X/9月20日 · !P1~!P3 · #红#蓝":
        "Shorthand: today/tomorrow/in N days/fri/9/20 · !P1–!P3 · #red #blue",
    "批量添加卡片": "Bulk Add Cards",
    "每行一张卡片，行内支持速记：明天 / 周五 / 3天后 / 9月20日、!P1、#红":
        "One card per line; shorthand supported: tomorrow / fri / in 3 days "
        "/ 9/20, !P1, #red",
    "例：\n明天 交周报 !P1 #红\n周五 复盘会\n采购打印机":
        "e.g.\ntomorrow weekly report !P1 #red\nfri retro\nbuy a printer",
    "添加到列表：": "Add to list:",
    "{n} 行": "{n} lines",
    "批量添加": "Bulk Add",
    "添加列表": "Add List",
    "列表名称：": "List name:",
    "例如：进行中": "e.g. In Progress",
    # ── 桌宠菜单 ──
    "展开看板": "Open Board",
    "今日清单": "Today List",
    "暂停动画": "Pause animations",
    "换皮肤": "Skin",
    # ── 托盘 ──
    "隐藏": "Hide",
    "显示": "Show",
    "撤销": "Undo",
    "退出应用": "Quit",
    "最大化 / 还原": "Zoom / Restore",
    # ── 提示与反馈 ──
    "已添加卡片": "Card added",
    "已保存": "Saved",
    "已删除 · {hint}": "Deleted · {hint}",
    "已删除列表 · {hint}": "List deleted · {hint}",
    "已添加列表": "List added",
    "已批量添加 {n} 张卡片": "Added {n} cards",
    "已加入今日": "Added to Today",
    "已移出今日": "Removed from Today",
    "已归档": "Archived",
    "已恢复": "Restored",
    "已撤销上一步": "Undone",
    "没有可撤销的操作": "Nothing to undo",
    "⌘Z 撤销": "⌘Z to undo",
    "Ctrl+Z 撤销": "Ctrl+Z to undo",
    "看板还没有列表，先添加一个列表": "No lists yet — add one first",
    "已完成 · 下次 {date}": "Done · next {date}",
    "全部完成！桌宠为你鼓掌 🎉": "All done! The pet applauds you 🎉",
    "今日已完成 3 张，节奏不错！🌱": "3 done today — nice pace! 🌱",
    "今日已完成 5 张，收工级表现！🏆": "5 done today — outstanding! 🏆",
    "已完成 {n} 张卡片，继续加油！": "{n} cards done — keep it up!",
    "截止提醒：{items}": "Due reminders: {items}",
    "等 {n} 项": "and {n} more",
    "专注中 {time} · {title}": "Focusing {time} · {title}",
    "专注完成！累计 {n} 个番茄 🍅": "Focus done — {n} 🍅 in total",
    "专注完成！休息一下 🎉": "Focus done! Take a break 🎉",
    "已结束专注": "Focus stopped",
    "切换专注": "Switch Focus",
    "正在专注「{title}」，切换将放弃当前进度。\n继续？":
        "Focusing on \"{title}\" — switching will discard the current "
        "progress.\nContinue?",
    "确认删除": "Confirm Delete",
    "列表「{title}」还有 {n} 张卡片，删除后不可恢复。\n继续？":
        "List \"{title}\" still has {n} cards. This cannot be undone.\n"
        "Continue?",
    # ── 归档对话框 ──
    "共 {n} 张归档 · 本周完成 {m} 张":
        "{n} archived · {m} done this week",
    "暂无归档卡片：右键卡片即可归档":
        "Nothing archived yet — right-click a card to archive it",
    # ── 今日浮窗 ──
    "今日待办 · {n}": "Today · {n}",
    "今天没有待办 🎉": "Nothing due today 🎉",
    "今日已完成 {n} 张 🎉": "{n} done today 🎉",
    "移出今日": "Remove from Today",
    "加入今日": "Add to Today",
    "点击徽章固定": "Click badge to pin",
    # ── 导入导出 ──
    "导出看板": "Export Board",
    "导出备份": "Export Backup",
    "从备份导入": "Import Backup",
    "已导出到 {path}": "Exported to {path}",
    "备份已导出到 {path}": "Backup exported to {path}",
    "导出失败：{err}": "Export failed: {err}",
    "备份文件无法读取：{err}": "Backup file can't be read: {err}",
    "导入将替换当前看板（建议先导出备份）。\n继续？":
        "Importing replaces the current board (export a backup first).\n"
        "Continue?",
    "已导入备份": "Backup imported",
    "错误": "Error",
    # ── 菜单栏（macOS）──
    "文件": "File",
    "编辑": "Edit",
    "视图": "View",
    "新建卡片": "New Card",
    "新建列表": "New List",
    "导出 Markdown": "Export Markdown",
    "导出 CSV": "Export CSV",
    "打开归档": "Open Archive",
    "剪切": "Cut",
    "复制": "Copy",
    "粘贴": "Paste",
    "全选": "Select All",
    "今日聚焦": "Today Focus",
    "深色主题": "Dark Theme",
    "收起为桌宠": "Collapse to Pet",
    "关于桌宠看板": "About Pet Board",
    "设置…": "Settings…",
    # ── 数据恢复 ──
    "看板数据异常": "Board Data Error",
    "看板数据文件{detail}，{note}检测到最近一次成功保存的副本（{name}），"
    "是否用它恢复数据？\n选择「否」则以默认看板启动。":
        "The board data file {detail}. {note}A recent good copy was found "
        "({name}). Restore it?\nChoose \"No\" to start with the default "
        "board.",
    "已损坏并隔离": "was corrupted and quarantined",
    "暂时无法读取": "can't be read right now",
    "原文件已自动隔离备份。\n": "The original file has been quarantined.\n",
    "原文件仍保留在原位置。\n": "The original file is kept in place.\n",
    "看板数据为空": "Board Data Is Empty",
    "看板当前没有任何卡片，但检测到 {n} 份历史数据副本（最近一份：{name}）。\n"
    "是否恢复最近一份数据？\n选择「否」将保持空看板，且下次不再询问"
    "（直到再次录入过数据）。":
        "The board has no cards, but {n} historical backup(s) were found "
        "(latest: {name}).\nRestore the latest one?\nChoose \"No\" to keep "
        "the empty board; you won't be asked again until new data is saved.",
    "已从快照恢复看板数据": "Board restored from snapshot",
    "已从备份恢复看板数据": "Board restored from backup",
    "快照 {name} 无法解析，恢复失败。":
        "Snapshot {name} can't be parsed — restore failed.",
    "备份文件 {name} 无法解析，恢复失败，本次以默认看板启动。":
        "Backup {name} can't be parsed — restore failed; starting with the "
        "default board.",
    "本次以默认看板启动。": "Starting with the default board.",
    "看板数据文件异常（{detail}），本次以默认看板启动。":
        "Board data file error ({detail}). Starting with the default board.",
    "看板数据保存失败，请检查磁盘空间或文件权限":
        "Failed to save board data — check disk space or file permissions",
}


def lang() -> str:
    return _lang


def set_lang(lang: str) -> None:
    """设置语言并广播（模式未变时短路）"""
    global _lang
    lang = "en" if lang == "en" else "zh"
    if lang == _lang:
        return
    _lang = lang
    for cb in list(_listeners):
        try:
            cb()
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "语言切换回调执行失败: %r", cb)


def register(callback) -> None:
    _listeners.append(callback)


def detect_language() -> str:
    """系统语言探测（首次启动默认值）：中文系统 → zh，其余 → en"""
    return "zh" if QLocale.system().name().lower().startswith("zh") else "en"

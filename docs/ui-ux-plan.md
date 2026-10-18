# 桌宠看板 · 全局 UI/UX 与交互优化计划

> 状态：**8 个阶段全部完成**。每阶段一次提交，全量测试与离屏截图逐阶段验收。

## 执行结果（截至本轮）

| 阶段 | 状态 | 提交 | 说明 |
|---|---|---|---|
| 0 基线与护栏 | ✅ | `f385430` | 对比度/token 棘轮测试；修掉冒烟截图失真 |
| 1 token 体系 | ✅ | `f193e88` | 65 字号 + 30 圆角 + 24 内边距字面量清零 |
| 2 色彩与对比度 | ✅ | `f193e88` `009a014` | 逐 tone 软底、中性板底、全部达标 |
| 3 图标体系 | ✅ | `cd73398` `f2e1be9` | 新建 icons.py（18 图标）；对话框 + 看板两侧替换 |
| 4 工具栏 | ✅ | `cd73398` | 今日 chip 说假话、统计行消失、窄窗重叠（含 900px 就重叠的真因） |
| 5 焦点/键盘/命中区 | ✅ | `a834923` `3b9ec4e` | 焦点环、分段控件与日历键盘、命中区 ≥24、无障碍名 |
| 6 可发现性与空态 | ✅ | 本次提交 | 11 个手势进速查面板（分三小节 + 手势区）；列表色点（crc32 假语义）移除；空看板改可点 CTA 面板；归档/今日浮窗补空态 |
| 7 字体与密度 | ✅ | 本次提交 | 三档密度（0.92/1.00/1.12）覆盖字号/间距/圆角/控件尺寸；设置页「外观」新增分段控件；固定尺寸解耦 |
| 8 验证与发布 | ✅ | `3b9ec4e` 本次提交 | 版本号/README/RELEASE_NOTES；828 项测试 + 5 张截图 |

**验收基线**：全量 828 项测试 OK（skipped=24，耗时约 45s）；`smoke_test.py`
五张截图无 FAILURES。测试数从 774 增至 828（新增对比度、token、焦点可达性、
控件可达性、密度五道护栏）。

**过程中发现并修掉的、超出原计划的真实缺陷**（均由审计或新护栏抓出）：
1. `success` tone 的徽章**根本没有 QSS 规则**，退化成裸文字；
2. 工具栏收纳判定的宽度估算少算 87px（固定宽按钮的 hint 偏小 + 交通灯的
   `minimumSizeHint()` 是无效值 -1），**900px 下就已重叠**（原以为 590px）；
3. 「今日 N」在搜索时改报匹配数，标签与数值不符；
4. 全项目无 `QPushButton:focus`，键盘焦点完全不可见；
5. 删除卡片没有任何键盘路径；
6. 卡片勾选框不可 check，读屏永远不报勾选状态；
7. 桌宠（折叠态即应用本体）纯鼠标可达；
8. 日历首屏只显示约 3.6 行且必带滚动条；
9. `test_empty_board_restore_yes` 的**既有偶发红灯**（种子快照写死过去时间戳，
   系统日期一晚于 9 月 9 日就随机失败）；
10. token 辅助函数返回整条声明，调用点重复写声明名会生成
    `padding: padding: …` —— 测试全绿但 Qt 静默丢弃整份样式表；
11. 快捷键面板内容扩容到 26 行后**下半截（正好是新增的手势区）看不到**，
    必须补滚动区——否则这次"补文档"等于白做；
12. 密度切换在 300 卡（2242 控件）下 `apply()` 实测 **665ms**，瓶颈是
    `QApplication.setStyleSheet()` 重 polish 整棵树（基准字体 0.1ms、
    调色板 0ms 都不是）。已改为等待光标 + 延迟到下一轮事件循环执行，
    并把实测数字写进代码注释——不假装它瞬时。

## 0. 目标与成功标准

**目标**：在不动技术栈、不动数据模型、不破坏现有约 800 个测试的前提下，把「功能极其完整但视觉与交互层是逐步堆出来的」现状，收敛成一套**有规则、可验证、可调密度**的界面系统。

**成功标准**（可量化，全部由测试守住）：

| 维度 | 现状 | 目标 |
|---|---|---|
| 语义色对比度（11px 徽章文字，WCAG AA 需 4.5:1） | 浅色 danger 3.34 / warning 1.73 / accent 3.84 / success 2.57；深色 danger 3.75 / accent 3.21，**全部不达标** | 主题 × 语义色 × 底色全组合 ≥ 4.5:1，由 `tests/test_contrast.py` 遍历断言 |
| 字号阶梯 | 7 种取值（10/11/12/13/14/15/17）散落 65 处硬编码 | 5 档 token（17/15/13/12/11），最小字号 10px→11px，QSS 中零字面量 |
| 圆角 | 11 种取值；`_board_qss` 单块内就有 8/10/12/14/17，而 token 只定义 6/8/12 | 全部走 token，圆形用 `size/2` 计算而非写死 17px |
| 间距 | QSS padding 24 种取值 46 处、setContentsMargins 21 种 43 处、setSpacing 8 种 | 8 档间距 token |
| 键盘焦点可见性 | 全应用仅 4 条 `:focus` 规则，无 `QPushButton:focus`；分段控件 `NoFocus` | 所有可聚焦控件有可见焦点环；分段控件可键盘操作 |
| 命中区 | 卡片勾选框 20×20、标签色块 34×22、对话框移除钮 22×22、浮窗关闭钮 22×22 | 全部 ≥ 24×24（视觉尺寸可保持，靠 padding 扩命中区） |
| 图标体系 | 矢量自绘 / Unicode 字形（`» ▾ ✕ ＋ ≡ ☑ ⏱`）/ emoji（`📂 📎 🍅 ⭐`）三套并存 | 单一矢量图标工厂，统一线宽与基线 |
| 手势可发现性 | ≈11 个纯鼠标手势，**0 个**被文档化 | 速查面板增「鼠标与手势」分区，全覆盖 |
| 界面密度 | 无任何字号/密度设置 | 三档密度设置，即时生效并持久化 |

---

## 1. 现状诊断（全部经代码/实测验证）

### 1.1 已经做得好，本轮**不动**

这些是刻意设计过的，改动只会破坏它：

- **i18n 机制**：AST 实测 424 条 `tr()` 文案中 421 条有英文（99.3%），仅 3 条缺失；中文原文即 key + 缺失回退原文，设计正确。
- **主题系统**：`AppTheme.register` 的弱引用/`_qt_alive` 监听器卫生、调色板与 QSS 同步下发、macOS 原生外观跟随 —— 实现质量高。
- **动效门控**：`motion.enabled()` 在**每个**动画点显式检查（`board_view.py:506,2433,2569,3910`、`main_window.py:582`、`controls.py:110,214`…），缓动统一 `OutCubic`，播完即卸 `QGraphicsOpacityEffect`。Qt 6.10 无系统级「减弱动效」接口，现有总开关是合理替代。
- **QSS 单点下发**：`_board_qss` 按 mode 缓存、只在 `BoardView` 一次 `setStyleSheet`，避免嵌套 re-polish（实测主题切换 285ms→一遍 polish）。**新增样式必须沿用这个模式，不得逐控件设样式。**
- **数据层**：原子写、损坏隔离、`.prev` 恢复、撤销栈自适应 —— 与本计划无关，不触碰。

### 1.2 问题清单（按严重度）

#### P0 — 语义色对比度全线不达标（可量化，影响每一次扫卡）

`_board_qss`（`board_view.py:161-169`）给**所有** tone 用同一个底色 `accent_soft`：

```
QLabel#cardBadge[tone="{tone}"] {{ background: {c["accent_soft"]}; color: {c[tone]}; font-size: 11px; }}
```

后果有两个，都很严重：

1. **对比度失败**（底色 `accent_soft` 叠在卡面上：浅色 `rgb(230,237,255)`，深色 `rgb(50,64,92)`）：

   | tone | 浅色 | 深色 |
   |---|---|---|
   | danger（已逾期） | **3.34:1** | **3.75:1** |
   | warning | **1.73:1** | 5.98:1 |
   | accent | **3.84:1** | **3.21:1** |
   | success | **2.57:1** | 5.20:1 |
   | text_secondary（≡ 有备注 / 📎） | 4.67:1 | **4.16:1** |

   徽章字号 11px 属小字，AA 门槛 4.5:1。浅色主题几乎全灭。

2. **语义丢失**：红/橙/绿文字压在**蓝色底**上，色相互相污染；截图里「已逾期 9/10」和「≡ 有备注」看起来是同一枚灰蓝 chip —— 这正是卡片信息行显得吵的根因。

根因还有一层：`COLORS` 的语义色是「给浅底做点缀」的亮度，直接用作文本色本身就偏浅 —— 白底上 warning **2.03:1**、success 3.01:1、danger 3.91:1，同样不达标。`LABEL_COLORS` 浅色下 green 4.31:1、orange 3.79:1 也不达标。

#### P0 — 键盘用户看不到焦点在哪

全应用只有 4 条 `:focus` 规则（`theme.py:282,304`、`board_view.py:255,257`），**没有任何 `QPushButton:focus`**。而 QSS 普遍写 `border: none` / 自定义边框，Qt 原生焦点矩形已被吃掉 —— 键盘 Tab 过工具栏、对话框按钮行时**完全无反馈**。`controls.py:166` 还给分段控件的按钮设了 `NoFocus`，等于「主题 / 语言 / 默认形态」三组设置键盘不可达。全应用仅 `board_view.py:650` 一处设 `Qt.StrongFocus`。

#### P1 — 设计 token 半途而废

`config.py:380-392` 已有 `UI_RADIUS_*` / `UI_PAD_*`，但：

- `_board_qss` 一个块内就混用 `8px`（token）/`10px`（卡片）/`12px`（选择栏）/`14px`（列表列）/`17px`（圆形按钮硬编码）；
- QSS padding **24 种取值 / 46 处**，margin **21 种组合 / 43 处**（连 token 版本都有 `UI_DIALOG_MARGIN_V` / `-2` / `-4` 三种变体）；
- **字号完全没有 token** —— 65 处 `font-size:` 硬编码在 7 个档位上。

#### P1 — 图标三套体系并存

自绘矢量（齿轮/日月/日历/星标/勾选框/交通灯/窗口键）与 Unicode 字形当图标（`»` `▾` `▸` `－` `✕` `＋` `≡` `☑` `⏱`）与 emoji（`📂` `📎` `🍅` `⭐` `☆`）混用。项目自己在 `board_view.py:469` 与 `today_popover.py:45` 写明「部分平台缺字形渲染成豆腐块」才改矢量，却只改了勾选框和星标。更麻烦的是 **emoji 被写进了 i18n key**：`tr("📂 打开工作目录")`、`tr("≡ 有备注")`、`tr("⏱ 插入当前时间")`。emoji 还会按平台渲染成不同字形与基线，卡片信息行因此高低不齐（截图可见）。

#### P1 — 工具栏是一排 12 项的平铺，靠手写收纳兜底

`board_view.py:3190-3382`：`setFixedHeight(56)` + 12 个控件 + 窗口键，收纳顺序硬编码为 `("stats","calendar","bulk_add","export","archive","add_list")`。设计已经在跟约束打架 —— 空看板引导文案必须**根据收纳状态改写**（`board_view.py:4363-4373`），指向一个可能被折进菜单的按钮。此外：动作之间无分组/分隔；`:pressed` 态缺失；拖放目标态复用 `:checked` 样式（`board_view.py:238-246`）导致「激活中」与「可投放」不可区分。

#### P1 — ≈11 个纯鼠标手势零文档

`shortcuts_dialog.py:29-55` 只列 15 行键盘快捷键，**鼠标手势一行都没有**：点色条=按标签过滤、空白处滚轮=横向滚动、拖卡到「归档」/「今日」按钮、拖外部文件到列建卡、双击列标题重命名、双击日历空白格建卡、拖列头搬整列、拖板底搬窗口、拖到折叠列自动展开、悬停卡片出 `✕`、悬停备注徽章出预览。全靠在 tooltip 里散落提示。

#### P1 — 列表色点是「假语义」

`_list_accent`（`board_view.py:118-124`）用 `crc32(list_id) % 7` 取色 —— 稳定但**无意义**。截图里「待办=蓝 / 进行中=橙 / 已完成=粉」，用户会合理推断这是状态色，实际只是哈希。这是典型的误导性可供性（false affordance）。

#### P2 — 空态与首次运行是纯文字

`_empty_hint`（`board_view.py:3414-3419`）是居中透明 `QLabel`，且设了 `WA_TransparentForMouseEvents` —— 没有插图、没有可点 CTA，文案让用户去右上角找按钮。搜索无结果、被过滤空、空列、空归档、空今日清单均无专门空态。首次运行无任何引导。

#### P2 — 命中区不足

`_CardCheckButton` 20×20（`board_view.py:494`，最高频操作）、标签色块 34×22（`card_dialog.py:180`）、对话框移除/清除钮 22×22（`card_dialog.py:672,751,1027`）、浮窗关闭钮 22×22（`today_popover.py:466`）。均低于 24×24 标准。

#### P2 — 对话框没有统一外壳

10 个对话框各写各的：模态性不一致（归档/日历非模态，其余模态）、最小宽 360/420/420/460/520/760 全凭手感、标题/边距/页脚按钮行/主按钮位置/`Esc` 与 `Return` 语义各实现一遍。没有 `_BaseDialog`。

#### P2 — 无内容缩放能力

全应用没有任何字号/密度设置（`zoom` 指的是窗口最大化，不是内容缩放）。10–13px 是唯一档位，HiDPI 与低视力用户无路可走。且多处固定尺寸（工具栏 56、列宽 272、`setFixedWidth(96/72)`、`setFixedHeight(90)`）会让「放大字号」直接裁切 —— 这是必须先解掉的耦合。

---

## 2. 设计基线（本计划要建立的规则）

### 2.1 Token 体系

`config.py` 新增，`app/views/tokens.py`（新建）提供取值函数：

```python
# 字号阶梯（px）——5 档收敛现有 7 种取值，取消 10px
UI_FONT_DISPLAY, UI_FONT_TITLE, UI_FONT_BODY, UI_FONT_LABEL, UI_FONT_CAPTION = 17, 15, 13, 12, 11

# 间距阶梯（px）
UI_SPACE = (2, 4, 6, 8, 12, 16, 20, 24)

# 圆角（补全）
UI_RADIUS_SHELL  = 14   # 看板列外壳
UI_RADIUS_PANEL  = 12   # 分区卡片 / 浮层
UI_RADIUS_CARD   = 10   # 卡片
UI_RADIUS_CONTROL = 8   # 输入框 / 按钮
UI_RADIUS_CHIP   = 6    # 徽章 / 色块 / 键帽
# 圆形一律 size/2 计算，禁止写死 17px
```

`tokens.py` 暴露 `font(step)` / `space(n)` / `radius(name)` / `pad(name)`，全部**感知密度档位**（见 §2.4）。所有 QSS 生成器的缓存键从 `mode` 改为 `(mode, scale)`。

### 2.2 色彩规则

**规则一：每个语义 tone 有自己的软底色**，不再共用 `accent_soft`。

**规则二：前景色必须由对比度测试反解**，不允许凭观感取值。下表是已算好的达标值（目标 4.6:1 留余量）：

| tone | 浅色 soft 底 | 浅色前景 | 比值 | 深色 soft 底（叠 `#262A33`） | 深色前景 | 比值 |
|---|---|---|---|---|---|---|
| danger | `#FDE7E8` | `#BD3B3F` | 4.60 | `#4D363E` | `#FF8284` | 4.60 |
| warning | `#FFF1DA` | `#946415` | 4.61 | `#4D433A` | `#FFB65C` | 5.54 |
| success | `#E2F6EC` | `#177C53` | 4.60 | `#2A4843` | `#3FCF8E` | 5.01 |
| accent | `#E6EDFF` | `#2A60E4` | 4.60 | `#32405C` | `#82ACFF` | 4.60 |
| neutral | `#EDF1F5` | `#5C6B7A` | 4.82 | `#393E48` | `#9FAABA` | 4.61 |

`LABEL_COLORS` 浅色修正：green `#15803D`→`#147B3A`（4.61:1）、orange `#C2570A`→`#AC4D09`（4.60:1）；并**新增深色标签色表**，不再复用浅色 pastel 底 + 强推 `#10131A` 文字。

**规则三：板底收敛为中性底 + 单一强调色**（已确认）。
`BoardView.paintEvent`（`board_view.py:3727-3742`）目前是 `QLinearGradient(0,0,w,h)` 三段高饱和 `#5B8DEF → #7C6CE8 → #B46BE8`。改为同色相、极低色差的近中性底（浅色 `#EEF2F7 → #E7ECF5`，深色 `#16181D → #191C24`，去掉紫色中段），让列与卡片成为主角；唯一强调色沿用 `#2F6BFF`。玻璃工具条（`glass`）随之改为**固定不透明表面**，避免同一排 chip 因落在渐变不同位置而颜色不一。

### 2.3 图标规则

新建 `app/views/icons.py`：单一 `QPainterPath` 工厂，统一线宽（1.6–1.8px）、统一 16/18/20/24 尺寸档、颜色取 `AppTheme`。替换掉全部字形/emoji 图标，并从 i18n key 中剥离 emoji（`tr("📂 打开工作目录")` → `tr("打开工作目录")` + 图标），同步更新 `i18n.py` 词典（key 变化，460 条里涉及约 8 条）。

### 2.4 密度

`QSettings` key `ui/scale`，三档 `compact|normal|comfortable` → 系数 0.92 / 1.00 / 1.12，写入 `AppConfig.get_ui_scale()`。设置页「外观」区新增分段控件（复用 `controls.SegmentedControl`）。应用方式：token 函数返回缩放后的值 + `QApplication.setFont` 设定基准字体。**前置条件**：移除与字号冲突的固定尺寸（工具栏 56、列宽 272、`setFixedWidth(96/72)`、`setFixedHeight(90)`），否则放大即裁切。

---

## 3. 分阶段实施计划（8 阶段，每阶段一次提交）

### 阶段 0 · 基线与护栏（不改任何视觉）
**目的**：先让后续每一步都能被自动验证。

- 新建 `docs/ui-ux-plan.md`，落盘本计划。
- 新建 `tests/test_design_tokens.py`：断言 QSS 中不出现字面量 `border-radius`/`font-size`/`padding`（白名单例外清单显式列出）；断言 token 表在 `COLORS`/`DARK_COLORS` 中键集合一致。
- 新建 `tests/test_contrast.py`：遍历「主题 × 语义 tone × 对应软底」，断言 ≥4.5:1（≥15px 的文本 ≥3:1）；失败信息直接打印候选值，供实现时反解。
- 扩写 `smoke_test.py`：固定窗口尺寸出图，作为每阶段人工核对产物（不引入像素级 diff，避免跨平台字体渲染导致假红）。

**验收**：两个新测试通过（阶段 0 的对比度测试会**先红**，红是预期的基线快照 —— 用 `expected_failures` 显式登记，阶段 2 转绿）。

### 阶段 1 · Token 体系补全（基础，视觉应无变化）
- `config.py`：新增 `UI_FONT_*`、`UI_SPACE`、`UI_RADIUS_SHELL/CARD`；清理 `UI_DIALOG_MARGIN_*` 的 `-2/-4` 变体。
- 新建 `app/views/tokens.py`。
- 全量替换 65 处 `font-size`、24 种 padding、11 种圆角、21 种 margin、8 种 spacing。
- `_board_qss` / 各对话框 QSS 缓存键改 `(mode, scale)`。
- **同步更新既有样式断言**（否则必红）：`test_board_view.py:199,904-908`、`test_calendar_view.py:188-189`、`test_card_dialog.py:307-309,619,658`、`test_notes_popover.py:210-219,258,287`、`test_theme.py:63-71`。

**验收**：全部测试绿；smoke 三张截图与阶段 0 逐像素接近（字号/圆角收敛后允许极小差异，人工确认无版式位移）。

### 阶段 2 · 色彩与对比度修正（视觉收益最大）
- `config.py`：新增 `danger_soft/warning_soft/success_soft/neutral_soft`（两主题）；按 §2.2 表修正语义前景色；修正 `LABEL_COLORS` 浅色两项 + 新增深色标签表。
- `board_view.py:161-169`：徽章规则由「统一 `accent_soft` 底」改为**按 tone 各配底色与前景**。
- `board_view.py:3727-3742` + `COLORS/DARK_COLORS`：板底按 §2.2 规则三收敛为中性底。
- `board_view.py:248-260`：搜索框/工具 chip 的 `glass` 改固定表面。
- 卡片 `border-radius: 10px` → `UI_RADIUS_CARD`，列表列 `14px` → `UI_RADIUS_SHELL`，圆形按钮 `17px` → `size/2`。
- `theme.label_style()`（`theme.py:247-252`）改走新的标签表。

**验收**：`tests/test_contrast.py` 全绿；smoke 浅/深/英三张截图重出并人工核对；卡片信息行「一眼分得清逾期/备注/附件」。

### 阶段 3 · 图标体系统一
- 新建 `app/views/icons.py`。
- 替换调用点：`board_view.py`（`»` `▾` `▸` `－` `✕` `≡` `☑` `📂` `📎` `🍅` `…`）、`card_dialog.py`（`＋` `✕` `⏱` `📎`）、`today_popover.py`（`✕`、星标）、`notes_popover.py`、`archive_dialog.py`。
- 剥离 i18n key 中的 emoji，同步 `i18n.py` 词条；`WORKDIR_BADGE_TEXT`（`board_view.py:148`）从 emoji 改为图标标记（注意：`_fit_meta_badges`、事件过滤、附件识别三处都靠它做文本判定，需一并改为图标判定）。
- 卡片信息行改「图标 + 文字」同基线排布。

**验收**：卡片信息行垂直节奏一致；`board_view.py:1026-1035` 那三处基于 emoji 前缀的判定全部改为图标判定且有测试覆盖。

### 阶段 4 · 工具栏重构与响应式
- `board_view.py:3190-3382`：按语义分四组（看板切换 / 今日·统计 / 搜索 ｜ 新建·批量·归档·日历·导出 ｜ 设置·主题·折叠），组间用间距档位而非硬编码分隔。
- 移除 `setFixedWidth(96)`/`setFixedWidth(72)`，改内容驱动 + 最小宽。
- 补齐 `:pressed`、`:focus`、`:disabled` 态；`dropTarget` 属性改用独立视觉（虚线强调色边框），不再与 `:checked` 同款。
- 收纳逻辑：保留 `_apply_toolbar_overflow`，但把顺序提为命名常量并写明理由；空看板引导不再按收纳态改写文案（阶段 6 用 CTA 取代）。
- 评估把 `BOARD_MIN_WIDTH`（980）降下来 —— 工具栏是唯一约束，分组后应有余量，让 1024 宽屏幕可用。

**验收**：980 / 1100 / 1440 三档宽度 + 中英文下无重叠、无裁切；窄窗下「»」菜单项与原按钮行为一致。

### 阶段 5 · 焦点、键盘与命中区
- `theme.py:global_qss()` 新增统一焦点环规则（`QPushButton:focus`、`QCheckBox:focus`、`QAbstractButton:focus`），用 accent 描边并复用圆角 token；对 `border: none` 的 `#boardToolBtn`/`#boardThemeBtn`/`#boardCollapseBtn` 逐个补 `:focus`。
- `controls.py:166`：分段控件按钮改可聚焦 + 左右方向键切换 + `:focus` 视觉。
- 命中区提到 ≥24×24：卡片勾选框 20→24（视觉仍 20，靠 padding）、标签色块 22→24、对话框移除/清除钮 22→24、浮窗关闭钮 22→24。
- 为所有自绘图标按钮补 `setAccessibleName`（齿轮/日月/日历/折叠/更多/开关/分段/桌宠画布/交通灯/窗口键）；`reapply_texts()` 里同步刷新。
- 新建 `tests/test_focus_a11y.py`：断言每个可交互按钮命中区 ≥24、有 accessibleName、有 `:focus` 样式。

**验收**：纯键盘可完成「新建卡片 → 设优先级 → 完成 → 归档」全流程且焦点始终可见。

### 阶段 6 · 可发现性与空态
- `shortcuts_dialog.py`：改双分区 —— 「键盘快捷键」（现有 15 行）+ **「鼠标与手势」**（§1.2 P1 列的 11 条全收）；加一行提示从哪进（`?`）。
- 列表色点二选一（**建议：去掉色点**，改用列头计数 chip 承载信息量；若保留则必须赋予真实语义并写进文档）。理由是 `crc32 % 7` 的假语义比没有更糟。
- 空看板态重做：图标 + 一句说明 + **可点的主 CTA**（「创建第一列」，去掉 `WA_TransparentForMouseEvents`）+ 次入口（从模板新建 / 导入 Trello）；删掉按收纳态改写文案的 hack。
- 补空态：搜索无结果、过滤后为空、空列、空归档、空今日清单。
- 首次运行：一次性可关闭的 3 步引导（桌宠=入口、看板=主体、`?`=全部手势），写 `QSettings` 标记只出一次。

**验收**：11 条手势在面板中全部可查；空看板一屏内可完成首次建列；`_update_empty_hint` 不再依赖 `_overflow_hidden`。

### 阶段 7 · 字体、密度与缩放
- 设置页「外观」新增「界面密度」分段控件；`config.py` 增 `KEY_UI_SCALE` + `get/save_ui_scale()`；`theme.py` 应用时重建 QSS 与基准字体。
- 移除全部与字号冲突的固定尺寸（工具栏 56、列宽 272、`setFixedWidth`、`setFixedHeight(90)` 等），改由 token 计算。
- 按 §2.1 落地 5 档字号阶梯；10px 全部升到 11px。

**验收**：三档密度 × 浅深主题 × 中英文共 12 种组合下无裁切无重叠；smoke 出三档截图；切换密度即时生效、重启保持。

### 阶段 8 · 验证与发布
- CI 全绿：`python -m unittest discover -s tests -t .`（Windows + macOS / Python 3.12 / offscreen）。
- 重出全部 5 张 smoke 截图，人工核对浅色 / 深色 / 英文。
- 更新 `README.md`（功能列表里的 emoji 图标描述、空态文案、新增密度设置）与 `RELEASE_NOTES.md`；`APP_VERSION` 按项目惯例递增。

---

## 4. 执行拓扑与写作用域

**关键约束：本计划不能靠多 agent 横向铺开。** `board_view.py`（4,511 行）与 `config.py` 是热点文件，阶段 2/3/4/5/6/7 全都要改它们，并行写必然冲突。**按文件串行、按文件并行**：

| 工作流 | 独占写作用域 | 负责阶段 |
|---|---|---|
| W1 基础 | `app/config.py`、`app/views/theme.py`、新建 `app/views/tokens.py`、新建 `app/views/icons.py` | 0、1、7（config/theme 部分） |
| W2 看板 | `app/views/board_view.py`、`app/views/main_window.py` | 2、3、4、5、6（看板部分） |
| W3 对话框 | `app/views/{card,quick_add,batch_edit,archive,settings,shortcuts}_dialog.py`、`app/views/{calendar_view,controls,notes_popover,today_popover}.py`、`app/i18n.py` | 3、5、6（对话框部分）、7（设置页） |
| W4 验证 | `tests/*`、`smoke_test.py`、`README.md`、`RELEASE_NOTES.md` | 0、8 |

依赖：**W1 必须先行**（token 与图标模块是所有改动的前置）；W2 与 W3 在 W1 完成后可并行（文件不相交）；W4 跟随各阶段末尾。`app/i18n.py` 归 W3 独占 —— W2 新增文案以清单形式交给 W3 落笔，避免两方同时改词典。

---

## 5. 验证与验收

- **每阶段门禁**：`python -m unittest discover -s tests -t .` 全绿 + `python smoke_test.py` 出图人工核对，二者通过才进下一阶段。
- **新增测试**：`test_design_tokens.py`（token 完整性 + 零字面量）、`test_contrast.py`（对比度遍历）、`test_focus_a11y.py`（焦点/命中区/无障碍名）。
- **必须同步修改的既有断言**：`test_board_view.py:199,904-908`、`test_calendar_view.py:188-189`、`test_card_dialog.py:307-309,619,658`、`test_notes_popover.py:210-219,258,287`、`test_theme.py:63-71`。
- **回归重点**：徽章折叠逻辑 `_fit_meta_badges`（`CARD_META_BADGE_MAX=4`，最坏组合约 281px > 列内可用 229px，改图标宽度后需重测）；`WORKDIR_BADGE_TEXT` 的 emoji 前缀判定三处；`_apply_toolbar_overflow` 的收纳顺序；三档密度下的列高同步 `_sync_height_to_content`。
- **不做像素级截图 diff**：跨平台字体渲染会假红，截图仅作人工核对产物。

---

## 6. 明确假设与不做的事

**假设**：单一强调色沿用现有 `#2F6BFF`（浅色）/ `#5B8DEF`（深色），不引入第二品牌色；板底改为中性后，卡片与列的层次靠 1px 边框 + 微弱阴影而非底色对比（实测中性底与白卡仅 1.12:1，必须靠边框）。

**不做**：不换技术栈、不引入第三方 UI 库（继续 QSS + 自绘）；不改数据模型与存储格式；不重写 `board_view.py` 架构（只做局部替换）；不做系统级「减弱动效」集成（Qt 6.10 无接口，现有 `motion` 总开关已覆盖）；不动 §1.1 列出的已良好部分。

**风险与回退**：阶段 2 的板底改动是最大视觉变化，独立成一次提交，回退只需还原 `COLORS/DARK_COLORS` 三个键 + `paintEvent` 一处；阶段 7 的密度设置会触碰最多固定尺寸，排在最后并在前三档组合下逐一验收。

---

## 7. 两路并行审计的补充发现（已并入对应阶段）

阶段 0 期间对 `board_view.py`/`main_window.py` 与 10 个对话框/浮窗做了两轮只读审计，
以下是计划正文之外新增的确认项，按归属阶段列出。**带 ⚠ 的是功能性缺陷（不只是观感）。**

### 并入阶段 0（已处理）

- ⚠ **`smoke_test.py` 抓的是未同步的设置页**：只调 `retexts()` 没调 `sync_from_prefs()`，
  参考图里分段控件没有任何选中高亮、两个开关全画在关闭位 —— 与真实界面不符，
  会误导后续所有基于截图的视觉核对。
  已修：抽出 `AppController.sync_settings_dialog()`，冒烟测试改走 `_on_settings_open()`
  真实打开路径。重新出图确认「System / English / Milk 有选中态、两开关为开」。
- ⚠ **`SegmentedControl.set_value()` 遇未知值静默 return**（`controls.py:260-266`）→
  控件可能呈现"零选中"。属阶段 5 一并修（回退到首项并记日志）。

### 并入阶段 1

- `theme.py:278` 硬编码 `8px`，而 `theme.py:301` 两行之后用的是 token —— 全局 QSS 自身就漏了。
- 新增 `UI_PAD_INPUT = "6px 10px"`、`UI_PAD_INPUT_DENSE = "4px 8px"`：表单类控件的
  10 种 padding 配方（`6px 10px`/`4px 8px`/`5px 10px`/`1px 5px`/`3px 8px`/`1px 6px`…）
  让同类字段高度差 1–2px。
- ⚠ **`_input_dialog_style` 是跨模块私有导入**（`batch_edit_dialog.py:26` 从
  `quick_add_dialog` 导入）→ 提到 `theme.py` 的 `form_dialog_style(c)`；
  `card_dialog.py:129-133` 与 `quick_add_dialog.py:54-58` 的 `QLabel[cap]` 规则逐字节重复。
- ⚠ **`_ElidedLabel` 困在 `archive_dialog.py:34-67`**，而 `card_dialog.py` 的附件行
  （`:738-741`）与资金环节名（`:1012`）有完全相同的长文本溢出 bug →
  提升为 `controls.ElidedLabel` 并两处复用。
- `shortcuts_dialog.py` 的 docstring 写"双列对照展示"但实际是单列 —— 阶段 6 一并修正。

### 并入阶段 2

- ⚠ **`text_disabled` 被当作"提示文字"用，实测 1.96:1（浅色）/ 3.06:1（深色）**：
  `card_dialog.py:371-378,1113-1120` 的空态占位（"未设置"）、`calendar_view.py:182-184`
  的非本月日期号（叠 `mask` 后约 1.7:1）、`notes_popover.py:140-156` 的 `#popHint`（约 2.2:1）、
  `board_view.py:330-334` 的空列提示（约 2.0:1）。
  → 新增 `text_hint` token（取 `text_secondary` 一档，保证 ≥4.5:1），
  并把 `tests/test_contrast.py` 的审计面从"徽章"扩到"提示文字"。
- ⚠ **标签色块 hover 与 checked 是逐字节相同的规则**（`card_dialog.py:203-204`）→
  悬停未选中色块看起来就像已选中，鼠标扫过时读不出当前标签集。
  且 `theme.label_style()`（`theme.py:250-252`）在深色下把六种色的边框统一成 `#10131A`，
  选中态只剩"淡彩填充"这一个载体 → 拆开 `:hover`（1px accent）与 `:checked`（2px 色 + 勾选标记），
  深色改为每色各自提亮的前景，不再统一压黑。
- ⚠ **`:checked` 与 `[dropTarget="true"]` 像素完全一致**（`board_view.py:238-246`）→
  "筛选已开启"与"松手就落这里"是两件事却长得一样。dropTarget 改虚线强调色边框。

### 并入阶段 4

- ⚠ **「今日 N」在过滤态报的是匹配数不是今日数**（`board_view.py:4178`，`_today_count_from` 同病）→
  输入 "OKR" 后 chip 变成「今日 2」，标签与数值不符。改为恒取 `_today_focus_total()`，
  匹配数另给一个专属 chip。
- ⚠ **统计标签是收纳第一顺位**（`_overflow_order` 首位）→ 而它是「匹配 N 张」的唯一渲染点，
  于是**搜索时最该看到的数字最先消失**。过滤态下把 `stats` 移出收纳名单。
- ⚠ **窄窗下工具栏会重叠且无兜底**：`_apply_toolbar_overflow`（`:4438-4442`）把可收纳项隐藏完就停，
  剩余不可收纳项合计约 460px + 间距，而 `_effective_min_size` 允许 320px。
  循环后补终局降级：搜索框收到 60px → 去标题后缀 → 再隐藏 `today`；补 480px 回归用例。
- ⚠ **标题被优先牺牲**：`_ElidedButton` 最小宽 88px + 搜索框独占 stretch（`:3262`）→
  760px 下标题只剩「我的…」。给标题更高优先级（最小宽 140 + 参与 stretch），先压搜索框。
- ⚠ **工具栏只在 resize 时重算收纳**（`:4382`）→ 标签 chip 出现/消失、`今日 12` 变宽都不会触发重排。
  在 `_apply_filter` / `reapply_texts` 末尾补一次调用（隐藏集未变时函数自身早退，成本可忽略）。
- ⚠ **齿轮按钮没有 `setFixedSize(34,34)`**（`:3303-3308`，而日历/主题键都有）→
  `border-radius: 17px` 画出来是圆角矩形而非圆形；且浅深两版截图里它都是一块**不透明白块**，
  是工具栏上最扎眼的渲染缺陷。属阶段 4 一并修（尺寸 + 验证深色下 `glass` 是否真的生效）。
- ⚠ **语言切换后「拖到归档/今日」的提示被截断**（`reapply_texts` `:3589-3599` 覆盖了构造函数里
  带说明的长 tooltip，而完整词条在 `i18n.py:702-705` 是有的）→ 英文用户失去这两个手势的唯一披露。
- 无 `:pressed` 态（`:227-246`）；全局 `QPushButton:pressed` 在特异性上输给 `#id:hover`。

### 并入阶段 5

- ⚠ **分段控件键盘不可达**（`controls.py:166` `NoFocus`）→ 主题/语言/默认形态三组设置纯鼠标。
  容器 `StrongFocus` + 左右方向键 + 焦点环 + 逐按钮 `setAccessibleName`。
- ⚠ **日历 100% 纯鼠标**（`_Chip`/`_DayCell` 均无焦点策略；编辑/拖拽/建卡全是鼠标事件）→
  键盘用户能翻月却打不开、改不了期、建不了卡。`_DayCell` 改 roving focus。
- ⚠ **日历默认尺寸显示不完整月份**：`_CELL_MIN_H = 108` × 最多 6 行 = 668px，
  而 `setMinimumSize(760, 540)` → 视口约 400px，**首屏只看到约 3.6 行**，
  月历本体一打开就带纵向滚动条。行高改为按视口推导（`height() // rows`）。
- ⚠ **`QDateEdit`/`QComboBox`/`QSpinBox` 没有 `:disabled` 规则**（`theme.py:285-292`）→
  批量编辑里"不修改"状态的日期框看起来完全可用，点了没反应也没有解释；
  同一个对话框里 `QLineEdit` 却会变灰。补 `:disabled` 规则。
- ⚠ **全项目仅 4 处 `setAccessibleName`，`setBuddy`/`setTabOrder` 为 0**：
  四个 ✕ 删除钮、`_StarButton`、`ToggleSwitch`、`_CardCheckButton`、`_Chip` 对读屏都只报"button"。
- ⚠ **`ToggleSwitch` 无 accessibleName、`paintEvent` 不看 `hasFocus()`**（`controls.py:61-105`）。
- ⚠ **命中区不足的完整清单**（正文只列了 4 处）：标签色块 34×22、四个 ✕ 22×22、
  卡片勾选框 20×20、日历条目约 17px 高、`#dayMore`「还有 N 项…」约 14px 高
  （而它是触达当天第 4 张以后卡片的**唯一**入口）。
- ⚠ **勾选框是 `QPushButton` 且不可 check**（`board_view.py:491-499`）→
  读屏只报"完成 X, button"，永远不报勾选状态；完成与否只由删除线和绿底承载。
  改 `setCheckable(True)` 让 QAccessible 暴露状态（自绘保留）。
- ⚠ **卡片无键盘删除路径**：✕ 只在 hover 时显示（隐藏控件也跳过 Tab），右键菜单有"归档"无"删除"。
  补 `Delete`/`Backspace` + 菜单项（撤销已兜底）。
- ⚠ **勾选框悬停变色不重绘**（`:577-587` 在 `paintEvent` 里读 `underMouse()`，却没有 enter/leave）→
  最高频操作的悬停反馈不可靠。

### 并入阶段 6

- ⚠ **今日浮窗打开时不聚焦输入框**，且 `_close_btn` 在 `_add_edit` **之前**创建
  （`today_popover.py:465` vs `:474`）→ `Qt.Popup` 抢走键盘后，用户直接打字是打给"没有"，
  Space/Enter 还可能命中已聚焦的 ✕ 把浮窗关掉。对比 `QuickAddDialog.showEvent` 的
  `setFocus() + selectAll()`（`quick_add_dialog.py:190-193`）。
- ⚠ **备注浮层完全没有键盘路径**：锚点徽章是 `QLabel`（不可聚焦）、浮层无 `keyPressEvent`、
  固定/取消固定只有鼠标过滤（`board_view.py:1225-1228`）、没有 Esc 关闭 →
  在一个**已经键盘可达**的卡片上，备注预览是唯一够不着的功能。
- ⚠ **日历拖放高亮不会被落点清除**：`dropEvent`（`calendar_view.py:273-283`）只发信号不 `_apply_style()`，
  只有 `dragLeaveEvent` 会复位 → 拖到同一天（控制器空操作）时该格永久保持高亮，
  用户无法判断是否落点成功。同时 `_Chip` 没有拖影（`setPixmap`/`setHotSpot`）。
- ⚠ **日历没有"选中日"状态**：`_apply_style` 只有 today/hover/drop 三态，单击日期无任何反应 →
  用户点某天想看当天卡片，得到的是"什么都没发生"。
- ⚠ **日历改期无确认**：`dropEvent` 后无 toast → 慢刷新时不知道是否生效。
- ⚠ **今日浮窗里打开卡片是纯鼠标**（`_TitleLabel` 的 `mouseReleaseEvent`）→
  键盘用户能勾选、能加星，唯独打不开卡片。
- ⚠ **快捷键面板只能靠按 `?` 到达**（`main_window.py:215-217`）→
  Windows 上不先知道快捷键就学不到快捷键，是 #7/#9/#25/#28 这些可发现性问题的总闸门。
  设置页加一行入口（托盘菜单同步）。
- ⚠ **空看板引导没有"怎么收回去"的说明** → 补一行「Esc 收起为桌宠」。
- 列表色点 `crc32 % 7` 的假语义（见正文）→ 建议直接去掉。
- 「列头 count + ▾」读起来像一个下拉，而真正的 ⋯ 菜单默认全透明（`:1610-1625`）；
  `▾` 同时表示"打开菜单"（标题）与"折叠"（列头）两种含义。
- 多选操作栏 8 个控件无收纳逻辑，约 600px 以下会两头裁切（工具栏有 `_apply_toolbar_overflow`，它没有）。

### 并入阶段 7

- ⚠ **卡片标题不设行数上限**（`board_view.py:931-936`）→ 一条啰嗦标题能撑成 6–8 行卡片，
  把列内其它卡片挤出视口，卡片高度参差、扫读失效。改 3 行省略（复用 `_ElidedButton._apply_elide` 的做法）。
- ⚠ **列标题既不省略也无 tooltip**（`:1561-1572,1608`）→ 长列表名被硬裁在半个字上，
  除了进重命名模式没有别的办法读到全名。
- ⚠ **附件长文件名会把 ✕ 挤出可视区**（`card_dialog.py:738-741`，表单区还关掉了横向滚动条）。
- ⚠ **设置页 `setFixedWidth(560)` + `setSizeGripEnabled(True)`**：尺寸把手只能纵向拖，
  却在承诺横向可调；且不记忆尺寸。默认高度只放得下 3/7 个分区，
  皮肤色块被底部页脚分隔线**从中间切断**（滚区没有底部内边距）。
- ⚠ **批量添加固定 160px 编辑框**（`quick_add_dialog.py:264-271`）而单批上限 100 行 →
  只能校对 8 行。改 `setMinimumHeight`。
- 一次性输入框（快速添加/批量编辑/快捷键）可拉伸但无可重排内容 → 改 `setFixedSize(sizeHint())`。
- `_due_toggle_btn` `setFlat(True)` 却不在 `_flat_buttons` 里（`card_dialog.py:399-407`）→
  渲染成第三个物种的按钮，且标签在「设置日期/清除日期」间切换导致整行回流。
- 优先级文案不一致：卡片对话框「无/高/中/低」vs 批量编辑「P1 高」（`PRIORITY_MARKS` 未用于主编辑器）。
- ⚠ **卡片对话框无脏检查**：Esc 直接丢弃整份清单 + 长备注，没有任何提示。
- ⚠ **空标题错误反馈只有颜色**：`card_dialog.py:1161-1168` 只把边框染红，
  而 `quick_add_dialog.py:213-219` 会给出 `#inputError` 文字说明 → 统一成带文字说明。
- `primary_button_style`（`theme.py:52-65`）缺 `:pressed` / `:disabled` / `:focus`；
  `:disabled` 全项目只有 `archive_dialog.py:376-379` 一处写了 → 提取 `secondary_button_style(c)` 推广。

### 明确不动（两轮审计共同确认）

`_CardCheckButton` 的 140ms 勾选动画；`[done="true"]` 删除线 + 文档化的对比度理由；
拖放目标语言（列 2px 边框 / 3px 插入线 / `dropTarget` 填充 / 成组拖影张数）；
过滤反馈（命中/总数、`_pinned_ids`、拖放被禁时给 toast 说明而非静默禁止）；
Esc 逐级链（重命名 → 多选 → 搜索 → 收起）；`motion.enabled()` 的全量门控；
`_WheelIgnoreMixin` 防触控板误改值；`sync_from_prefs` 的静默同步；
`ToggleSwitch` 的 44×26 / 右为开 / 120ms 机制；日历「还有 N 项…」是真菜单 + 650ms 翻月停留；
归档对话框的 `:disabled` 样式与完整空态；列高"跟内容长、超视口再内部滚动"的模型。


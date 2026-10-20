# 视觉皮肤（Styles）：实现说明

> 状态：**已实现**。5 套皮肤可在「设置 → 外观 → 视觉皮肤」切换，默认 `editorial`，
> 选择写入 `QSettings`（键 `ui/skin`），下次启动自动恢复。
> 真实运行截图：[`style-previews/skin-*.png`](./style-previews/)（`scripts/theme_preview.py` 渲染）。

## 1. 皮肤是什么

皮肤 = **一套配色 + 一套造型**，与「深浅色」正交（同一皮肤有 light / dark 两份色表）。

| key | 名称 | 一句话 | 标志性特征 |
|---|---|---|---|
| `editorial` | 瑞士排版（默认） | 纸色底 + 墨黑字 + 单一电光蓝 | 圆角 3–6px；徽章**无底**、左侧一根 2×10 竖线色标；列头 800 字重；工具栏描边按钮；宋体不上、等宽不上 |
| `warm` | 暖纸手帐 | 奶油纸 + 陶土色 | 圆角 6–16px；**列头宋体**；徽章药丸；玻璃工具栏；深色为「暖黑」而非冷黑 |
| `terminal` | 等宽终端 | 荧光绿 + 等宽字，只做暗底 | 零圆角；**徽章写成行内文本**（`!逾期` / `~P2` / `#1/2` / `.附件`）；板底 64px 网格；添加按钮无框加号 |
| `brutal` | 新粗野主义 | 2.5px 黑描边 + 硬投影 | 零圆角；列头 800 字重；徽章 1.6px 描边药丸；板底撒点；**卡片右下 5px 实色硬投影**（自绘） |
| `frosted` | 玻璃拟态 | 半透明面板 + 柔光底 | 圆角 8–18px；面板 `rgba(255,255,255,.72)`；板底三团柔光；卡片保持不透明（见 §4） |

## 2. 代码地图

| 位置 | 职责 |
|---|---|
| `app/config.py: UI_SKINS` | 皮肤表：`{key: {name, light: 色表, dark: 色表, shape: 造型}}` |
| `app/config.py: SKIN_LABEL_COLORS` | 皮肤专属的标签色（卡片左缘色条 / 对话框色块） |
| `app/config.py: get_ui_skin / save_ui_skin` | 偏好读写（白名单，非法值回退默认） |
| `app/views/tokens.py: shape() / radius()` | 造型取值层：缺省补全 + 密度缩放（直角皮肤可到 0） |
| `app/views/theme.py: AppTheme.skin/set_skin/colors/shape` | 运行期状态：`colors()` 按皮肤取表，`set_skin()` 落盘 + 重下 QSS + 广播 |
| `app/views/board_view.py: _board_qss(mode, scale, skin)` | 看板整块样式表（缓存键含皮肤） |
| `app/views/board_view.py: BoardView._paint_hard_shadows` | 新粗野主义硬投影（自绘，见 §4） |
| `app/views/settings_dialog.py: signal_ui_skin_selected` | 设置页单选组 + 色板图标 |
| `app/controllers/app_controller.py: _on_ui_skin_selected / _refresh_for_skin` | 落盘 → 下一轮事件循环重下样式表 → 各视图按皮肤重建 |
| `scripts/theme_preview.py` | 离屏渲染 5 皮肤 × 明暗的看板截图（改皮肤后必跑） |
| `tests/test_ui_skins.py` | 结构 / 对比度 / 持久化 / 切换四类护栏（24 项） |

## 3. 新增一套皮肤

1. 在 `AppConfig.UI_SKINS` 加一项：`light` / `dark` 两份色表，**键集合必须与 `COLORS` 完全一致**
   （`tests/test_ui_skins.py::test_every_skin_has_light_and_dark_with_identical_keys` 会拦）；
2. `shape` 里可选的键（缺省值见 `tokens._SKIN_SHAPE_DEFAULTS`）：
   `radii`（写了就要写全 6 个名）、`border_width`、`card_weight`、`list_weight`、
   `title_weight`、`title_letter_spacing`、`badge_style`（`pill|outline|bar|text`）、
   `board_chrome`（`plain|grid|dots|glow`）、`toolbar_button`（`glass|outline`）、
   `add_button_dashed`、`hard_shadow`（px，0=无）、`check_style`（`rounded|square`）、
   `font_mono`、`font_list_serif`；
3. 加 `SKIN_LABEL_COLORS[新key]` 的 `light` / `dark`；
4. 跑 `python -m unittest tests.test_ui_skins`，再跑
   `QT_QPA_PLATFORM=offscreen python scripts/theme_preview.py --skin 新key` 肉眼看图。

## 4. 踩过的坑（都写进了代码注释，这里留索引）

| 坑 | 现象 | 结论 |
|---|---|---|
| QSS 给了背景的 QFrame 会整块重画 | 在卡片 `paintEvent` 里先画的硬投影被盖掉（卡片全黑） | 投影只能由祖先在子控件之前画出 → `BoardView.paintEvent` 末尾统一画 |
| 给卡片单独 `setStyleSheet` 加背景 | 与看板级样式表叠加后卡片整块发黑 | 底色一律由看板级 QSS 下发，控件内不设背景 |
| 自定义 `QAbstractScrollArea` 视口 | 双击/滚轮事件不被接管，离屏下直接段错误 | 换回「祖先一次画完」的投影方案 |
| `@staticmethod` 漏写 | `_build_board_chrome` 把 self 当 `kind`、w/h 整体错位 → 段错误 | 会画出路径的纯函数一律 `@staticmethod` |
| 徽章前缀只能落在文本上 | QSS `::text` 在 Qt 里不生效 | `badge_content()` 生成前缀；皮肤 key 进卡片指纹，切皮肤时整批 rebuild |
| 写死 `white` 的文字（4 处） | 深色系皮肤（终端荧光绿）上白字不可读 | 全部改 `accent_on` / `danger_on`（pet_view 徽章 / settings 主按钮 / 看板删除钮 / 今日浮窗） |
| 玻璃拟态的半透明 | 桌面常驻看板背后是别的窗口，半透明卡片读不清 | 只让**面板**透，卡片 `.96` 以上不透明（与 notes_popover 的既有结论一致） |

## 5. 验证

```bash
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -t .   # 854 项
QT_QPA_PLATFORM=offscreen python scripts/theme_preview.py --out design/style-previews
QT_QPA_PLATFORM=offscreen python smoke_test.py                        # 冒烟截图
```

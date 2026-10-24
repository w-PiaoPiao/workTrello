# AGENTS.md — workTrello

本仓库已建立 CodeGraph 索引（`.codegraph/`，已在 `.gitignore` 中）。
下面第 1 节是探索规则，优先级高于「先 grep 看看」的本能；第 5 节是项目速览，帮你少走弯路。

## 1. 探索规则：先 codegraph，再考虑 grep / read

任何关于「这段代码怎么实现的 / 在哪里 / 谁调用它 / X 到 Y 的流程」的问题，
**第一个动作**是调用 `mcp__codegraph__codegraph_explore`：

- `query` 用**符号名或文件名**，比自然语言长句召回更准。例如：
  `set_skin save_pet_skin 皮肤选择 持久化 调用链`、
  `board_view 卡片拖动 调用链`、`settings_dialog.py 设置同步`。
- `projectPath` 传**本仓库根目录的绝对路径**（就是同时包含 `.git/` 和 `app/` 的那一层）。
  该工具在本环境属于「无默认项目」模式，`projectPath` 是必填项。
- 它等价于 Read：一次返回命中符号的**带行号逐字源码** + 调用链 + 影响面。
  把它当成「已经读过的文件」，**不要再用 read 重读同一批代码**。
- 信任返回值，**不要用 grep 复核**。只有 codegraph 覆盖不到的东西才回退到 read / grep：
  配置文件、文档、`.github/`、i18n 资源、以及任何非索引文件。

**为什么**：codegraph 是预先建好的符号/调用图索引，自己再 grep + 逐文件 read 一遍，
等于把同样的活干两遍。而且本仓库有几个大文件——`app/views/board_view.py` 约 192KB、
`app/controllers/app_controller.py` 约 118KB、`app/config.py` 约 52KB——整篇 read 一次
就是几万到十几万字符，而 `codegraph_explore` 一次通常只返回相关符号的那几段。

## 2. 什么时候别用它

- **盘点 / 审计型**问题（例如「全局 UI 有哪些问题」「设计风格盘点」「交互优化计划」）：
  这类必须通读大量原文，codegraph 的符号摘录帮不上，直接用 read / grep。
- **已知改动就在一个小文件里**：直接 read 那个文件更快。
- 工具报「该项目未索引」：立刻改用内置 grep / read，不要重试，也不要自己跑 `codegraph init`。

## 3. 查询选型与成本

| 意图 | 用什么 | 典型输出 |
|---|---|---|
| 知道符号名，要看它的实现 + 谁调用 | `codegraph node <symbol>`（若该工具已启用） | 1–4K 字符 |
| 开放式问题 / 跨文件流程 | `codegraph_explore` | 14–19K 字符 |

注意：`explore` 的输出可能超过 8K 字符，**会话压缩时中间部分会被裁掉**。
所以一次只问一个聚焦的问题；需要看多个区域时分开问。

## 4. 索引维护

- 后台 daemon 在连接时会自动追平改动，通常无需手动操作。
- 需要手动时：`codegraph sync`（增量，≤1.2s）、`codegraph status`（看是否 up to date）。
- 返回里出现 `⚠ changed since last index sync` 横幅时：**源码是新的、可信**；
  不可信的是符号名 / 行号 / 调用图，对横幅里列出的文件 read 一次即可。
- `.codegraph/` 不进版本控制：新克隆的仓库需要跑一次 `codegraph init` 才有索引。

## 5. 项目速览

- 技术栈：Python + PySide6 桌面应用，入口 `main.py`，业务代码在 `app/`。
- 分层：`app/views/`（界面）、`app/controllers/`（控制器）、`app/models/`、
  `app/services/`（业务服务）、`app/platform/`（平台适配）、
  `app/config.py`（配置与持久化）、`app/i18n.py`（多语言）。
- 打包：PyInstaller `.spec`；测试与冒烟脚本在 `scripts/`。
- 提交信息用中文，按「阶段 N」推进。

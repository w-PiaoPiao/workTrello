# 桌宠看板

Windows 桌面悬浮小工具：**缩小时是一只卡通桌宠，展开是高颜值 Trello 风格看板**。

## 功能

### 桌宠（折叠态）
- 纯代码绘制的卡通小家伙：漂浮、呼吸、随机歪头/跳跃、眨眼，悬停弹跳
- 右上角角标实时显示卡片总数
- 单击展开看板，拖拽移动，右键菜单（展开 / 快速添加卡片 / 暂停动画 / 退出）

### 看板（展开态）
- 渐变背景 + 半透明圆角列表列，浅色 / 深色双主题
- 卡片：标题、备注、6 色标签、截止日期（自动识别"今天/明天/已逾期"，可清除）、完成勾选
- 卡片单击编辑、悬停右上角 ✕ 删除；拖拽跨列表移动、列表内重排（QDrag）
- 列表：双击标题或列头"⋯"菜单重命名、列头"⋯"菜单删除、添加列表
- 顶部统计（总卡片数 / 完成数）、一键切换主题、折叠回桌宠
- Esc 键（看板内任意位置）也可折叠

### 通用
- 无边框置顶窗口、位置与展开尺寸持久化（QSettings）
- 数据 JSON 原子落盘（防抖 + .tmp + fsync + rename），损坏自动隔离备份，
  启动时若检测到最近好副本（.prev）会询问是否一键恢复
- 系统托盘（显示/隐藏切换、气泡通知、退出）、单实例锁

## 运行

```bash
pip install -r requirements.txt
python main.py            # 正常启动
python main.py --debug    # 调试日志
```

或直接双击 `run.bat`。

## 打包

```bash
powershell -ExecutionPolicy Bypass -File tools\build.ps1
```

产物命名：`桌宠看板v{版本号}.exe`（版本号见 `app/config.py` 的 `APP_VERSION`）。

## 测试

```bash
python tests/test_board.py        # 数据层单元测试
python smoke_test.py              # 离屏渲染冒烟测试（生成 smoke_*.png 截图）
```

## 项目结构

```
main.py                        启动入口（单实例锁、全局样式）
app/
  config.py                    全部配置常量、配色板与窗口状态持久化键
  models/
    board.py                   Board / BoardList / Card 数据模型 + BoardStore
    json_io.py                 JSON 原子读写、损坏隔离备份、.prev 好副本恢复
  controllers/
    app_controller.py          控制器（信号连接、业务逻辑、落盘调度、恢复引导）
  services/
    tray_service.py            系统托盘（图标绘制、显示/隐藏菜单、通知）
  views/
    pet_view.py                桌宠（QPainter 绘制 + 属性动画）
    board_view.py              看板（列表列、卡片、拖拽、删除、工具栏）
    card_dialog.py             卡片编辑对话框
    main_window.py             无边框主窗口（折叠/展开动画、Esc 折叠、显隐）
    theme.py                   主题系统（浅/深 QSS、切换广播）
```

## 数据位置

默认在系统用户数据目录（`appdirs`），可用环境变量 `PET_BOARD_DATA_DIR` 覆盖。
开发调试时会在项目根目录生成 `data/board.json`。

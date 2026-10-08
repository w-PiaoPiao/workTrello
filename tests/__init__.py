# -*- coding: utf-8 -*-
"""测试包标记

使 `python -m unittest discover -s tests` 可用（缺此文件时 discover 报
"Start directory is not importable"）。逐文件运行与 pytest 不受影响。

另外在这里预置两件全测试进程共享的隔离设置（模块级执行，早于任何用例）：

- **数据目录**：只有一个值真正生效（`AppConfig.DATA_DIR` 在首次导入时
  定死），各测试模块 import 时各自 setdefault 的那份 mkdtemp 只有最先
  导入的那个有效。这里统一兜底，避免"哪个模块先被导入"决定数据落点。
- **空板恢复确认标记**：`AppController()` 启动时若"看板没有任何卡片、
  但存在历史快照"会弹模态询问框——无人可点的测试环境下这会直接挂死。
  全量跑时前序用例清空卡片是常态，凡是后续新建控制器的用例都会撞上，
  因此在测试进程里从一开始就置位"用户已确认保持空看板"。
- **模态询问框一律自动回「否」**：上面的标记会在"保存过含卡看板"时被
  清掉（那是正常业务逻辑），单靠置位挡不住整轮跑；凡是走到
  `QMessageBox.question` 的路径，在测试里都没有人能点，统一回答"否"
  （各用例要断言这两个分支时自行打桩覆盖）。
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PET_BOARD_DATA_DIR", tempfile.mkdtemp())

# 项目根进 sys.path：本文件早于测试模块执行，此时它们各自的 sys.path
# 插入尚未发生（pytest 的 rootdir 推断也可能给出别的位置）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QMessageBox   # noqa: E402

from app.config import AppConfig   # noqa: E402 — 必须在环境变量之后导入

AppConfig.set_empty_board_ack(True)


def _answer_no(*_args, **_kwargs):
    return QMessageBox.No


# staticmethod 包一层：QMessageBox.question 是静态槽，直接塞普通函数会被
# 当成未绑定方法再补一个 self
patch.object(QMessageBox, "question", staticmethod(_answer_no)).start()


_shared_controller = None


def shared_controller():
    """进程内共享的 AppController（惰性创建，全测试进程只有一个）

    构造一个 AppController 会连带建立托盘、无边框主窗口、桌宠、单实例
    监听与后台落盘线程池，而**所有实例共用同一份数据目录**（DATA_DIR 在
    首次导入 app.config 时就定死了）。用例各自 new 一个 = 多个写入者抢
    同一批文件 + 事件循环里越堆越多的顶层控件；实测各模块单跑都通过，
    全量连跑会在中途卡死（主线程停在 Qt 事件循环里、无人可点的嵌套模态）。
    统一收口成一个实例，顺带让"当前看板状态"在全量跑里保持连贯。
    """
    global _shared_controller
    if _shared_controller is None:
        from app.controllers.app_controller import AppController
        _shared_controller = AppController()
        # 启动是折叠态延迟构建：多数用例会直接断言列/卡控件
        _shared_controller._ensure_board_ui()
    return _shared_controller


def capture_notifications(controller) -> list:
    """接管控制器的通知出口，返回收集列表

    控制器是进程内共享的，直接给它挂 instance 属性会**跨模块残留**：
    后面的用例拿到的"通知"其实还被前面的 lambda 收着，断言全落空。
    配套 restore_notify() 清掉这层实例影子（见其注释）。
    """
    out: list = []
    controller._notify = out.append
    return out


def restore_notify(controller) -> None:
    """恢复控制器的真实 _notify

    打桩走的是 instance 属性，删掉它就回落到类方法——比"先存后写回"
    更稳（前者不怕中间又被别的用例覆盖）。重复调用安全。
    """
    try:
        del controller._notify
    except AttributeError:
        pass

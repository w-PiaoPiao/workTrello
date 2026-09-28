"""单实例唤醒：第二个实例经 Unix domain socket 通知首个实例亮出界面

背景：本应用常驻托盘，窗口整个隐藏是常态——用户再次双击应用图标时，
单实例锁会让新进程退出，只弹一个"已在运行"提示，观感与闪退无异。有了
唤醒通道，第二实例直接把已运行实例的窗口拉到前台，静默退出。

实现用 QSocketNotifier（read 事件在主线程触发）：监听 socket 交给事件
循环，accept/recv 全在主线程完成——无后台线程，也就没有跨线程投递的
时序坑。QtNetwork 在打包中被裁剪（tools/build.sh 的 --exclude-module），
QLocalServer 不可用，故用标准库 socket。

Windows 的官方 CPython 构建不提供 AF_UNIX（socket 模块压根没有该属性，
不是"旧系统才会缺"），故缺该属性时 start/wake 整体降级为空操作，单实例
锁回到 main.py 的"已在运行"提示框路径（v0.2.3 漏了这层降级，属性访问的
AttributeError 逃出 except OSError，Windows 上启动即崩）。
"""

from __future__ import annotations

import logging
import socket
from pathlib import Path

from PySide6.QtCore import QObject, QSocketNotifier, Signal

logger = logging.getLogger(__name__)

_SOCKET_NAME = "instance.sock"
_REQUEST_SHOW = b"show"

# 能力探测：Windows 官方 CPython 无 socket.AF_UNIX（见模块 docstring）。
# 模块级常量而非内联 hasattr，便于测试直接替身验证降级分支。
_AF_UNIX_SUPPORTED = hasattr(socket, "AF_UNIX")


class InstanceWaker(QObject):
    """锁持有者侧：监听唤醒请求；第二实例侧：wake() 发送请求"""

    signal_show_requested = Signal()

    def __init__(self, data_dir: Path, parent=None):
        super().__init__(parent)
        self._path = Path(data_dir) / _SOCKET_NAME
        self._server: socket.socket | None = None
        self._notifier: QSocketNotifier | None = None

    # ── 首实例：监听 ──────────────────────────────────────

    def start(self) -> None:
        """开始监听唤醒请求（事件循环驱动）；不支持的平台为空操作"""
        if not _AF_UNIX_SUPPORTED:
            logger.debug("本平台无 AF_UNIX，唤醒通道降级为空操作")
            return
        try:
            if self._path.exists():
                self._path.unlink()   # 清掉崩溃残留（新锁已拿到，旧 socket 必死）
            self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._server.bind(str(self._path))
            self._server.listen(16)   # backlog 足量：连续快速 double-click 不丢唤醒
            self._server.setblocking(False)
        except OSError as e:
            logger.warning("单实例唤醒通道不可用: %s", e)
            self._server = None
            return
        self._notifier = QSocketNotifier(
            self._server.fileno(), QSocketNotifier.Read, self)
        self._notifier.activated.connect(self._on_readable)

    def _on_readable(self) -> None:
        if self._server is None:
            return
        while True:
            try:
                conn, _addr = self._server.accept()
            except (BlockingIOError, InterruptedError):
                return    # 连接已取完
            except OSError:
                return
            self._handle(conn)

    def _handle(self, conn: socket.socket) -> None:
        """读取一条唤醒请求；client 先 send 后 close，数据在本机毫秒级
        到达，留 100ms 余量防 accept 与 send 的时序竞态（双击场景频率
        极低，阻塞主线程 100ms 无感）"""
        data = b""
        try:
            conn.settimeout(0.1)
            data = conn.recv(64)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass
        if data.strip() == _REQUEST_SHOW:
            self.signal_show_requested.emit()

    # ── 第二实例：请求已有实例亮出界面 ────────────────────

    def wake(self) -> bool:
        """请求已有实例亮出界面；不支持的平台直接失败，调用方退回提示框"""
        if not _AF_UNIX_SUPPORTED:
            return False
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.settimeout(0.5)
                sock.connect(str(self._path))
                sock.sendall(_REQUEST_SHOW + b"\n")
            finally:
                sock.close()
            return True
        except OSError:
            return False

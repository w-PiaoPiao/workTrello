"""单实例唤醒：第二个实例经 Unix domain socket 通知首个实例亮出界面

背景：本应用常驻托盘，窗口整个隐藏是常态——用户再次双击应用图标时，
单实例锁会让新进程退出，只弹一个"已在运行"提示，观感与闪退无异。有了
唤醒通道，第二实例直接把已运行实例的窗口拉到前台，静默退出。

实现用 QSocketNotifier（read 事件在主线程触发）：监听 socket 交给事件
循环，accept/recv 全在主线程完成——无后台线程，也就没有跨线程投递的
时序坑。QtNetwork 在打包中被裁剪（tools/build.sh 的 --exclude-module），
QLocalServer 不可用，故用标准库 socket。不支持 AF_UNIX 的平台（旧
Windows）优雅降级：start/wake 都不动，单实例锁退回"仅弹提示框"的原有
行为。
"""

from __future__ import annotations

import logging
import socket
from pathlib import Path

from PySide6.QtCore import QObject, QSocketNotifier, Signal

logger = logging.getLogger(__name__)

_SOCKET_NAME = "instance.sock"
_REQUEST_SHOW = b"show"


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

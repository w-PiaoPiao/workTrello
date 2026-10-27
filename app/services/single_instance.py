"""单实例唤醒：第二个实例经 Unix domain socket 通知首个实例亮出界面

背景：本应用常驻托盘，窗口整个隐藏是常态——用户再次双击应用图标时，
单实例锁会让新进程退出，只弹一个"已在运行"提示，观感与闪退无异。有了
唤醒通道，第二实例直接把已运行实例的窗口拉到前台，静默退出。

实现用 QSocketNotifier（read 事件在主线程触发）：监听 socket 交给事件
循环，accept/recv 全在主线程完成——无后台线程，也就没有跨线程投递的
时序坑。QtNetwork 在打包中被裁剪（tools/build.sh 的 --exclude-module），
QLocalServer 不可用，故用标准库 socket。

Windows 的官方 CPython 构建不提供 AF_UNIX（socket 模块压根没有该属性，
不是"旧系统才会缺"），该平台改用 127.0.0.1 回环 TCP：首实例 bind 随机
端口并把端口号写进 <DATA_DIR>/instance.port，第二实例读文件后连接。
此前 Windows 上整条通道是空操作，于是"已在运行"的提示没有任何出口——
托盘图标被 Windows 折叠进溢出区、或图标压根没注册成功（低完整性进程被
UIPI 拦掉）时，用户只能去任务管理器杀进程。（v0.2.3 曾漏了这层降级，
属性访问的 AttributeError 逃出 except OSError，Windows 上启动即崩。）
"""

from __future__ import annotations

import logging
import socket
import time
from pathlib import Path

from PySide6.QtCore import QObject, QSocketNotifier, Signal

logger = logging.getLogger(__name__)

_SOCKET_NAME = "instance.sock"
_PORT_FILE = "instance.port"
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
        self._port_path = Path(data_dir) / _PORT_FILE
        self._server: socket.socket | None = None
        self._port: int | None = None
        self._notifier: QSocketNotifier | None = None

    # ── 首实例：监听 ──────────────────────────────────────

    def start(self) -> None:
        """开始监听唤醒请求（事件循环驱动）；失败时降级为无通道"""
        if _AF_UNIX_SUPPORTED:
            self._start_unix()
        else:
            self._start_tcp()

    def _start_unix(self) -> None:
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
        self._attach_notifier()

    def _start_tcp(self) -> None:
        """Windows 回退：回环 TCP，端口由首实例写文件告知第二实例

        用随机端口（bind 0）而不是固定端口：固定端口会与其他程序撞车，
        撞了就永久失去唤醒能力。端口写在数据目录里，第二实例读同一份。
        """
        try:
            self._port_path.unlink(missing_ok=True)   # 清掉上一轮的端口
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.bind(("127.0.0.1", 0))
            server.listen(16)
            server.setblocking(False)
            self._port = server.getsockname()[1]
            self._server = server
            self._port_path.write_text(str(self._port), encoding="utf-8")
        except OSError as e:
            logger.warning("单实例唤醒通道不可用: %s", e)
            self._close_server()
            return
        self._attach_notifier()

    def _attach_notifier(self) -> None:
        self._notifier = QSocketNotifier(
            self._server.fileno(), QSocketNotifier.Read, self)
        self._notifier.activated.connect(self._on_readable)

    def _close_server(self) -> None:
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
        self._server = None

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
        """请求已有实例亮出界面；通道不可用时返回 False，调用方退回提示框"""
        if not _AF_UNIX_SUPPORTED:
            return self._wake_tcp()
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

    def _wake_tcp(self) -> bool:
        port = self._read_port()
        if not port:
            return False
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=0.5)
            try:
                sock.sendall(_REQUEST_SHOW + b"\n")
            finally:
                sock.close()
            return True
        except OSError:
            return False

    def _read_port(self) -> int | None:
        """读首实例写下的端口

        首实例可能刚拿到锁、监听还没起来（第二实例启动得更快），短暂
        重试几次：这里多等 300ms，换的是"双击图标真的能把界面叫出来"。
        """
        for _ in range(6):
            try:
                return int(self._port_path.read_text(
                    encoding="utf-8").strip())
            except (OSError, ValueError):
                time.sleep(0.05)
        return None

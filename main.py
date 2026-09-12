"""
桌宠看板 — 启动入口

用法：
    python main.py          # 正常启动
    python main.py --debug  # 调试模式（输出详细日志）
"""

import logging
import sys


def main():
    debug = "--debug" in sys.argv

    level = logging.DEBUG if debug else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # 打包版 console=False 看不到 stderr：Qt 事件处理器里抛出的异常
    # 会被 PySide6 吞掉，钩到日志里才能事后定位（如列折叠嵌合态问题）
    def _log_uncaught(tp, val, tb):
        logging.error("未处理异常", exc_info=(tp, val, tb))

    sys.excepthook = _log_uncaught

    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName("桌宠看板")
    app.setOrganizationName("Personal")
    app.setQuitOnLastWindowClosed(False)

    from app.services.app_icon import create_app_icon
    app.setWindowIcon(create_app_icon())

    app.setStyle("Fusion")

    # macOS：动态激活策略——交互时接管菜单栏（切到 regular），
    # 失活后回到 accessory（不占 Dock，桌宠/看板继续悬浮）
    if sys.platform == "darwin":
        from app.platform.mac_activation import enable_dynamic_activation
        enable_dynamic_activation(app)

    # ── 单实例锁 ──────────────────────────────────────────
    from pathlib import Path

    from PySide6.QtCore import QLockFile

    from app.config import AppConfig

    try:
        AppConfig.DATA_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logging.warning("创建数据目录失败: %s", e)

    _lock = QLockFile(str(AppConfig.DATA_DIR / "instance.lock"))
    _lock.setStaleLockTime(5000)
    if not _lock.tryLock(100):
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(None, AppConfig.APP_NAME, "应用已在运行，请查看系统托盘。")
        sys.exit(0)

    # ── 全局样式 ──────────────────────────────────────────
    from app.views.theme import AppTheme
    from app import i18n
    # 语言先于任何 UI 构造确定（QSettings 已按数据目录定位好）
    i18n.set_lang(AppConfig.get_language())
    AppTheme.apply()   # 调色板 + QSS + 原生外观一并应用

    # ── 启动控制器 ────────────────────────────────────────
    from app.controllers.app_controller import AppController

    # 显式挂在 app 上保活：控制器无 Qt 父级，仅靠栈帧局部变量引用
    # 在重构（如提前 return）时容易被回收
    app._controller = AppController()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

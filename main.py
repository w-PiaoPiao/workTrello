"""
桌宠看板 — 启动入口

用法：
    python main.py          # 正常启动
    python main.py --debug  # 调试模式（输出详细日志）
"""

import logging
import os
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

    from app.config import AppConfig
    from app import i18n
    app.setApplicationName(AppConfig.APP_NAME)
    app.setOrganizationName(AppConfig.APP_ORG)
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

    try:
        AppConfig.ensure_data_dir()
    except OSError as e:
        # 目录不可写时继续走下去，QLockFile 必然拿不到锁，最后被误报成
        # "已有实例在运行"，用户永远找不到真正原因
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(
            None, AppConfig.APP_NAME,
            i18n.tr("数据目录不可用：{path}\n{err}").format(
                path=AppConfig.DATA_DIR, err=e))
        sys.exit(1)

    # 文件日志：打包版 console=False 看不到 stderr，落盘才能事后定位
    # （放锁检查之前：第二实例/启动崩溃的场景也要能留下痕迹）
    log_path = AppConfig.DATA_DIR / "app.log"
    try:
        from logging.handlers import RotatingFileHandler
        _fh = RotatingFileHandler(log_path, maxBytes=1 << 20,
                                  backupCount=1, encoding="utf-8")
        _fh.setLevel(logging.DEBUG if debug else logging.WARNING)
        _fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        logging.getLogger().addHandler(_fh)
        if hasattr(os, "chmod") and sys.platform != "win32":
            try:
                os.chmod(log_path, 0o600)   # 日志含卡片文本与堆栈
            except OSError:
                pass
    except OSError as e:
        logging.warning("日志文件不可用: %s", e)

    # 语言先于任何 UI（含锁冲突提示框）确定（QSettings 已按数据目录定位好）
    i18n.set_lang(AppConfig.get_language())

    _lock = QLockFile(str(AppConfig.DATA_DIR / "instance.lock"))
    _lock.setStaleLockTime(5000)
    if not _lock.tryLock(100):
        # 已有实例在跑：优先唤醒它把界面亮出来（常驻托盘时窗口整个隐藏，
        # 只弹"已在运行"的提示观感与闪退无异）；唤醒通道不可用再弹提示框
        from app.services.single_instance import InstanceWaker

        if InstanceWaker(AppConfig.DATA_DIR).wake():
            sys.exit(0)
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(None, AppConfig.APP_NAME,
                            i18n.tr("应用已在运行，请查看系统托盘。"))
        sys.exit(0)

    # ── 全局样式 ──────────────────────────────────────────
    from app.views.theme import AppTheme
    AppTheme.apply()   # 调色板 + QSS + 原生外观一并应用

    # ── 启动控制器 ────────────────────────────────────────
    from app.controllers.app_controller import AppController

    # 显式挂在 app 上保活：控制器无 Qt 父级，仅靠栈帧局部变量引用
    # 在重构（如提前 return）时容易被回收
    try:
        app._controller = AppController()
    except Exception as e:   # noqa: BLE001 — 打包版（console=False）崩溃必须可见
        logging.exception("控制器初始化失败")
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(
            None, AppConfig.APP_NAME,
            i18n.tr("应用启动失败：{err}").format(
                err=f"{type(e).__name__}: {e}")
            + "\n\n" + i18n.tr("详情见日志文件：{path}").format(path=log_path))
        sys.exit(1)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

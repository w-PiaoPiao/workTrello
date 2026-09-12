"""
应用控制器：连接数据模型、托盘服务与 UI 视图
"""

from __future__ import annotations

import csv
import json
import logging
import time
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QDesktopServices,
    QGuiApplication,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QDialog,
    QLineEdit,
    QMenuBar,
    QMessageBox,
    QPlainTextEdit,
)

from app import i18n
from app.config import AppConfig
from app.i18n import tr
from app.models import json_io
from app.models.board import Board, BoardList, BoardStore, Card
from app.models.quick_syntax import parse_quick_input
from app.services.tray_service import TrayService
from app.views import motion
from app.views.board_view import BoardView
from app.views.main_window import MainWindow
from app.views.pet_view import PetView
from app.views.theme import AppTheme

logger = logging.getLogger(__name__)


class _FlushNotifier(QObject):
    """后台落盘失败 → 主线程通知桥（跨线程信号自动 queued 投递）"""

    save_failed = Signal(str)


class AppController(QObject):
    """应用控制器"""

    def __init__(self):
        super().__init__()

        app = QApplication.instance()

        # ── 数据层 ────────────────────────────────────────
        self._store = BoardStore(AppConfig.board_path())

        # ── UI 层 ─────────────────────────────────────────
        self._window = MainWindow()
        self._pet_view = PetView()
        self._board_view = BoardView()
        # 折叠态启动不构建看板控件（默认形态是桌宠，看板不可见），首次
        # 展开时才全量构建列/卡控件——几百卡时省掉启动首帧前的最大一块
        self._board_ui_built = False

        self._window.set_views(self._pet_view, self._board_view)
        self._window.set_visibility_callback(self._on_window_visibility)

        # ── 系统服务 ──────────────────────────────────────
        self._tray = TrayService(self._window)
        self._tray.signal_show_requested.connect(self._on_tray_show)
        self._tray.signal_hide_requested.connect(self._on_tray_hide)
        self._tray.signal_quit_requested.connect(self._on_quit)

        # ── 落盘防抖 ──────────────────────────────────────
        self._save_timer = QTimer()
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(AppConfig.SAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self._flush_store)
        # 后台落盘：主线程只做快照，文件 IO 串行跑在单线程池上；
        # 退出时由 _on_about_to_quit 同步兜底
        self._save_pool = QThreadPool(self)
        self._save_pool.setMaxThreadCount(1)
        self._flush_notifier = _FlushNotifier()
        self._flush_notifier.save_failed.connect(self._on_save_failed)
        self._last_save_error_notify: float | None = None   # 失败气泡去重
        if app is not None:
            app.aboutToQuit.connect(self._on_about_to_quit)

        # ── 撤销 / 截止提醒 / 系统主题跟随 / 番茄钟 ────────
        self._undo_stack: list[dict] = []
        self._today_popover: TodayPopover | None = None   # 今日清单浮窗（惰性创建）
        self._due_timer = QTimer(self)
        self._due_timer.setInterval(AppConfig.DUE_CHECK_INTERVAL_MS)
        self._due_timer.timeout.connect(self._check_due_dates)
        self._due_timer.start()
        # 启动后立即检查一次(等窗口就绪,避免提醒弹在启动瞬间)；
        # 之后由 DUE_CHECK_INTERVAL_MS 周期驱动
        QTimer.singleShot(2000, self._check_due_dates)
        self._pomo_card_id: str | None = None
        self._pomo_shown_minute: int | None = None   # 托盘 tooltip 已显示的分钟位
        self._pomo_left = 0
        self._pomo_timer = QTimer(self)
        self._pomo_timer.setInterval(1000)
        self._pomo_timer.timeout.connect(self._pomo_tick)
        self._archive_dialog: ArchiveDialog | None = None
        self._settings_dialog: SettingsDialog | None = None   # 惰性创建
        self._archive_key: tuple | None = None   # 归档内容指纹（按需重建用）
        if app is not None:
            QGuiApplication.styleHints().colorSchemeChanged.connect(
                self._on_system_scheme_changed)

        # ── 加载数据（含损坏恢复询问） ─────────────────────
        # 启动检查只走上方 2s 延迟那一次（等窗口就绪，避免提醒弹在
        # 启动瞬间）；此处不再立即调用，否则重复全板扫描且提醒抢跑
        self._load_data()

        # ── 连接信号 ──────────────────────────────────────
        self._connect_signals()

        # ── 主题 / 动画偏好恢复 ───────────────────────────
        AppTheme.set_mode(AppConfig.get_theme_mode())
        if not AppConfig.get_animation_enabled():
            self._on_pet_animation_toggled(False)

        # ── 窗口置顶偏好（show 之前应用，避免闪烁）────────
        on_top = AppConfig.get_always_on_top()
        self._window.set_always_on_top(on_top)
        self._pet_view.set_always_top_checked(on_top)
        self._tray.set_always_top_checked(on_top)

        # ── 菜单栏（macOS：应用激活接管菜单栏时可见）───────
        if AppConfig.IS_MACOS:
            self._build_menu_bar()

        # ── 显示 ──────────────────────────────────────────
        self._window.show()
        self._window.start_collapsed_idle()

    # ── 公共访问 ──────────────────────────────────────────

    def window(self) -> MainWindow:
        """返回主窗口实例"""
        return self._window

    # ── 数据 ──────────────────────────────────────────────

    def _load_data(self) -> None:
        board = self._store.load()
        if self._store.problems:
            kinds = [k for k, _ in self._store.problems]
            self._store.problems.clear()
            if self._offer_restore(kinds):
                return  # 已从备份恢复并刷新视图
            # 用户放弃恢复：好数据仍躺在 .prev 里，先固化一份带时间戳的副本，
            # 避免随后的默认看板首次落盘把 .prev 轮转覆盖掉
            self._preserve_good_copy()
            detail_txt = tr("；").join(
                tr("已损坏并隔离") if k == "corrupted"
                else tr("暂时无法读取") for k in kinds)
            self._show_error(
                tr("看板数据文件异常（{detail}），本次以默认看板启动。").format(
                    detail=detail_txt))
            self._apply_board_to_ui(board)
            # 异常路径必须把默认看板落盘，覆盖掉损坏或不可读的旧文件
            self._store.mark_dirty()
            self._schedule_save()
            return

        self._apply_board_to_ui(board)
        # 仅首次启动（数据文件尚不存在、生成默认看板）主动落盘；
        # 正常加载不重写磁盘，避免每次启动轮转 .prev、刷新 saved_at
        if not self._store.path.exists():
            self._store.mark_dirty()
            self._schedule_save()
        else:
            # 每次正常启动为上一份数据留档：即使随后被异常覆盖（如旧版
            # exe 非原子写空板），快照链仍可恢复；空板不产生快照。
            # has_cards 用已加载的 board 结果，免 snapshot 内部二次全量解析
            json_io.snapshot_board(
                self._store.path,
                has_cards=any(lst.cards for lst in board.lists))
        # 空板恢复引导（看板为空但有历史快照时询问，记住用户选择）
        self._maybe_offer_empty_restore()

    def _maybe_offer_empty_restore(self) -> None:
        """看板为空但存在历史数据时询问是否恢复（"否"则记住选择）

        防"空板吞数据"：任何把默认空板写盘的事故都不会触发损坏恢复，
        这里在启动时用快照链兜底。用户明确保持空看板后不再追问，
        直到再次录入过数据（保存含卡内容会清除确认标记）。
        """
        if AppConfig.get_empty_board_ack():
            return
        board = self._store.load()
        if any(lst.cards for lst in board.lists):
            return                       # 看板非空，无需引导
        candidates: list[Path] = []
        prev = json_io.good_prev_copy(self._store.path)
        if prev is not None and json_io.doc_has_cards(prev):
            candidates.append(prev)      # .prev 是最新一次原子写的旧内容
        candidates.extend(json_io.nonempty_snapshots(self._store.path))
        if not candidates:
            return
        latest = candidates[0]
        reply = QMessageBox.question(
            self._window,
            tr("看板数据为空"),
            tr("看板当前没有任何卡片，但检测到 {n} 份历史数据副本"
               "（最近一份：{name}）。\n是否恢复最近一份数据？\n"
               "选择「否」将保持空看板，且下次不再询问（直到再次录入过数据）。").format(
                n=len(candidates), name=latest.name),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            AppConfig.set_empty_board_ack(True)
            return
        if not json_io.restore_from_backup(self._store.path, latest):
            AppConfig.set_empty_board_ack(True)
            self._show_error(
            tr("快照 {name} 无法解析，恢复失败。").format(name=latest.name))
            return
        board = self._store.reload()
        self._apply_board_to_ui(board)
        self._store.mark_dirty()
        self._schedule_save()
        self._tray.show_notification(tr("已从快照恢复看板数据"))

    def _preserve_good_copy(self) -> None:
        """把最近好副本（.prev）复制为带时间戳的保留文件，防止被后续落盘轮转覆盖"""
        import shutil

        prev = json_io.good_prev_copy(self._store.path)
        if prev is None:
            return
        keep = prev.with_name(prev.name + json_io.backup_ext("good"))
        try:
            shutil.copy2(prev, keep)
        except OSError as e:
            logger.warning("保留好副本失败: %s (%s)", keep, e)
            return
        logger.info("已保留最近好数据副本: %s", keep)
        # 此前只创建不清理：每次"数据异常且放弃恢复"都会新增一份且永不
        # 删除，磁盘随事故次数无限累积（corrupt/snap 都有轮转，唯独它没有）
        json_io.rotate_backups(self._store.path, "good",
                               json_io.GOOD_BACKUP_KEEP)

    def _offer_restore(self, kinds: list[str]) -> bool:
        """数据文件异常时询问是否从最近好副本（.prev）恢复；成功恢复返回 True"""
        bak = json_io.good_prev_copy(self._store.path)
        if bak is None:
            return False
        corrupted = "corrupted" in kinds
        detail = tr("已损坏并隔离") if corrupted else tr("暂时无法读取")
        origin_note = (tr("原文件已自动隔离备份。\n") if corrupted
                       else tr("原文件仍保留在原位置。\n"))
        reply = QMessageBox.question(
            self._window,
            tr("看板数据异常"),
            tr("看板数据文件{detail}，{note}检测到最近一次成功保存的副本"
               "（{name}），是否用它恢复数据？\n选择「否」则以默认看板启动。").format(
                detail=detail, note=origin_note, name=bak.name),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return False
        if not json_io.restore_from_backup(self._store.path, bak):
            self._preserve_good_copy()
            self._show_error(
                tr("备份文件 {name} 无法解析，恢复失败，本次以默认看板启动。").format(
                    name=bak.name))
            return False
        # 重新读盘，取回刚写入的恢复数据
        board = self._store.reload()
        self._apply_board_to_ui(board)
        self._store.mark_dirty()
        self._schedule_save()
        self._tray.show_notification(tr("已从备份恢复看板数据"))
        return True

    def _apply_board_to_ui(self, board) -> None:
        stats = board.today_stats(date.today())
        if self._board_ui_built:
            self._board_view.refresh(board.lists, stats=stats)
        self._refresh_pet_state(stats)

    def _ensure_board_ui(self) -> None:
        """首次展开前构建看板控件（折叠态启动跳过了全量构建）"""
        if self._board_ui_built:
            return
        self._board_ui_built = True
        board = self._store.load()
        stats = board.today_stats(date.today())
        self._board_view.refresh(board.lists, stats=stats)

    def _schedule_save(self) -> None:
        self._save_timer.start()

    def _flush_store(self) -> None:
        """防抖到点的常规保存：序列化在主线程，文件 IO 走后台单线程池"""
        board = self._store.load()
        had_cards = any(lst.cards for lst in board.lists)
        self._store.flush_async(
            self._save_pool,
            on_error=self._flush_notifier.save_failed.emit)
        if had_cards and AppConfig.get_empty_board_ack():
            # 标记绝大多数时间缺席：先查再删，省掉每次保存的一次 QSettings remove
            AppConfig.clear_empty_board_ack()

    def _on_about_to_quit(self) -> None:
        """退出兜底：等在途后台写完成，再把剩余脏数据同步落盘（含 fsync）"""
        self._save_pool.waitForDone(3000)
        self._store.flush()

    def _on_save_failed(self, detail: str) -> None:
        """后台落盘失败（worker 线程经信号 queued 回主线程）"""
        logger.error("保存数据失败: %s", detail)
        self._schedule_save()    # 静默重试
        now = time.monotonic()
        last = self._last_save_error_notify
        if last is not None and now - last < 30:
            return               # 气泡 30s 去重：磁盘满时防止 500ms 轰炸
        self._last_save_error_notify = now
        # 落盘失败用户可见，避免静默丢写
        self._tray.show_notification(
            tr("看板数据保存失败，请检查磁盘空间或文件权限"))

    # ── 信号 ──────────────────────────────────────────────

    def _connect_signals(self) -> None:
        # 首次展开：先同步构建看板控件（折叠态启动时跳过了全量构建）
        self._window.signal_about_to_expand.connect(self._ensure_board_ui)
        # 桌宠 → 展开
        self._pet_view.signal_expand_clicked.connect(self._window.expand)
        self._pet_view.signal_quick_add_clicked.connect(self._on_quick_add)
        self._pet_view.signal_quit_requested.connect(self._on_quit)
        self._pet_view.signal_animation_toggled.connect(
            self._on_pet_animation_toggled)
        self._pet_view.signal_skin_selected.connect(self._on_pet_skin_selected)
        self._pet_view.signal_always_top_toggled.connect(
            self._on_always_top_toggled)
        self._board_view.signal_card_pomo.connect(self._on_card_pomo)
        self._board_view.signal_card_archive.connect(self._on_card_archive)
        self._board_view.signal_card_star.connect(self._on_card_star)
        self._board_view.signal_card_workdir_open.connect(
            self._on_card_workdir_open)
        self._board_view.signal_card_workdir_set.connect(
            self._on_card_workdir_set)
        self._board_view.signal_archive_open.connect(self._on_archive_open)
        self._board_view.signal_export.connect(self._on_export)
        self._board_view.signal_export_backup.connect(self._on_export_backup)
        self._board_view.signal_import_backup.connect(self._on_import_backup)
        self._board_view.signal_list_collapsed.connect(self._on_list_collapsed)
        self._board_view.signal_today_toggled.connect(
            self._on_today_mode_changed)
        self._pet_view.signal_today_list_clicked.connect(
            self._on_today_list_open)
        self._pet_view.signal_settings_clicked.connect(self._on_settings_open)
        self._tray.signal_settings_requested.connect(self._on_settings_open)
        self._board_view.signal_settings_clicked.connect(self._on_settings_open)

        # 看板 → 折叠 / 主题
        self._board_view.signal_collapse_clicked.connect(self._window.collapse)
        self._board_view.signal_theme_selected.connect(self._on_theme_selected)
        self._board_view.signal_quit_requested.connect(self._on_quit)
        self._board_view.signal_zoom_requested.connect(self._window.toggle_zoom)
        # Windows 窗口控制键的最大化图标 ⇆ 跟随 toggle_zoom（含工具栏绿键/双击触发）
        self._window.zoom_state_changed.connect(
            self._board_view.set_zoom_state)

        # 托盘
        self._tray.signal_always_top_toggled.connect(
            self._on_always_top_toggled)
        self._tray.signal_undo_requested.connect(
            lambda: self._on_undo_requested(True))
        # Windows/Linux 键盘入口（macOS 由全局菜单栏 QAction 承担同键）
        if not AppConfig.IS_MACOS:
            self._window.undo_shortcut.connect(
                lambda: self._on_undo_requested(True))
            self._window.new_card_shortcut.connect(self._on_quick_add)

        # 看板数据操作
        self._board_view.signal_card_add.connect(self._on_card_add)
        self._board_view.signal_card_edit.connect(self._on_card_edit)
        self._board_view.signal_card_done.connect(self._on_card_done)
        self._board_view.signal_card_delete.connect(self._on_card_delete)
        self._board_view.signal_card_move.connect(self._on_card_move)
        self._board_view.signal_list_add.connect(self._on_list_add)
        self._board_view.signal_list_title_changed.connect(
            self._on_list_title_changed)
        self._board_view.signal_list_delete.connect(self._on_list_delete)
        self._board_view.signal_list_move.connect(self._on_list_move)

    # ── 卡片操作 ──────────────────────────────────────────

    def _on_quick_add(self) -> None:
        """桌宠右键快速添加：单行对话框（带速记实时预览），加到第一个列表"""
        from app.views.quick_add_dialog import QuickAddDialog

        board = self._store.load()
        if not board.lists:
            self._notify(tr("看板还没有列表，先添加一个列表"))
            return
        dlg = QuickAddDialog(
            tr("快速添加卡片"),
            tr("卡片标题（支持速记：明天 / 周五 / !P1 / #红）"),
            tr("例：明天 交周报 !P1 #红"),
            syntax_preview=True,
            parent=self._window)
        if dlg.exec() != QDialog.Accepted:
            return
        title = dlg.text().strip()
        if title:
            self._on_card_add(board.lists[0].id, title)

    def _on_bulk_add(self) -> None:
        """批量添加：每行一张卡（支持速记语法），适合迁移清单/会议行动项

        打开时剪贴板为多行文本则自动预填——"复制若干行 → 一键入库"
        是最常见的批量来源。撤销一次回滚整批。列表选择内嵌在对话框里
        （此前是输入完再弹 QInputDialog 选列表两步走）。
        """
        from app.views.quick_add_dialog import BulkAddDialog
        from PySide6.QtWidgets import QApplication

        board = self._store.load()
        names = [l.title for l in board.lists]
        if not names:
            self._notify(tr("看板还没有列表，先添加一个列表"))
            return
        clipboard = QApplication.clipboard()
        clip = clipboard.text() if clipboard is not None else ""
        prefill = clip if "\n" in clip else ""
        dlg = BulkAddDialog(names, prefill, parent=self._window)
        if dlg.exec() != QDialog.Accepted:
            return
        lines = [ln.strip() for ln in dlg.text().splitlines()
                 if ln.strip()][:100]
        if not lines:
            return
        target = board.lists[dlg.list_index()]
        self._push_undo()
        new_cards = [self._make_card_from_text(ln) for ln in lines]
        target.cards[0:0] = new_cards   # 保序插入列首（第一行在最上）
        board.invalidate_index()
        self._after_data_change(
            tr("已批量添加 {n} 张卡片").format(n=len(new_cards)))

    def _today_focus_items(self, focus=None) -> list[tuple[BoardList, Card]]:
        """今日聚焦卡片（含所属列表），供今日清单浮窗显示

        传入 today_stats()["focus"] 时直接复用单趟统计结果，免再扫一遍。
        """
        if focus is not None:
            return list(focus)
        board = self._store.load()
        today = date.today()
        return [(lst, c) for lst in board.lists for c in lst.cards
                if c.in_today_focus(today)]

    def _on_today_list_open(self) -> None:
        """桌宠右键"今日清单"：浮窗概览今日待办（勾选/打开编辑直通控制器）"""
        if self._today_popover is None:
            from app.views.today_popover import TodayPopover

            self._today_popover = TodayPopover()
            self._today_popover.signal_card_done.connect(self._on_card_done)
            self._today_popover.signal_card_edit.connect(self._on_card_edit)
            self._today_popover.signal_card_star.connect(self._on_card_star)
        board = self._store.load()
        stats = board.today_stats(date.today())
        self._today_popover.set_items(
            self._today_focus_items(stats["focus"]),
            done_count=stats["done_today"])
        self._today_popover.show_below(self._window.frameGeometry())

    def _make_card_from_text(self, text: str) -> Card:
        """速记语法建卡：行内可写截止日（明天/周五/9/20）、优先级（!P1）、标签（#红）

        解析后标题为空（整行全是语法 token）时回退原文，不吞用户输入。
        """
        title, fields = parse_quick_input(text)
        return Card(title=title or text.strip(), **fields)

    def _on_card_add(self, list_id: str, title: str = "") -> None:
        from app.views.card_dialog import CardDialog

        lst = self._store.load().find_list(list_id)
        if lst is None:
            return
        if not title:
            dialog = CardDialog(None, self._window)
            # exec 返回后对话框即弃用：关闭时销毁，避免 widget 树在
            # 主窗口下以隐藏状态无限累积（QDialog.exec 已 close）
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            if dialog.exec() != CardDialog.Accepted:
                return
            self._push_undo()
            card = Card(**dialog.result_card())
        else:
            self._push_undo()
            card = self._make_card_from_text(title)
        lst.cards.insert(0, card)
        board = self._store.load()
        board.invalidate_index()   # 直接改 cards 结构，索引在变更点立即失效
        self._after_data_change(tr("已添加卡片"))

    def _on_card_edit(self, list_id: str, card_id: str) -> None:
        from app.views.card_dialog import CardDialog

        _lst, card = self._store.load().find_card(card_id)
        if card is None:
            return
        dialog = CardDialog(card, self._window)
        dialog.setAttribute(Qt.WA_DeleteOnClose)   # 同 _on_card_add：用完即销毁
        if dialog.exec() != CardDialog.Accepted:
            return
        self._push_undo()
        card.apply(dialog.result_card())
        self._after_data_change(tr("已保存"))

    def _on_card_done(self, list_id: str, card_id: str, done: bool) -> None:
        _lst, card = self._store.load().find_card(card_id)
        if card is None:
            return
        self._push_undo()
        card.set_done(done)
        if done and card.roll_repeat():
            # 重复任务：完成即滚动到下一周期并复位，提示下次日期
            next_day = (card.due_date or "")[5:].replace("-", "/")
            self._after_data_change(
                tr("已完成 · 下次 {date}").format(date=next_day))
            return
        stats = self._after_data_change(None)
        if done:
            self._notify_done(card, stats)

    def _notify_done(self, card: Card, stats: dict) -> None:
        """里程碑反馈：看板展开时走窗口内 toast，与 _notify 分流一致

        stats 复用 _after_data_change 单趟算好的统计，免再 3 趟全板扫描
        （today_done_count/total_cards/done_cards 与字段逐一同口径）。
        """
        today_n = stats["done_today"]
        total = stats["total"]
        done = stats["done"]
        if total > 0 and done == total:
            self._notify(tr("全部完成！桌宠为你鼓掌 🎉"))
            if self._window.mode == "collapsed":
                self._pet_view.celebrate()
        elif today_n == 3:
            self._notify(tr("今日已完成 3 张，节奏不错！🌱"))
        elif today_n == 5:
            self._notify(tr("今日已完成 5 张，收工级表现！🏆"))
        elif done > 0 and done % 5 == 0:
            self._notify(tr("已完成 {n} 张卡片，继续加油！").format(n=done))

    def _on_card_delete(self, list_id: str, card_id: str) -> None:
        """删除卡片：不打断流，撤销兜底（toast 提示 ⌘Z）

        撤销栈（20 步）完全覆盖单卡删除，模态确认框反而是唯一打断
        录入节奏的弹窗——与归档（更"重"却不确认）不一致，也与 README
        声称的轻提示行为不符。误删一次 ⌘Z 即回。
        """
        board = self._store.load()
        _lst, card = board.find_card(card_id)
        if card is None:
            return
        self._push_undo()
        if card_id == self._pomo_card_id:
            self._pomo_stop()          # 删除正专注的卡片时先结束番茄钟
        if self._store.load().remove_card(card_id) is None:
            return
        self._after_data_change(tr("已删除 · {hint}").format(hint=self._undo_hint()))

    def _on_card_move(self, card_id: str, target_list_id: str, index: int) -> None:
        """拖拽移动卡片（跨列表 / 列表内重排）

        index 按拖拽时的原始控件顺序计算（"插到第 i 张之前"）；
        同列表向下移动时先移除卡片会使后续元素前移，插入点需左移一位。
        """
        board = self._store.load()
        src_list, moved = board.find_card(card_id)
        if moved is None:
            return
        self._push_undo()
        src_index = src_list.cards.index(moved)
        # 原地摘除（复用 find_card 结果）：原 board.remove_card 会再全板
        # 扫一遍，连同上面的 index() 一次移动共扫三遍
        src_list.cards.pop(src_index)
        board.invalidate_index()
        target = board.find_list(target_list_id)
        if target is None:
            target = board.lists[0]
            target.cards.append(moved)
        else:
            if target is src_list and src_index < index:
                index -= 1
            index = max(0, min(index, len(target.cards)))
            target.cards.insert(index, moved)
        self._after_data_change(None)

    # ── 列表操作 ──────────────────────────────────────────

    def _on_list_add(self) -> None:
        dlg = QuickAddDialog(tr("添加列表"), tr("列表名称："),
                             tr("例如：进行中"), parent=self._window)
        if dlg.exec() != QDialog.Accepted:
            return
        title = dlg.text().strip()
        if not title:
            return
        self._push_undo()
        lst = BoardList(title=title)
        self._store.load().lists.append(lst)
        self._after_data_change(tr("已添加列表"))
        # 新列 append 在最右端：不滚过去用户会以为点击没生效
        self._board_view.reveal_list(lst.id)

    def _on_list_title_changed(self, list_id: str, new_title: str) -> None:
        lst = self._store.load().find_list(list_id)
        if lst is None:
            return
        self._push_undo()
        lst.title = new_title
        self._after_data_change(None)

    def _on_list_delete(self, list_id: str) -> None:
        board = self._store.load()
        lst = board.find_list(list_id)
        if lst is None:
            return
        n = len(lst.cards)
        if n > 0:
            reply = QMessageBox.question(
                self._window, tr("确认删除"),
                tr("列表「{title}」还有 {n} 张卡片，删除后不可恢复。\n继续？").format(
                    title=lst.title, n=n),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
        # 空列表无卡片可丢，直接删除（撤销栈可恢复）
        self._push_undo()
        board.remove_list(list_id)
        self._after_data_change(
            tr("已删除列表 · {hint}").format(hint=self._undo_hint()))

    def _on_list_move(self, moved_id: str, target_id: str,
                      insert_before: bool) -> None:
        """整列拖拽重排：把 moved 列插到 target 列前/后

        拖回原相邻位置视为无变化（不产生撤销快照、不刷新）。
        """
        board = self._store.load()
        moved = board.find_list(moved_id)
        target = board.find_list(target_id)
        if moved is None or target is None or moved_id == target_id:
            return
        current = [lst.id for lst in board.lists]
        rest = [lst.id for lst in board.lists if lst.id != moved_id]
        new_order = list(rest)
        t = rest.index(target_id)
        if insert_before:
            new_order.insert(t, moved_id)
        else:
            new_order.insert(t + 1, moved_id)
        if new_order == current:
            return                        # 位置未变（拖回原位）→ 短路
        self._push_undo()
        by_id = {lst.id: lst for lst in board.lists}
        board.lists[:] = [by_id[lid] for lid in new_order]
        self._after_data_change(None)

    def _on_list_collapsed(self, list_id: str, collapsed: bool) -> None:
        """列折叠状态持久化（会话之间保留）"""
        ids = AppConfig.get_collapsed_lists()
        if collapsed:
            ids.add(list_id)
        else:
            ids.discard(list_id)
        AppConfig.save_collapsed_lists(ids)

    # ── 数据变更后的统一刷新 ──────────────────────────────

    def _after_data_change(self, notify: str | None) -> dict:
        """数据变更后的统一刷新；返回 today_stats() 结果供调用方复用"""
        board = self._store.load()
        # 任何模型改动（增删改卡/列、拖拽、归档、番茄计数…）都必须在此标脏：
        # flush() 只在脏标记为真时落盘，只 start() 防抖计时器而不标脏，
        # 计时器到点后会因不脏而直接返回，编辑将停留在内存、退出即丢
        self._store.mark_dirty()
        # 统一失效卡片索引：本类多处直接 lst.cards.insert/pop 改结构，
        # 不走 Board 方法，在此收口最可靠
        board.invalidate_index()
        # 单趟统计一次算全 total/done/今日聚焦/逾期，喂给看板视图与
        # 桌宠状态，取代原先 4+ 趟全板扫描
        stats = board.today_stats(date.today())
        # 看板控件尚未构建（折叠态启动后未展开过）时跳过视图刷新：
        # 首次展开的 _ensure_board_ui 会用最新数据构建，无一致性缺口
        if self._board_ui_built:
            self._board_view.refresh(board.lists, stats=stats)
        self._refresh_pet_state(stats)
        self._refresh_archive()
        self._schedule_save()
        if notify:
            self._notify(notify)
        # 今日清单浮窗可见时按模型全量重建（编辑保存/撤销/勾选后的行
        # 与徽章保持同步；今日谓词天然排除刚完成的卡）
        if (self._today_popover is not None
                and self._today_popover.isVisible()):
            self._today_popover.set_items(
                self._today_focus_items(stats["focus"]),
                done_count=stats["done_today"])
        return stats

    def _notify(self, text: str) -> None:
        """操作反馈：看板展开态走窗口内 toast，折叠/隐藏态走托盘气泡"""
        if self._window.mode == "expanded":
            self._board_view.show_toast(text)
        else:
            self._tray.show_notification(text)

    def _undo_hint(self) -> str:
        return tr("⌘Z 撤销") if AppConfig.IS_MACOS else tr("Ctrl+Z 撤销")

    # ── 桌宠状态联动 ──────────────────────────────────────

    def _refresh_pet_state(self, stats: dict | None = None) -> None:
        """角标=今日聚焦量（专注时显示倒计时）；表情：逾期难过 / 今日截止紧张 / 其余开心

        stats 传入 today_stats() 结果时免重扫（数据变更管线已算过）。
        """
        board = self._store.load()
        if stats is None:
            stats = board.today_stats(date.today())
        if self._pomo_card_id is None:
            self._pet_view.update_count(stats["focus_count"])
        if stats["overdue"]:
            mood = "sad"
        elif stats["due_today"]:
            mood = "worried"
        else:
            mood = "happy"
        self._pet_view.set_mood(mood)

    # ── 设置界面 / 语言 ───────────────────────────────────

    def _on_settings_open(self) -> None:
        """打开设置（三处入口共用）：惰性创建并同步当前偏好"""
        from app.views.settings_dialog import SettingsDialog

        if self._settings_dialog is None:
            dlg = SettingsDialog(self._window)
            dlg.signal_theme_selected.connect(self._on_theme_pref_selected)
            dlg.signal_language_selected.connect(self._on_language_changed)
            dlg.signal_skin_selected.connect(self._on_settings_skin)
            dlg.signal_animation_toggled.connect(
                self._on_pet_animation_toggled)
            dlg.signal_always_top_toggled.connect(
                self._on_always_top_toggled)
            i18n.register(dlg.retexts)          # 语言切换整页刷新
            AppTheme.register(dlg.reapply_theme)
            self._settings_dialog = dlg
        self._settings_dialog.sync_from_prefs(
            theme_mode=AppConfig.get_theme_mode(),
            lang=i18n.lang(),
            skin=AppConfig.get_pet_skin(),
            animation=AppConfig.get_animation_enabled(),
            always_top=self._window.is_always_on_top())
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def _on_theme_pref_selected(self, mode: str) -> None:
        """设置里的三态主题：原始模式（含 system）落盘，供跟随系统切换"""
        AppConfig.save_theme_mode(mode)
        AppTheme.set_mode(mode)
        act = getattr(self, "_menu_act_dark", None)
        if act is not None and act.isChecked() != (AppTheme.mode() == "dark"):
            act.blockSignals(True)
            act.setChecked(AppTheme.mode() == "dark")
            act.blockSignals(False)

    def _on_settings_skin(self, key: str) -> None:
        """设置换肤：与桌宠右键同一落点（绘制 + 持久化）"""
        self._pet_view._pet_canvas.set_skin(key)
        self._on_pet_skin_selected(key)

    def _on_language_changed(self, lang: str) -> None:
        """切换语言：广播（设置页即时刷新）+ 全应用静态/动态文案刷新"""
        i18n.set_lang(lang)               # 已注册回调先跑（设置页自身）
        AppConfig.save_language(lang)
        self._board_view.reapply_texts()  # 静态文案 + 清卡片指纹
        self._after_data_change(None)     # 全板 rebuild 徽章/tooltip + 浮窗
        self._pet_view.reapply_texts()
        self._tray.reapply_texts()
        if AppConfig.IS_MACOS:
            self._reapply_menu_texts()
        self._window.reapply_texts()
        from app.views.notes_popover import retexts_if_created
        retexts_if_created()
        if self._archive_dialog is not None:
            self._archive_dialog.retexts()

    # ── 主题 / 动画 ───────────────────────────────────────

    def _on_theme_selected(self, mode: str) -> None:
        AppTheme.set_mode(mode)
        AppConfig.save_theme_mode(AppTheme.mode())

    def _on_pet_animation_toggled(self, enabled: bool) -> None:
        """"暂停动画"总开关：同时管住桌宠待机与全部过渡动效

        Qt 6.10 的 QStyleHints 没有任何 motion/reduce 成员（已核实），
        无法自动跟随系统辅助功能设置，应用内开关是唯一调节手段，
        因此必须覆盖全部动画而不只是桌宠那几段。
        """
        AppConfig.save_animation_enabled(enabled)
        motion.set_enabled(enabled)
        self._pet_view.set_animation_enabled(enabled)
        if enabled:
            self._window.start_collapsed_idle()

    def _on_pet_skin_selected(self, key: str) -> None:
        AppConfig.save_pet_skin(key)

    def _on_always_top_toggled(self, on: bool) -> None:
        self._window.set_always_on_top(on)
        AppConfig.save_always_on_top(on)
        # 三处入口（桌宠右键 / 托盘菜单 / 菜单栏）勾选态保持一致
        self._pet_view.set_always_top_checked(on)
        self._tray.set_always_top_checked(on)
        act = getattr(self, "_menu_act_always_top", None)
        if act is not None and act.isChecked() != on:
            act.blockSignals(True)
            act.setChecked(on)
            act.blockSignals(False)

    # ── 菜单栏（macOS）────────────────────────────────────

    def _build_menu_bar(self) -> None:
        """构建全局菜单栏：应用激活（regular）时接管顶部菜单栏后可见"""
        menu_bar = QMenuBar(None)          # 无父级 = 全局默认菜单栏
        self._menu_bar = menu_bar
        # (setter, 中文原文)：语言切换时整栏重设（_reapply_menu_texts）
        self._menu_texts: list = []

        def _menu(key: str):
            m = menu_bar.addMenu(tr(key))
            self._menu_texts.append((m.setTitle, key))
            return m

        def _act(key: str) -> QAction:
            a = QAction(tr(key), self)
            self._menu_texts.append((a.setText, key))
            return a

        # 文件
        m_file = _menu("文件")
        act_card = _act("新建卡片")
        act_card.setShortcut(QKeySequence.New)
        act_card.triggered.connect(self._on_quick_add)
        m_file.addAction(act_card)
        act_list = _act("新建列表")
        act_list.triggered.connect(self._on_list_add)
        m_file.addAction(act_list)
        act_bulk = _act("批量添加卡片")
        act_bulk.triggered.connect(self._on_bulk_add)
        m_file.addAction(act_bulk)
        m_file.addSeparator()
        act_md = _act("导出 Markdown")
        act_md.triggered.connect(lambda: self._on_export("md"))
        m_file.addAction(act_md)
        act_csv = _act("导出 CSV")
        act_csv.triggered.connect(lambda: self._on_export("csv"))
        m_file.addAction(act_csv)
        m_file.addSeparator()
        act_archive = _act("打开归档")
        act_archive.triggered.connect(self._on_archive_open)
        m_file.addAction(act_archive)

        # 编辑
        m_edit = _menu("编辑")
        act_undo = _act("撤销")
        act_undo.setShortcut(QKeySequence.Undo)
        act_undo.triggered.connect(lambda: self._on_undo_requested(False))
        m_edit.addAction(act_undo)
        m_edit.addSeparator()
        for label, seq, method in (("剪切", QKeySequence.Cut, "cut"),
                                   ("复制", QKeySequence.Copy, "copy"),
                                   ("粘贴", QKeySequence.Paste, "paste"),
                                   ("全选", QKeySequence.SelectAll, "selectAll")):
            act = _act(label)
            act.setShortcut(seq)
            act.triggered.connect(
                lambda _=False, m=method: self._edit_focus_widget(m))
            m_edit.addAction(act)

        # 视图
        m_view = _menu("视图")
        self._menu_act_today = _act("今日聚焦")
        self._menu_act_today.setCheckable(True)
        self._menu_act_today.setChecked(self._board_view.is_today_mode())
        self._menu_act_today.toggled.connect(self._on_menu_today_toggled)
        m_view.addAction(self._menu_act_today)
        self._menu_act_dark = _act("深色主题")
        self._menu_act_dark.setCheckable(True)
        self._menu_act_dark.setChecked(AppTheme.mode() == "dark")
        self._menu_act_dark.toggled.connect(self._on_menu_dark_toggled)
        m_view.addAction(self._menu_act_dark)
        self._menu_act_always_top = _act("窗口置顶")
        self._menu_act_always_top.setCheckable(True)
        self._menu_act_always_top.setChecked(self._window.is_always_on_top())
        self._menu_act_always_top.toggled.connect(self._on_always_top_toggled)
        m_view.addAction(self._menu_act_always_top)
        m_view.addSeparator()
        act_expand = _act("展开看板")
        act_expand.triggered.connect(self._window.expand)
        m_view.addAction(act_expand)
        act_collapse = _act("收起为桌宠")
        act_collapse.setShortcut(QKeySequence.Close)   # Cmd+W
        act_collapse.triggered.connect(self._window.collapse)
        m_view.addAction(act_collapse)

        # 应用菜单项（macOS 按 role 自动归入应用名菜单）
        act_about = _act("关于桌宠看板")
        act_about.setMenuRole(QAction.MenuRole.AboutRole)
        act_about.triggered.connect(self._show_about)
        m_file.addAction(act_about)
        act_settings = _act("设置…")
        act_settings.setMenuRole(QAction.MenuRole.PreferencesRole)
        act_settings.setShortcut(QKeySequence.Preferences)
        act_settings.triggered.connect(self._on_settings_open)
        m_file.addAction(act_settings)
        act_quit = _act("退出")
        act_quit.setMenuRole(QAction.MenuRole.QuitRole)
        act_quit.setShortcut(QKeySequence.Quit)
        act_quit.triggered.connect(self._on_quit)
        m_file.addAction(act_quit)

        # 主题被动变化（如跟随系统）时同步菜单勾选态
        AppTheme.signal_theme_applied.connect(self._sync_menu_dark)

    def _reapply_menu_texts(self) -> None:
        """语言切换后整栏重设菜单文案（勾选态不受影响）"""
        for setter, key in getattr(self, "_menu_texts", []):
            setter(tr(key))

    def _on_menu_today_toggled(self, on: bool) -> None:
        self._board_view.set_today_mode(on)

    def _on_today_mode_changed(self, on: bool) -> None:
        act = getattr(self, "_menu_act_today", None)
        if act is not None and act.isChecked() != on:
            act.blockSignals(True)
            act.setChecked(on)
            act.blockSignals(False)

    def _on_menu_dark_toggled(self, on: bool) -> None:
        self._on_theme_selected("dark" if on else "light")

    def _sync_menu_dark(self, mode: str) -> None:
        act = getattr(self, "_menu_act_dark", None)
        if act is not None and act.isChecked() != (mode == "dark"):
            act.blockSignals(True)
            act.setChecked(mode == "dark")
            act.blockSignals(False)

    def _edit_focus_widget(self, method: str) -> None:
        """把标准编辑操作应用到当前聚焦的文本控件"""
        fw = QApplication.focusWidget()
        if isinstance(fw, (QLineEdit, QPlainTextEdit)):
            getattr(fw, method)()

    def _show_about(self) -> None:
        from app.i18n import app_display_name
        QMessageBox.about(
            self._window, tr("关于桌宠看板"),
            f"{app_display_name()} v{AppConfig.APP_VERSION}\n"
            + tr("桌宠形态的轻量任务看板：今日聚焦、番茄钟、归档与导出。"))

    # ── 撤销 ──────────────────────────────────────────────

    def _push_undo(self) -> None:
        """在变更前保存看板快照（撤销恢复用）"""
        self._undo_stack.append(self._store.load().to_dict())
        del self._undo_stack[:-AppConfig.UNDO_LIMIT]

    def _on_undo_requested(self, notify_empty: bool = False) -> None:
        if not self._undo_stack:
            if notify_empty:
                self._notify(tr("没有可撤销的操作"))
            return
        doc = self._undo_stack.pop()
        self._store.replace_board(Board.from_dict(doc))
        self._after_data_change(None)
        self._notify(tr("已撤销上一步"))

    # ── 截止提醒 ──────────────────────────────────────────

    def _check_due_dates(self) -> None:
        """截止提醒：逐卡检查（逾期 / 今天截止），每天每卡只提醒一次

        提醒签名（card_id:due_date:kind + 日期）持久化到 QSettings，
        同一天重启不再重复轰炸；通知聚合为最多两条主条目 + 数量，
        折叠态时桌宠跳一下示意。
        """
        today = date.today()
        board = self._store.load()
        log = AppConfig.get_remind_log()
        log_for_today = set(log.get(today.isoformat(), ()))   # set 判重 O(1)
        items: list[tuple[str, str]] = []   # (卡片标题, 状态文案)
        for lst in board.lists:
            for c in lst.cards:
                if c.done or c.archived:
                    continue
                delta = c.due_delta(today)
                if delta is None or delta > 0:
                    continue
                kind = "overdue" if delta < 0 else "today"
                key = f"{c.id}:{c.due_date}:{kind}"
                if key in log_for_today:
                    continue
                log_for_today.add(key)
                items.append((c.title, tr("已逾期") if kind == "overdue"
                              else tr("今天截止")))
        # 清旧日志（只留今天与昨天）：必须无条件执行——放在"无新提醒就
        # 早退"之后会让旧条目在无提醒的日子永不修剪，日志越积越大
        keep = (today.isoformat(),
                (today - timedelta(days=1)).isoformat())
        trimmed = {d: v for d, v in log.items() if d in keep}
        trimmed[today.isoformat()] = sorted(log_for_today)
        if items or trimmed.keys() != log.keys():
            AppConfig.save_remind_log(trimmed)
        if not items:
            return

        parts = [f"「{t}」{k}" for t, k in items[:2]]
        if len(items) > 2:
            parts.append(tr("等 {n} 项").format(n=len(items)))
        self._tray.show_notification(
            tr("截止提醒：{items}").format(items=tr("、").join(parts)))
        if self._window.mode == "collapsed":
            self._pet_view.nudge()

    # ── 番茄钟 ────────────────────────────────────────────

    def _on_card_pomo(self, card_id: str) -> None:
        board = self._store.load()
        _lst, card = board.find_card(card_id)
        if card is None:
            return
        if self._pomo_card_id == card_id:
            self._pomo_stop()          # 再次触发同一张卡 = 停止
            return
        if self._pomo_card_id is not None:
            # 专注中误点其他卡的"开始专注"会无声清零当前进度：先确认
            cur = board.find_card(self._pomo_card_id)[1]
            cur_title = cur.title[:20] if cur is not None else "当前卡片"
            reply = QMessageBox.question(
                self._window, tr("切换专注"),
                tr("正在专注「{title}」，切换将放弃当前进度。\n继续？").format(
                    title=cur_title),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
            self._pomo_stop()          # 确认切换：结束当前（不计番茄）
        self._pomo_start(card)

    def _pomo_start(self, card: Card) -> None:
        self._pomo_card_id = card.id
        self._pomo_shown_minute = None   # 强制首个 tick 刷新托盘 tooltip
        self._pomo_left = AppConfig.POMODORO_MINUTES * 60
        self._pomo_timer.start()
        self._board_view.set_focusing_card(card.id)
        self._pet_view.set_focus_mode(True)
        m, s = divmod(self._pomo_left, 60)
        self._pet_view.set_badge_override(f"{m:02d}:{s:02d}")
        self._tray.set_tooltip(tr("专注中 {time} · {title}").format(
            time=f"{m:02d}:{s:02d}", title=card.title[:16]))

    def _pomo_stop(self, finished: bool = False) -> None:
        card_id, self._pomo_card_id = self._pomo_card_id, None
        self._pomo_timer.stop()
        self._board_view.set_focusing_card(None)
        self._pet_view.set_focus_mode(False)
        self._pet_view.set_badge_override(None)
        self._tray.set_tooltip(AppConfig.APP_NAME)
        self._refresh_pet_state()
        if finished:
            board = self._store.load()
            _lst, card = board.find_card(card_id)
            if card is not None:
                card.pomodoros += 1
                if card.pomodoros % 5 == 0:
                    self._after_data_change(
                        tr("专注完成！累计 {n} 个番茄 🍅").format(
                            n=card.pomodoros))
                else:
                    self._after_data_change(tr("专注完成！休息一下 🎉"))
            else:
                self._notify(tr("专注完成！休息一下 🎉"))
        else:
            self._notify(tr("已结束专注"))

    def _pomo_tick(self) -> None:
        if self._pomo_card_id is None:
            return
        self._pomo_left -= 1
        if self._pomo_left <= 0:
            self._pomo_stop(finished=True)
            return
        m, s = divmod(self._pomo_left, 60)
        self._pet_view.set_badge_override(f"{m:02d}:{s:02d}")
        # 托盘 tooltip 是平台调用（触发系统托盘重绘），文案分钟粒度才变：
        # 只在分钟位变化时更新，顺带省掉每秒一次的全板 find_card
        if m != self._pomo_shown_minute:
            self._pomo_shown_minute = m
            board = self._store.load()
            _lst, card = board.find_card(self._pomo_card_id)
            title = card.title[:16] if card else ""
            self._tray.set_tooltip(tr("专注中 {time} · {title}").format(
                time=f"{m:02d}:{s:02d}", title=title))

    # ── 归档 ──────────────────────────────────────────────

    def _on_card_star(self, card_id: str) -> None:
        """星标 toggle（卡片右键 / 今日浮窗行按钮）：加入或移出今日聚焦"""
        board = self._store.load()
        _lst, card = board.find_card(card_id)
        if card is None:
            return
        self._push_undo()
        card.starred = not card.starred
        self._after_data_change(
            tr("已加入今日") if card.starred else tr("已移出今日"))

    def _on_card_workdir_open(self, card_id: str) -> None:
        """打开卡片工作目录（📂 徽章 / 右键菜单）

        目录常在外接移动硬盘上，不在是常态：先 is_dir 校验，打不开走
        _notify 轻提示（展开态 toast / 折叠态托盘气泡），不弹错误框。
        """
        board = self._store.load()
        _lst, card = board.find_card(card_id)
        if card is None:
            return
        path = Path(card.workdir)
        if not card.workdir or not path.is_dir():
            self._notify(tr("工作目录无法访问（设备可能未连接）"))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _on_card_workdir_set(self, card_id: str) -> None:
        """右键快捷设置/更改工作目录：选完即存，不经过编辑对话框"""
        board = self._store.load()
        _lst, card = board.find_card(card_id)
        if card is None:
            return
        start = (card.workdir if card.workdir and Path(card.workdir).is_dir()
                 else str(Path.home()))
        chosen = QFileDialog.getExistingDirectory(
            self._window, tr("选择工作目录"), start)
        if not chosen:
            return
        self._push_undo()
        card.workdir = chosen
        self._after_data_change(tr("已设置工作目录"))

    def _on_card_archive(self, card_id: str) -> None:
        board = self._store.load()
        _lst, card = board.find_card(card_id)
        if card is None or card.archived:
            return
        self._push_undo()
        if card_id == self._pomo_card_id:
            self._pomo_stop()          # 归档正专注的卡片时先结束番茄钟
        card.archived = True
        self._after_data_change(tr("已归档"))

    def _on_archive_open(self) -> None:
        from app.views.archive_dialog import ArchiveDialog

        if self._archive_dialog is None:
            self._archive_dialog = ArchiveDialog(self._window)
            self._archive_dialog.signal_restore_requested.connect(
                self._on_card_restore)
        self._archive_dialog.show()
        self._archive_dialog.raise_()
        self._archive_dialog.activateWindow()
        self._refresh_archive()

    def _refresh_archive(self) -> None:
        """归档对话框可见时按需刷新：归档内容未变则跳过整树重建"""
        dlg = self._archive_dialog
        if dlg is None or not dlg.isVisible():
            return
        board = self._store.load()
        archived = board.archived_cards()
        weekly = board.weekly_done_count()
        key = (weekly, tuple((lst.id, c.id, c.title, c.done)
                             for lst, c in archived))
        if key == self._archive_key:
            return
        self._archive_key = key
        dlg.set_items(archived, weekly)

    def _on_card_restore(self, card_id: str) -> None:
        board = self._store.load()
        _lst, card = board.find_card(card_id)
        if card is None or not card.archived:
            return
        self._push_undo()
        card.archived = False
        self._after_data_change(tr("已恢复"))

    # ── 系统主题跟随 ──────────────────────────────────────

    def _on_system_scheme_changed(self, *_args) -> None:
        """系统深浅色切换：仅当用户未手动固定主题（mode=system）时跟随"""
        if AppConfig.get_theme_mode() == "system":
            AppTheme.set_mode("system")

    # ── 导出 ──────────────────────────────────────────────

    def _on_export(self, fmt: str) -> None:
        board = self._store.load()
        default = Path(str(AppConfig.DATA_DIR)) / \
            f"桌宠看板导出_{date.today():%Y%m%d}.{fmt}"
        path, _ = QFileDialog.getSaveFileName(
            self._window, tr("导出看板"), str(default),
            "Markdown (*.md)" if fmt == "md" else "CSV (*.csv)")
        if not path:
            return
        try:
            if fmt == "md":
                self._write_export_md(Path(path), board)
            else:
                self._write_export_csv(Path(path), board)
        except OSError as e:
            self._show_error(tr("导出失败：{err}").format(err=e))
            return
        self._tray.show_notification(
            tr("已导出到 {path}").format(path=path))

    def _on_export_backup(self) -> None:
        """导出完整看板备份（含归档，可用于日后导入恢复）"""
        board = self._store.load()
        default = Path(str(AppConfig.DATA_DIR)) / \
            f"桌宠看板备份_{date.today():%Y%m%d}.json"
        path, _ = QFileDialog.getSaveFileName(
            self._window, tr("导出备份"), str(default), "JSON (*.json)")
        if not path:
            return
        try:
            Path(path).write_text(
                json.dumps(board.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8")
        except OSError as e:
            self._show_error(tr("导出失败：{err}").format(err=e))
            return
        self._tray.show_notification(
            tr("备份已导出到 {path}").format(path=path))

    def _on_import_backup(self) -> None:
        """从备份导入：确认后整体替换当前看板（撤销栈清空）"""
        path, _ = QFileDialog.getOpenFileName(
            self._window, tr("从备份导入"), str(AppConfig.DATA_DIR),
            "JSON (*.json)")
        if not path:
            return
        try:
            doc = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                raise ValueError("备份文件不是有效的数据结构")
            new_board = Board.from_dict(doc)
        except (OSError, ValueError, AttributeError, TypeError) as e:
            self._show_error(tr("备份文件无法读取：{err}").format(err=e))
            return
        reply = QMessageBox.question(
            self._window, tr("从备份导入"),
            tr("导入将替换当前看板（建议先导出备份）。\n继续？"),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self._store.replace_board(new_board)
        self._undo_stack.clear()
        self._after_data_change(tr("已导入备份"))

    @staticmethod
    def _write_export_md(path: Path, board) -> None:
        lines = [f"# 看板导出（{date.today():%Y-%m-%d}）", ""]
        for lst in board.lists:
            cards = [c for c in lst.cards if not c.archived]
            lines.append(f"## {lst.title}")
            lines.append("")
            if not cards:
                lines.append("_（空）_")
                lines.append("")
                continue
            for c in cards:
                mark = "x" if c.done else " "
                extra = ""
                if c.priority:
                    extra += f" {AppConfig.PRIORITY_MARKS.get(c.priority, '')}"
                if c.due_date:
                    extra += f" 📅 {c.due_date}"
                if c.repeat != "never":
                    extra += f" 🔁{AppConfig.REPEAT_NAMES.get(c.repeat, '')}"
                if c.pomodoros:
                    extra += f" 🍅×{c.pomodoros}"
                lines.append(f"- [{mark}] {c.title}{extra}")
                for ln in c.notes.splitlines():
                    lines.append(f"      {ln}")
            lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")

    @staticmethod
    def _write_export_csv(path: Path, board) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["列表", "标题", "备注", "标签", "截止日期",
                             "完成", "优先级", "番茄数", "重复", "创建时间"])
            for lst in board.lists:
                for c in lst.cards:
                    if c.archived:
                        continue
                    writer.writerow([
                        lst.title, c.title, c.notes,
                        ";".join(c.labels), c.due_date or "",
                        "是" if c.done else "否",
                        AppConfig.PRIORITY_NAMES.get(c.priority, ""),
                        c.pomodoros,
                        AppConfig.REPEAT_NAMES.get(c.repeat, ""), c.created_at,
                    ])

    # ── 托盘 / 显隐 / 退出 ────────────────────────────────

    def _on_window_visibility(self, visible: bool) -> None:
        self._tray.set_window_visible(visible)

    def _on_tray_show(self) -> None:
        self._window.show_and_activate()

    def _on_tray_hide(self) -> None:
        self._window.hide_to_tray()

    def _on_quit(self) -> None:
        self._flush_store()
        self._tray.hide()
        QApplication.quit()

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self._window, tr("错误"), message)

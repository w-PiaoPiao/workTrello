"""
应用控制器：连接数据模型、托盘服务与 UI 视图
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QLineEdit,
    QMenuBar,
    QMessageBox,
    QPlainTextEdit,
)

from app.config import AppConfig
from app.models import json_io
from app.models.board import Board, BoardList, BoardStore, Card
from app.services.tray_service import TrayService
from app.views import motion
from app.views.archive_dialog import ArchiveDialog
from app.views.board_view import BoardView
from app.views.card_dialog import CardDialog
from app.views.main_window import MainWindow
from app.views.pet_view import PetView
from app.views.theme import AppTheme
from app.views.today_popover import TodayPopover

logger = logging.getLogger(__name__)


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
        if app is not None:
            app.aboutToQuit.connect(self._flush_store)

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
            self._show_error(
                "看板数据文件异常（"
                + "；".join("已损坏并隔离" if k == "corrupted" else "暂时无法读取"
                            for k in kinds)
                + "），本次以默认看板启动。")
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
            # exe 非原子写空板），快照链仍可恢复；空板不产生快照
            json_io.snapshot_board(self._store.path)
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
            "看板数据为空",
            f"看板当前没有任何卡片，但检测到 {len(candidates)} 份历史数据"
            f"副本（最近一份：{latest.name}）。\n"
            "是否恢复最近一份数据？\n"
            "选择「否」将保持空看板，且下次不再询问（直到再次录入过数据）。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            AppConfig.set_empty_board_ack(True)
            return
        if not json_io.restore_from_backup(self._store.path, latest):
            AppConfig.set_empty_board_ack(True)
            self._show_error(f"快照 {latest.name} 无法解析，恢复失败。")
            return
        board = self._store.reload()
        self._apply_board_to_ui(board)
        self._store.mark_dirty()
        self._schedule_save()
        self._tray.show_notification("已从快照恢复看板数据")

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
        else:
            logger.info("已保留最近好数据副本: %s", keep)

    def _offer_restore(self, kinds: list[str]) -> bool:
        """数据文件异常时询问是否从最近好副本（.prev）恢复；成功恢复返回 True"""
        bak = json_io.good_prev_copy(self._store.path)
        if bak is None:
            return False
        corrupted = "corrupted" in kinds
        detail = "已损坏并隔离" if corrupted else "暂时无法读取"
        origin_note = ("原文件已自动隔离备份。\n" if corrupted
                       else "原文件仍保留在原位置。\n")
        reply = QMessageBox.question(
            self._window,
            "看板数据异常",
            f"看板数据文件{detail}，{origin_note}"
            f"检测到最近一次成功保存的副本（{bak.name}），是否用它恢复数据？\n"
            "选择「否」则以默认看板启动。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return False
        if not json_io.restore_from_backup(self._store.path, bak):
            self._preserve_good_copy()
            self._show_error(
                f"备份文件 {bak.name} 无法解析，恢复失败，本次以默认看板启动。")
            return False
        # 重新读盘，取回刚写入的恢复数据
        board = self._store.reload()
        self._apply_board_to_ui(board)
        self._store.mark_dirty()
        self._schedule_save()
        self._tray.show_notification("已从备份恢复看板数据")
        return True

    def _apply_board_to_ui(self, board) -> None:
        self._board_view.refresh(board.lists)
        self._refresh_pet_state()

    def _schedule_save(self) -> None:
        self._save_timer.start()

    def _flush_store(self) -> None:
        board = self._store.load()
        had_cards = any(lst.cards for lst in board.lists)
        try:
            self._store.flush()
        except Exception as e:
            logger.error("保存数据失败: %s", e)
            # 落盘失败用户可见，避免静默丢写
            self._tray.show_notification(
                "看板数据保存失败，请检查磁盘空间或文件权限")
            return
        if had_cards:
            # 保存过含卡数据：清除"已确认空板"标记，下次真空时会重新询问
            AppConfig.clear_empty_board_ack()

    # ── 信号 ──────────────────────────────────────────────

    def _connect_signals(self) -> None:
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
        self._board_view.signal_archive_open.connect(self._on_archive_open)
        self._board_view.signal_export.connect(self._on_export)
        self._board_view.signal_export_backup.connect(self._on_export_backup)
        self._board_view.signal_import_backup.connect(self._on_import_backup)
        self._board_view.signal_list_collapsed.connect(self._on_list_collapsed)
        self._board_view.signal_today_toggled.connect(
            self._on_today_mode_changed)
        self._pet_view.signal_today_list_clicked.connect(
            self._on_today_list_open)

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
        """桌宠右键快速添加：弹输入框，加到第一个列表"""
        title, ok = QInputDialog.getText(self._window, "快速添加卡片", "卡片标题：")
        if ok and title.strip():
            board = self._store.load()
            if board.lists:
                self._on_card_add(board.lists[0].id, title.strip())

    def _today_focus_items(self) -> list[tuple[BoardList, Card]]:
        """今日聚焦卡片（含所属列表），供今日清单浮窗显示"""
        board = self._store.load()
        today = date.today()
        return [(lst, c) for lst in board.lists for c in lst.cards
                if c.in_today_focus(today)]

    def _on_today_list_open(self) -> None:
        """桌宠右键"今日清单"：浮窗概览今日待办（勾选/打开编辑直通控制器）"""
        if self._today_popover is None:
            self._today_popover = TodayPopover()
            self._today_popover.signal_card_done.connect(self._on_card_done)
            self._today_popover.signal_card_edit.connect(self._on_card_edit)
        self._today_popover.set_items(self._today_focus_items())
        self._today_popover.show_below(self._window.frameGeometry())

    def _on_card_add(self, list_id: str, title: str = "") -> None:
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
            card = Card(title=title)
        lst.cards.insert(0, card)
        self._after_data_change("已添加卡片")

    def _on_card_edit(self, list_id: str, card_id: str) -> None:
        _lst, card = self._store.load().find_card(card_id)
        if card is None:
            return
        dialog = CardDialog(card, self._window)
        dialog.setAttribute(Qt.WA_DeleteOnClose)   # 同 _on_card_add：用完即销毁
        if dialog.exec() != CardDialog.Accepted:
            return
        self._push_undo()
        card.apply(dialog.result_card())
        self._after_data_change("已保存")

    def _on_card_done(self, list_id: str, card_id: str, done: bool) -> None:
        _lst, card = self._store.load().find_card(card_id)
        if card is None:
            return
        self._push_undo()
        card.set_done(done)
        if done and card.roll_repeat():
            # 重复任务：完成即滚动到下一周期并复位，提示下次日期
            self._after_data_change(
                f"已完成 · 下次 {card.due_date[5:].replace('-', '/')}")
            return
        self._after_data_change(None)
        if done:
            self._notify_done(card)

    def _notify_done(self, card: Card) -> None:
        board = self._store.load()
        today_n = board.today_done_count()
        total = board.total_cards()
        done = board.done_cards()
        if total > 0 and done == total:
            self._tray.show_notification("全部完成！桌宠为你鼓掌 🎉")
            if self._window.mode == "collapsed":
                self._pet_view.celebrate()
        elif today_n == 3:
            self._tray.show_notification("今日已完成 3 张，节奏不错！🌱")
        elif today_n == 5:
            self._tray.show_notification("今日已完成 5 张，收工级表现！🏆")
        elif done > 0 and done % 5 == 0:
            self._tray.show_notification(f"已完成 {done} 张卡片，继续加油！")

    def _on_card_delete(self, list_id: str, card_id: str) -> None:
        _lst, card = self._store.load().find_card(card_id)
        title = card.title if card else "此卡片"
        reply = QMessageBox.question(
            self._window,
            "确认删除",
            f"确定要删除「{title[:30]}」吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._push_undo()
        if card_id == self._pomo_card_id:
            self._pomo_stop()          # 删除正专注的卡片时先结束番茄钟
        if self._store.load().remove_card(card_id) is None:
            return
        self._after_data_change("已删除 · " + self._undo_hint())

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
        board.remove_card(card_id)
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
        title, ok = QInputDialog.getText(self._window, "添加列表", "列表名称：")
        if not (ok and title.strip()):
            return
        self._push_undo()
        self._store.load().lists.append(BoardList(title=title.strip()))
        self._after_data_change("已添加列表")

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
                self._window, "确认删除",
                f"列表「{lst.title}」还有 {n} 张卡片，删除后不可恢复。\n继续？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
        # 空列表无卡片可丢，直接删除（撤销栈可恢复）
        self._push_undo()
        board.remove_list(list_id)
        self._after_data_change("已删除列表 · " + self._undo_hint())

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

    def _after_data_change(self, notify: str | None) -> None:
        board = self._store.load()
        # 任何模型改动（增删改卡/列、拖拽、归档、番茄计数…）都必须在此标脏：
        # flush() 只在脏标记为真时落盘，只 start() 防抖计时器而不标脏，
        # 计时器到点后会因不脏而直接返回，编辑将停留在内存、退出即丢
        self._store.mark_dirty()
        self._board_view.refresh(board.lists)
        self._refresh_pet_state()
        self._refresh_archive()
        self._schedule_save()
        if notify:
            self._notify(notify)
        # 今日清单浮窗可见时按模型全量重建（编辑保存/撤销/勾选后的行
        # 与徽章保持同步；今日谓词天然排除刚完成的卡）
        if (self._today_popover is not None
                and self._today_popover.isVisible()):
            self._today_popover.set_items(self._today_focus_items())

    def _notify(self, text: str) -> None:
        """操作反馈：看板展开态走窗口内 toast，折叠/隐藏态走托盘气泡"""
        if self._window.mode == "expanded":
            self._board_view.show_toast(text)
        else:
            self._tray.show_notification(text)

    def _undo_hint(self) -> str:
        return "⌘Z 撤销" if AppConfig.IS_MACOS else "Ctrl+Z 撤销"

    # ── 桌宠状态联动 ──────────────────────────────────────

    def _refresh_pet_state(self) -> None:
        """角标=今日聚焦量（专注时显示倒计时）；表情：逾期难过 / 今日截止紧张 / 其余开心"""
        board = self._store.load()
        if self._pomo_card_id is None:
            n = len(board.today_focus_cards(date.today()))
            self._pet_view.update_count(n)
        overdue, due_today = board.due_counts(date.today())
        if overdue:
            mood = "sad"
        elif due_today:
            mood = "worried"
        else:
            mood = "happy"
        self._pet_view.set_mood(mood)

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

        # 文件
        m_file = menu_bar.addMenu("文件")
        act_card = QAction("新建卡片", self)
        act_card.setShortcut(QKeySequence.New)
        act_card.triggered.connect(self._on_quick_add)
        m_file.addAction(act_card)
        act_list = QAction("新建列表", self)
        act_list.triggered.connect(self._on_list_add)
        m_file.addAction(act_list)
        m_file.addSeparator()
        act_md = QAction("导出 Markdown", self)
        act_md.triggered.connect(lambda: self._on_export("md"))
        m_file.addAction(act_md)
        act_csv = QAction("导出 CSV", self)
        act_csv.triggered.connect(lambda: self._on_export("csv"))
        m_file.addAction(act_csv)
        m_file.addSeparator()
        act_archive = QAction("打开归档", self)
        act_archive.triggered.connect(self._on_archive_open)
        m_file.addAction(act_archive)

        # 编辑
        m_edit = menu_bar.addMenu("编辑")
        act_undo = QAction("撤销", self)
        act_undo.setShortcut(QKeySequence.Undo)
        act_undo.triggered.connect(lambda: self._on_undo_requested(False))
        m_edit.addAction(act_undo)
        m_edit.addSeparator()
        for label, seq, method in (("剪切", QKeySequence.Cut, "cut"),
                                   ("复制", QKeySequence.Copy, "copy"),
                                   ("粘贴", QKeySequence.Paste, "paste"),
                                   ("全选", QKeySequence.SelectAll, "selectAll")):
            act = QAction(label, self)
            act.setShortcut(seq)
            act.triggered.connect(
                lambda _=False, m=method: self._edit_focus_widget(m))
            m_edit.addAction(act)

        # 视图
        m_view = menu_bar.addMenu("视图")
        self._menu_act_today = QAction("今日聚焦", self)
        self._menu_act_today.setCheckable(True)
        self._menu_act_today.setChecked(self._board_view.is_today_mode())
        self._menu_act_today.toggled.connect(self._on_menu_today_toggled)
        m_view.addAction(self._menu_act_today)
        self._menu_act_dark = QAction("深色主题", self)
        self._menu_act_dark.setCheckable(True)
        self._menu_act_dark.setChecked(AppTheme.mode() == "dark")
        self._menu_act_dark.toggled.connect(self._on_menu_dark_toggled)
        m_view.addAction(self._menu_act_dark)
        self._menu_act_always_top = QAction("窗口置顶", self)
        self._menu_act_always_top.setCheckable(True)
        self._menu_act_always_top.setChecked(self._window.is_always_on_top())
        self._menu_act_always_top.toggled.connect(self._on_always_top_toggled)
        m_view.addAction(self._menu_act_always_top)
        m_view.addSeparator()
        act_expand = QAction("展开看板", self)
        act_expand.triggered.connect(self._window.expand)
        m_view.addAction(act_expand)
        act_collapse = QAction("收起为桌宠", self)
        act_collapse.setShortcut(QKeySequence.Close)   # Cmd+W
        act_collapse.triggered.connect(self._window.collapse)
        m_view.addAction(act_collapse)

        # 应用菜单项（macOS 按 role 自动归入应用名菜单）
        act_about = QAction("关于桌宠看板", self)
        act_about.setMenuRole(QAction.MenuRole.AboutRole)
        act_about.triggered.connect(self._show_about)
        m_file.addAction(act_about)
        act_quit = QAction("退出", self)
        act_quit.setMenuRole(QAction.MenuRole.QuitRole)
        act_quit.setShortcut(QKeySequence.Quit)
        act_quit.triggered.connect(self._on_quit)
        m_file.addAction(act_quit)

        # 主题被动变化（如跟随系统）时同步菜单勾选态
        AppTheme.signal_theme_applied.connect(self._sync_menu_dark)

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
        QMessageBox.about(
            self._window, "关于桌宠看板",
            f"桌宠看板 v{AppConfig.APP_VERSION}\n"
            "桌宠形态的轻量任务看板：今日聚焦、番茄钟、归档与导出。")

    # ── 撤销 ──────────────────────────────────────────────

    def _push_undo(self) -> None:
        """在变更前保存看板快照（撤销恢复用）"""
        self._undo_stack.append(self._store.load().to_dict())
        del self._undo_stack[:-AppConfig.UNDO_LIMIT]

    def _on_undo_requested(self, notify_empty: bool = False) -> None:
        if not self._undo_stack:
            if notify_empty:
                self._notify("没有可撤销的操作")
            return
        doc = self._undo_stack.pop()
        self._store.replace_board(Board.from_dict(doc))
        self._after_data_change(None)
        self._notify("已撤销上一步")

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
        log_for_today = log.get(today.isoformat(), [])
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
                log_for_today.append(key)
                items.append((c.title, "已逾期" if kind == "overdue"
                              else "今天截止"))
        if not items:
            return
        # 清旧日志：只保留今天与昨天（防无限增长）
        keep = (today.isoformat(),
                (today - timedelta(days=1)).isoformat())
        log = {d: v for d, v in log.items() if d in keep}
        log[today.isoformat()] = log_for_today
        AppConfig.save_remind_log(log)

        parts = [f"「{t}」{k}" for t, k in items[:2]]
        if len(items) > 2:
            parts.append(f"等 {len(items)} 项")
        self._tray.show_notification("截止提醒：" + "、".join(parts))
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
        self._tray.set_tooltip(f"专注中 {m:02d}:{s:02d} · {card.title[:16]}")

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
                        f"专注完成！累计 {card.pomodoros} 个番茄 🍅")
                else:
                    self._after_data_change("专注完成！休息一下 🎉")
            else:
                self._tray.show_notification("专注完成！休息一下 🎉")
        else:
            self._tray.show_notification("已结束专注")

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
            self._tray.set_tooltip(f"专注中 {m:02d}:{s:02d} · {title}")

    # ── 归档 ──────────────────────────────────────────────

    def _on_card_archive(self, card_id: str) -> None:
        board = self._store.load()
        _lst, card = board.find_card(card_id)
        if card is None or card.archived:
            return
        self._push_undo()
        if card_id == self._pomo_card_id:
            self._pomo_stop()          # 归档正专注的卡片时先结束番茄钟
        card.archived = True
        self._after_data_change("已归档")

    def _on_archive_open(self) -> None:
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
        self._after_data_change("已恢复")

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
            self._window, "导出看板", str(default),
            "Markdown (*.md)" if fmt == "md" else "CSV (*.csv)")
        if not path:
            return
        try:
            if fmt == "md":
                self._write_export_md(Path(path), board)
            else:
                self._write_export_csv(Path(path), board)
        except OSError as e:
            self._show_error(f"导出失败：{e}")
            return
        self._tray.show_notification(f"已导出到 {path}")

    def _on_export_backup(self) -> None:
        """导出完整看板备份（含归档，可用于日后导入恢复）"""
        board = self._store.load()
        default = Path(str(AppConfig.DATA_DIR)) / \
            f"桌宠看板备份_{date.today():%Y%m%d}.json"
        path, _ = QFileDialog.getSaveFileName(
            self._window, "导出备份", str(default), "JSON (*.json)")
        if not path:
            return
        try:
            Path(path).write_text(
                json.dumps(board.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8")
        except OSError as e:
            self._show_error(f"导出失败：{e}")
            return
        self._tray.show_notification(f"备份已导出到 {path}")

    def _on_import_backup(self) -> None:
        """从备份导入：确认后整体替换当前看板（撤销栈清空）"""
        path, _ = QFileDialog.getOpenFileName(
            self._window, "从备份导入", str(AppConfig.DATA_DIR),
            "JSON (*.json)")
        if not path:
            return
        try:
            doc = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                raise ValueError("备份文件不是有效的数据结构")
            new_board = Board.from_dict(doc)
        except (OSError, ValueError, AttributeError, TypeError) as e:
            self._show_error(f"备份文件无法读取：{e}")
            return
        reply = QMessageBox.question(
            self._window, "从备份导入",
            "导入将替换当前看板（建议先导出备份）。\n继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self._store.replace_board(new_board)
        self._undo_stack.clear()
        self._after_data_change("已导入备份")

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
        QMessageBox.warning(self._window, "错误", message)

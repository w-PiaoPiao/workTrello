"""
应用控制器：连接数据模型、托盘服务与 UI 视图
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from app.config import AppConfig
from app.models import json_io
from app.models.board import BoardList, BoardStore, Card
from app.services.tray_service import TrayService
from app.views.board_view import BoardView
from app.views.card_dialog import CardDialog
from app.views.main_window import MainWindow
from app.views.pet_view import PetView
from app.views.theme import AppTheme

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

        # ── 加载数据（含损坏恢复询问） ─────────────────────
        self._load_data()

        # ── 连接信号 ──────────────────────────────────────
        self._connect_signals()

        # ── 主题 / 动画偏好恢复 ───────────────────────────
        AppTheme.set_mode(AppConfig.get_theme_mode())
        if not AppConfig.get_animation_enabled():
            self._pet_view.set_animation_enabled(False)

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
        # 首次启动生成的默认看板也落盘（否则用户未做修改就退出时数据不保存）
        self._store.mark_dirty()
        self._schedule_save()

    def _preserve_good_copy(self) -> None:
        """把最近好副本（.prev）复制为带时间戳的保留文件，防止被后续落盘轮转覆盖"""
        import shutil

        prev = json_io.good_prev_copy(self._store.path)
        if prev is None:
            return
        keep = prev.with_name(prev.name + json_io._backup_ext())
        try:
            shutil.copy2(prev, keep)
        except OSError as e:
            logger.warning("保留好副本失败: %s (%s)", keep, e)
        else:
            logger.info("已保留最近好数据副本: %s", keep)

    def _offer_restore(self, kinds: list[str]) -> bool:
        """数据文件异常时询问是否从备份恢复；成功恢复返回 True"""
        bak = json_io.good_prev_copy(self._store.path)
        if bak is None:
            bak = json_io.latest_backup(self._store.path)
        if bak is None:
            return False
        detail = "已损坏并隔离" if "corrupted" in kinds else "暂时无法读取"
        reply = QMessageBox.question(
            self._window,
            "看板数据异常",
            f"看板数据文件{detail}，已自动备份原文件。\n"
            f"检测到可用备份（{bak.name}），是否恢复数据？\n"
            "选择「否」则以默认看板启动，原数据保留在备份文件中。",
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
        self._update_pet_count()

    def _schedule_save(self) -> None:
        self._save_timer.start()

    def _flush_store(self) -> None:
        try:
            self._store.flush()
        except Exception as e:
            logger.error("保存数据失败: %s", e)

    def _update_pet_count(self) -> None:
        self._pet_view.update_count(self._store.load().total_cards())

    # ── 信号 ──────────────────────────────────────────────

    def _connect_signals(self) -> None:
        # 桌宠 → 展开
        self._pet_view.signal_expand_clicked.connect(self._window.expand)
        self._pet_view.signal_quick_add_clicked.connect(self._on_quick_add)
        self._pet_view.signal_quit_requested.connect(self._on_quit)
        self._pet_view.signal_animation_toggled.connect(
            self._on_pet_animation_toggled)

        # 看板 → 折叠 / 主题
        self._board_view.signal_collapse_clicked.connect(self._window.collapse)
        self._board_view.signal_theme_selected.connect(self._on_theme_selected)
        self._board_view.signal_quit_requested.connect(self._on_quit)
        self._board_view.signal_zoom_requested.connect(self._window.toggle_zoom)

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

    # ── 卡片操作 ──────────────────────────────────────────

    def _on_quick_add(self) -> None:
        """桌宠右键快速添加：弹输入框，加到第一个列表"""
        title, ok = QInputDialog.getText(self._window, "快速添加卡片", "卡片标题：")
        if ok and title.strip():
            board = self._store.load()
            if board.lists:
                self._on_card_add(board.lists[0].id, title.strip())

    def _on_card_add(self, list_id: str, title: str = "") -> None:
        lst = self._store.load().find_list(list_id)
        if lst is None:
            return
        if not title:
            dialog = CardDialog(None, self._window)
            if dialog.exec() != CardDialog.Accepted:
                return
            card = Card(**dialog.result_card())
        else:
            card = Card(title=title)
        lst.cards.insert(0, card)
        self._after_data_change("已添加卡片")

    def _on_card_edit(self, list_id: str, card_id: str) -> None:
        _lst, card = self._store.load().find_card(card_id)
        if card is None:
            return
        dialog = CardDialog(card, self._window)
        if dialog.exec() != CardDialog.Accepted:
            return
        card.apply(dialog.result_card())
        self._after_data_change("已保存")

    def _on_card_done(self, list_id: str, card_id: str, done: bool) -> None:
        _lst, card = self._store.load().find_card(card_id)
        if card is None:
            return
        card.done = done
        self._after_data_change(None)
        self._notify_done(card)

    def _notify_done(self, card: Card) -> None:
        board = self._store.load()
        total = board.total_cards()
        done = board.done_cards()
        if total > 0 and done == total:
            self._tray.show_notification("全部完成！桌宠为你鼓掌 🎉")
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
        if self._store.load().remove_card(card_id) is None:
            return
        self._after_data_change("已删除")

    def _on_card_move(self, card_id: str, target_list_id: str, index: int) -> None:
        """拖拽移动卡片（跨列表 / 列表内重排）"""
        board = self._store.load()
        moved = board.remove_card(card_id)
        if moved is None:
            return
        target = board.find_list(target_list_id)
        if target is None:
            target = board.lists[0]
            target.cards.append(moved)
        else:
            index = max(0, min(index, len(target.cards)))
            target.cards.insert(index, moved)
        self._after_data_change(None)

    # ── 列表操作 ──────────────────────────────────────────

    def _on_list_add(self) -> None:
        title, ok = QInputDialog.getText(self._window, "添加列表", "列表名称：")
        if not (ok and title.strip()):
            return
        self._store.load().lists.append(BoardList(title=title.strip()))
        self._after_data_change("已添加列表")

    def _on_list_title_changed(self, list_id: str, new_title: str) -> None:
        lst = self._store.load().find_list(list_id)
        if lst is None:
            return
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
        board.remove_list(list_id)
        self._after_data_change("已删除列表")

    # ── 数据变更后的统一刷新 ──────────────────────────────

    def _after_data_change(self, notify: str | None) -> None:
        board = self._store.load()
        self._board_view.refresh(board.lists)
        self._update_pet_count()
        self._schedule_save()
        if notify:
            self._tray.show_notification(notify)

    # ── 主题 / 动画 ───────────────────────────────────────

    def _on_theme_selected(self, mode: str) -> None:
        AppTheme.set_mode(mode)
        AppConfig.save_theme_mode(AppTheme.mode())

    def _on_pet_animation_toggled(self, enabled: bool) -> None:
        AppConfig.save_animation_enabled(enabled)
        if enabled:
            self._window.start_collapsed_idle()

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

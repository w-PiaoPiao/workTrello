"""
归档对话框：查看已归档卡片并恢复

由控制器按需创建并保持引用；数据经 set_items 注入，
恢复操作通过 signal_restore_requested 交回控制器处理。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.i18n import tr
from app.models.board import BoardList, Card
from app.views.theme import AppTheme


class _ArchiveRow(QFrame):
    """归档行（行控件自持样式，主题回调可整行重刷）

    行样式是构建时的配色快照：主题切换时若不重下，已打开的归档对话框
    行面停留在旧主题（今日清单浮窗同类问题已有先例处理）。
    """

    def __init__(self, lst: BoardList, card: Card, on_restore):
        super().__init__()
        self.setObjectName("archiveRow")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(6)

        self._title = QLabel(f"{'✅ ' if card.done else ''}{card.title}")
        origin = QLabel(lst.title)
        self._origin = origin
        btn = QPushButton(tr("恢复"))
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda _=False, cid=card.id: on_restore(cid))

        lay.addWidget(self._title, 1)
        lay.addWidget(origin)
        lay.addWidget(btn)
        self.reapply_theme()

    def reapply_theme(self) -> None:
        c = AppTheme.colors()
        self.setStyleSheet(f"""
            QFrame#archiveRow {{
                background: {c['bg_card']};
                border: 1px solid {c['border']};
                border-radius: 8px;
            }}
        """)
        self._title.setStyleSheet(
            f"color: {c['text_primary']}; background: transparent;")
        self._origin.setStyleSheet(
            f"color: {c['text_secondary']}; font-size: 11px;"
            " background: transparent;")


class ArchiveDialog(QDialog):
    """归档查看器：统计头 + 归档卡片列表（可恢复）"""

    signal_restore_requested = Signal(str)   # card_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("归档"))
        self.setModal(False)
        self.setMinimumSize(440, 380)

        self._stats_label = QLabel()
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.addWidget(self._stats_label)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._rows_host = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(2, 2, 2, 2)
        self._rows_layout.setSpacing(6)
        self._scroll.setWidget(self._rows_host)
        root.addWidget(self._scroll, 1)

        self._close_btn = QPushButton(tr("关闭"))
        self._close_btn.setCursor(Qt.PointingHandCursor)
        self._close_btn.clicked.connect(self.close)
        root.addWidget(self._close_btn, 0, Qt.AlignRight)

        self._empty_label: QLabel | None = None
        self._last_items: list | None = None
        self._last_weekly = 0
        AppTheme.register(self.reapply_theme)
        self.reapply_theme()

    def retexts(self) -> None:
        """语言切换：标题/按钮/统计行刷新（有数据时整组重建行）"""
        self.setWindowTitle(tr("归档"))
        self._close_btn.setText(tr("关闭"))
        if self._last_items is not None:
            self.set_items(self._last_items, self._last_weekly)

    def set_items(self, items: list[tuple[BoardList, Card]],
                  weekly_done: int) -> None:
        """注入归档数据（items: (所属列表, 卡片)）并重建行"""
        self._last_items = list(items)
        self._last_weekly = weekly_done
        self._stats_label.setText(
            tr("共 {n} 张归档 · 本周完成 {m} 张").format(
                n=len(items), m=weekly_done))

        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._empty_label = None

        if not items:
            empty = QLabel(tr("暂无归档卡片：右键卡片即可归档"))
            empty.setAlignment(Qt.AlignCenter)
            self._empty_label = empty
            self._apply_empty_style()
            self._rows_layout.addWidget(empty)
            return

        for lst, card in items:
            # 与今日清单行一致的"row card"卡面
            self._rows_layout.addWidget(
                _ArchiveRow(lst, card, self.signal_restore_requested.emit))

        self._rows_layout.addStretch(1)

    def _apply_empty_style(self) -> None:
        if self._empty_label is None:
            return
        self._empty_label.setStyleSheet(
            f"color: {AppTheme.colors()['text_disabled']};"
            " font-size: 12px; padding: 24px;")

    def reapply_theme(self) -> None:
        c = AppTheme.colors()
        self.setStyleSheet(f"""
            QDialog {{ background: {c['bg_primary']}; }}
            QLabel {{ color: {c['text_primary']}; }}
            QPushButton {{
                background: {c['bg_card']};
                color: {c['text_primary']};
                border: 1px solid {c['border']};
                border-radius: 8px;
                padding: 4px 12px;
            }}
            QPushButton:hover {{ background: {c['bg_hover']}; }}
        """)
        self._stats_label.setStyleSheet(
            f"color: {c['text_secondary']}; font-size: 12px;")
        self._apply_empty_style()
        # 行样式是构建时快照：主题切换时对现存行整行重刷
        for i in range(self._rows_layout.count()):
            w = self._rows_layout.itemAt(i).widget()
            if isinstance(w, _ArchiveRow):
                w.reapply_theme()

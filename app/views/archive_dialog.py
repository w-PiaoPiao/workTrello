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

from app.models.board import BoardList, Card
from app.views.theme import AppTheme


class ArchiveDialog(QDialog):
    """归档查看器：统计头 + 归档卡片列表（可恢复）"""

    signal_restore_requested = Signal(str)   # card_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("归档")
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

        close = QPushButton("关闭")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.close)
        root.addWidget(close, 0, Qt.AlignRight)

        AppTheme.register(self.reapply_theme)
        self.reapply_theme()

    def set_items(self, items: list[tuple[BoardList, Card]],
                  weekly_done: int) -> None:
        """注入归档数据（items: (所属列表, 卡片)）并重建行"""
        self._stats_label.setText(
            f"共 {len(items)} 张归档 · 本周完成 {weekly_done} 张")

        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        if not items:
            empty = QLabel("暂无归档卡片：右键卡片即可归档")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(
                f"color: {AppTheme.colors()['text_disabled']};"
                " font-size: 12px; padding: 24px;")
            self._rows_layout.addWidget(empty)
            return

        for lst, card in items:
            c = AppTheme.colors()
            row = QFrame()
            # 与今日清单行一致的"row card"卡面
            row.setStyleSheet(f"""
                QFrame {{
                    background: {c['bg_card']};
                    border: 1px solid {c['border']};
                    border-radius: 8px;
                }}
            """)
            lay = QHBoxLayout(row)
            lay.setContentsMargins(8, 4, 8, 4)
            lay.setSpacing(6)

            title = QLabel(f"{'✅ ' if card.done else ''}{card.title}")
            title.setStyleSheet(f"color: {AppTheme.colors()['text_primary']};")
            origin = QLabel(lst.title)
            origin.setStyleSheet(
                f"color: {AppTheme.colors()['text_secondary']}; font-size: 11px;")
            btn = QPushButton("恢复")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(
                lambda _=False, cid=card.id: self.signal_restore_requested.emit(cid))

            lay.addWidget(title, 1)
            lay.addWidget(origin)
            lay.addWidget(btn)
            self._rows_layout.addWidget(row)

        self._rows_layout.addStretch(1)

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

"""
卡片编辑对话框：标题 / 备注 / 标签色 / 截止日期 / 完成勾选
"""

from __future__ import annotations

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDateEdit,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from app.config import AppConfig
from app.models.board import Card
from app.views.theme import AppTheme


class LabelChip(QPushButton):
    """可勾选的标签色块"""

    def __init__(self, key: str, parent=None):
        self._key = key
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(34, 22)
        self.setCursor(Qt.PointingHandCursor)
        self.reapply()

    def key(self) -> str:
        return self._key

    def reapply(self) -> None:
        bg, fg = AppTheme.label_style(self._key)
        border = "2px solid " + fg if self.isChecked() else "1px solid transparent"
        self.setStyleSheet(f"""
            QPushButton {{
                background: {bg};
                border: {border};
                border-radius: 6px;
            }}
            QPushButton:hover {{ border: 2px solid {fg}; }}
        """)


class CardDialog(QDialog):
    """新建/编辑卡片对话框"""

    def __init__(self, card: Card | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑卡片" if card else "新建卡片")
        self.setModal(True)
        self.setFixedWidth(420)
        self._card = card

        c = AppTheme.colors()
        self.setStyleSheet(f"""
            QDialog {{ background: {c['bg_primary']}; }}
            QLabel[cap="true"] {{
                color: {c['text_secondary']};
                font-size: 12px;
                font-weight: bold;
            }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)

        # 标题
        cap = QLabel("标题")
        cap.setProperty("cap", True)
        root.addWidget(cap)
        self._title_edit = QLineEdit()
        if card:
            self._title_edit.setText(card.title)
        root.addWidget(self._title_edit)

        # 备注
        cap2 = QLabel("备注")
        cap2.setProperty("cap", True)
        root.addWidget(cap2)
        self._notes_edit = QPlainTextEdit()
        self._notes_edit.setFixedHeight(90)
        self._notes_edit.setPlaceholderText("补充说明、链接、清单…")
        if card:
            self._notes_edit.setPlainText(card.notes)
        root.addWidget(self._notes_edit)

        # 标签色
        cap3 = QLabel("标签")
        cap3.setProperty("cap", True)
        root.addWidget(cap3)
        labels_row = QHBoxLayout()
        labels_row.setSpacing(6)
        self._label_chips: list[LabelChip] = []
        for key in AppConfig.LABEL_COLORS:
            chip = LabelChip(key)
            if card and key in card.labels:
                chip.setChecked(True)
            chip.clicked.connect(self._on_chip_toggled)
            self._label_chips.append(chip)
            labels_row.addWidget(chip)
        labels_row.addStretch(1)
        root.addLayout(labels_row)

        # 截止日期 + 完成
        row = QGridLayout()
        row.setHorizontalSpacing(12)
        cap4 = QLabel("截止日期")
        cap4.setProperty("cap", True)
        row.addWidget(cap4, 0, 0)
        self._due_edit = QDateEdit()
        self._due_edit.setCalendarPopup(True)
        self._due_edit.setDisplayFormat("yyyy-MM-dd")
        self._due_edit.setCurrentSection(QDateEdit.MonthSection)
        if card and card.due_date:
            self._due_edit.setDate(QDate.fromString(card.due_date, "yyyy-MM-dd"))
        else:
            self._due_edit.setDate(QDate.currentDate())
        row.addWidget(self._due_edit, 1, 0)

        self._done_check = QCheckBox("标记为已完成")
        if card:
            self._done_check.setChecked(card.done)
        row.addWidget(self._done_check, 1, 1)
        root.addLayout(row)

        # 清除日期（点击后提交时 due_date 为 None，不再回填今天）
        self._due_cleared = False
        self._clear_due = QPushButton("清除日期")
        self._clear_due.setFlat(True)
        self._clear_due.setCursor(Qt.PointingHandCursor)
        self._clear_due.clicked.connect(self._on_clear_due)
        # 占位：放右下
        bottom = QHBoxLayout()
        bottom.addWidget(self._clear_due)
        bottom.addStretch(1)
        root.addLayout(bottom)

        # 按钮
        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("保存")
        ok.setDefault(True)
        ok.clicked.connect(self._on_save)
        ok.setStyleSheet(f"""
            QPushButton {{
                background: {c['accent']};
                color: white;
                border: none;
                border-radius: 8px;
                padding: 7px 22px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background: {c['accent_hover']}; }}
        """)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        root.addLayout(btns)

    def _on_chip_toggled(self) -> None:
        for chip in self._label_chips:
            chip.reapply()

    def _on_clear_due(self) -> None:
        self._due_cleared = True
        self._due_edit.setDate(QDate.currentDate())  # 控件必须有合法值，提交时忽略
        self._clear_due.setEnabled(False)

    def _on_save(self) -> None:
        title = self._title_edit.text().strip()
        if not title:
            self._title_edit.setFocus()
            return
        self.accept()

    # ── 结果 ──────────────────────────────────────────────

    def result_card(self) -> dict:
        """收集表单内容，返回卡片字段 dict（清除日期后 due_date 为 None）"""
        labels = [chip.key() for chip in self._label_chips if chip.isChecked()]
        return {
            "title": self._title_edit.text().strip(),
            "notes": self._notes_edit.toPlainText().strip(),
            "labels": labels,
            "due_date": None if self._due_cleared
            else self._due_edit.date().toString("yyyy-MM-dd"),
            "done": self._done_check.isChecked(),
        }

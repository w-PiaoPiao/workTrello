"""
卡片编辑对话框：标题 / 备注 / 标签色 / 截止日期 / 完成勾选
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QKeySequence, QShortcut
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
        super().__init__(parent)
        self._key = key
        self.setCheckable(True)
        self.setFixedSize(34, 22)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(AppConfig.LABEL_NAMES.get(key, key))
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

        # 备注（标题行右侧：快速插入当前时间，便于在备注里记进度）
        notes_header = QHBoxLayout()
        cap2 = QLabel("备注")
        cap2.setProperty("cap", True)
        notes_header.addWidget(cap2)
        notes_header.addStretch(1)
        self._insert_time_btn = QPushButton("⏱ 插入当前时间")
        self._insert_time_btn.setFlat(True)
        self._insert_time_btn.setCursor(Qt.PointingHandCursor)
        self._insert_time_btn.setToolTip("在备注光标处插入当前时间（如 09-08 14:30）")
        self._insert_time_btn.setStyleSheet(f"""
            QPushButton {{
                color: {c['accent']};
                font-size: 11px;
                background: transparent;
                border: none;
                padding: 2px 6px;
            }}
            QPushButton:hover {{
                background: {c['accent_soft']};
                border-radius: 6px;
            }}
        """)
        self._insert_time_btn.clicked.connect(self._on_insert_time)
        notes_header.addWidget(self._insert_time_btn)
        root.addLayout(notes_header)
        self._notes_edit = QPlainTextEdit()
        self._notes_edit.setFixedHeight(90)
        self._notes_edit.setPlaceholderText("补充说明、链接、清单…\n（记进度时点右上角「⏱ 插入当前时间」）")
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
        # 截止日期可选：新建/无日期默认"未设置"，提交 due_date=None，不再默认今天。
        # 未设置态显示灰色"未设置"文字（而非禁用的日期框），设置后才是日期选择框
        has_due = card is not None and bool(card.due_date)
        self._due_cleared = not has_due
        if has_due:
            self._due_edit.setDate(QDate.fromString(card.due_date, "yyyy-MM-dd"))
        else:
            self._due_edit.setDate(QDate.currentDate())  # 占位值，未设置态不显示
        self._due_none_label = QLabel("未设置")
        self._due_none_label.setStyleSheet(f"""
            QLabel {{
                color: {c['text_disabled']};
                font-size: 13px;
                font-style: italic;
                background: transparent;
            }}
        """)
        due_box = QHBoxLayout()
        due_box.setContentsMargins(0, 0, 0, 0)
        due_box.addWidget(self._due_none_label)
        due_box.addWidget(self._due_edit)
        row.addLayout(due_box, 1, 0)

        self._done_check = QCheckBox("标记为已完成")
        if card:
            self._done_check.setChecked(card.done)
        row.addWidget(self._done_check, 1, 1)

        self._star_check = QCheckBox("⭐ 加入今日聚焦")
        self._star_check.setToolTip("星标后卡片会出现在「今日聚焦」视图和桌宠角标中")
        if card:
            self._star_check.setChecked(card.starred)
        row.addWidget(self._star_check, 1, 2)
        root.addLayout(row)

        # 截止日期开关（清除 ⇄ 设置 双向；cleared 时提交 due_date 为 None）
        self._due_toggle_btn = QPushButton()
        self._due_toggle_btn.setFlat(True)
        self._due_toggle_btn.setCursor(Qt.PointingHandCursor)
        self._due_toggle_btn.clicked.connect(self._on_toggle_due)
        bottom = QHBoxLayout()
        bottom.addWidget(self._due_toggle_btn)
        bottom.addStretch(1)
        root.addLayout(bottom)
        self._apply_due_state()

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

        # 备注是多行编辑框，Enter 只换行、够不到"保存"默认键 →
        # 补 Ctrl+Return（macOS 另有 ⌘+Return）直接保存
        self._save_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        self._save_shortcut.activated.connect(self._on_save)
        if AppConfig.IS_MACOS:
            self._mac_save_shortcut = QShortcut(
                QKeySequence("Meta+Return"), self)
            self._mac_save_shortcut.activated.connect(self._on_save)

    def showEvent(self, event) -> None:
        """打开即聚焦标题框并全选（直接输入即可覆盖标题）"""
        super().showEvent(event)
        self._title_edit.setFocus()
        self._title_edit.selectAll()

    def _on_chip_toggled(self) -> None:
        for chip in self._label_chips:
            chip.reapply()

    def _apply_due_state(self) -> None:
        """按当前 _due_cleared 切换：未设置=灰字标签，已设置=日期选择框"""
        self._due_edit.setVisible(not self._due_cleared)
        self._due_none_label.setVisible(self._due_cleared)
        self._due_toggle_btn.setText(
            "设置日期" if self._due_cleared else "清除日期")

    def _on_toggle_due(self) -> None:
        """截止日期 清除 ⇄ 设置 双向切换"""
        self._due_cleared = not self._due_cleared
        self._apply_due_state()
        if not self._due_cleared:
            self._due_edit.setFocus()

    def _on_insert_time(self) -> None:
        """在备注光标处插入紧凑时间戳 MM-DD HH:MM（不占位置）"""
        stamp = datetime.now().strftime("%m-%d %H:%M")
        cursor = self._notes_edit.textCursor()
        cursor.insertText(stamp)
        self._notes_edit.setTextCursor(cursor)
        self._notes_edit.setFocus()

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
            "starred": self._star_check.isChecked(),
        }

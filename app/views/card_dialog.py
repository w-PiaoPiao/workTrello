"""
卡片编辑对话框：标题 / 备注 / 标签色 / 截止日期 / 完成勾选
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QDate, QSize, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
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


def _selector_button_style(c: dict) -> str:
    """单选小按钮（重复 / 优先级共用的样式）"""
    return f"""
        QPushButton {{
            background: {c['bg_card']};
            color: {c['text_secondary']};
            border: 1px solid {c['border']};
            border-radius: 6px;
            padding: 4px 10px;
            font-size: 12px;
        }}
        QPushButton:checked {{
            background: {c['accent_soft']};
            color: {c['accent']};
            border: 1.5px solid {c['accent']};
        }}
        QPushButton:hover {{
            border: 1.5px solid {c['accent']};
        }}
    """


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
        """下发本 chip 的完整配色（含 :checked 选中态）

        选中/取消由 Qt 伪态自动切换边框，点击时无需再重设样式表——
        此前每点一个 chip 会对全部 6 个 chip 重新拼接并 reparse。
        """
        bg, fg = AppTheme.label_style(self._key)
        # 色块内写标签首字：纯颜色区分对色盲用户不可达
        self.setText(AppConfig.LABEL_NAMES.get(self._key, self._key)[:1])
        self.setStyleSheet(f"""
            QPushButton {{
                background: {bg};
                color: {fg};
                font-size: 11px;
                font-weight: bold;
                border: 1px solid transparent;
                border-radius: 6px;
            }}
            QPushButton:checked {{ border: 2px solid {fg}; }}
            QPushButton:hover {{ border: 2px solid {fg}; }}
        """)


class CardDialog(QDialog):
    """新建/编辑卡片对话框

    宽高均可拖拽调整，并在关闭后记住尺寸（下次打开沿用）。
    此前宽度被 setFixedWidth(420) 锁死：最大宽=最小宽=420，只能纵向拉。
    """

    def __init__(self, card: Card | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑卡片" if card else "新建卡片")
        self.setModal(True)
        self._card = card
        self._apply_saved_size()

        c = AppTheme.colors()
        self.setStyleSheet(f"""
            QDialog {{ background: {c['bg_primary']}; }}
            QLabel[cap="true"] {{
                color: {c['text_secondary']};
                font-size: 12px;
                font-weight: bold;
            }}
            QLineEdit[error="true"] {{
                border: 1.5px solid {c['danger']};
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
        self._title_edit.textChanged.connect(self._clear_title_error)
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
                chip.setChecked(True)   # 选中态边框由 :checked 伪态自动切换
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

        self._star_check = QCheckBox("加入今日聚焦")
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

        # 重复周期：勾选完成时自动滚动截止日期到下一周期（配合截止日期使用）
        cap5 = QLabel("重复")
        cap5.setProperty("cap", True)
        root.addWidget(cap5)
        repeat_row = QHBoxLayout()
        repeat_row.setSpacing(6)
        self._repeat_choices: dict[str, QPushButton] = {}
        for key, name in (("never", "不重复"), ("daily", "每天"),
                          ("weekly", "每周")):
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(_selector_button_style(c))
            btn.clicked.connect(self._on_repeat_clicked)
            self._repeat_choices[key] = btn
            repeat_row.addWidget(btn)
        selected = (card.repeat if card is not None
                    and card.repeat in self._repeat_choices else "never")
        self._repeat_choices[selected].setChecked(True)
        repeat_row.addStretch(1)
        root.addLayout(repeat_row)

        # 优先级：今日聚焦内按 高 > 中 > 低 排序展示
        cap6 = QLabel("优先级")
        cap6.setProperty("cap", True)
        root.addWidget(cap6)
        priority_row = QHBoxLayout()
        priority_row.setSpacing(6)
        self._priority_choices: dict[int, QPushButton] = {}
        for key, name in ((0, "无"), (1, "高"), (2, "中"), (3, "低")):
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(_selector_button_style(c))
            btn.clicked.connect(self._on_priority_clicked)
            self._priority_choices[key] = btn
            priority_row.addWidget(btn)
        selected_p = (card.priority if card is not None
                      and card.priority in self._priority_choices else 0)
        self._priority_choices[selected_p].setChecked(True)
        priority_row.addStretch(1)
        root.addLayout(priority_row)

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

    # ── 尺寸（宽高可调 + 记住上次值）───────────────────────

    def _apply_saved_size(self) -> None:
        """恢复上次关闭时的尺寸；无记录则用默认值

        在布局构建前调用：QDialog 的 showEvent 只在控件未被显式 resize
        过时才 adjustSize，故此处 resize 后打开时会沿用该尺寸。
        """
        self.setMinimumSize(AppConfig.CARD_DIALOG_MIN_WIDTH,
                            AppConfig.CARD_DIALOG_MIN_HEIGHT)
        self.setMaximumSize(16777215, 16777215)   # 解除任何既有的宽高锁定
        size = AppConfig.get_card_dialog_size()
        if isinstance(size, QSize):
            w, h = size.width(), size.height()
        else:
            w, h = AppConfig.CARD_DIALOG_WIDTH, AppConfig.CARD_DIALOG_HEIGHT
        self.resize(*self._clamp_to_screen(w, h))

    @staticmethod
    def _clamp_to_screen(w: int, h: int) -> tuple[int, int]:
        """夹进屏幕可用区：显示器变小/拔掉外接屏后不留超大窗口"""
        screen = QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            w = min(w, avail.width())
            h = min(h, avail.height())
        return (max(AppConfig.CARD_DIALOG_MIN_WIDTH, w),
                max(AppConfig.CARD_DIALOG_MIN_HEIGHT, h))

    def done(self, result: int) -> None:
        """关闭/确定/取消统一出口：记住当前尺寸（X 关闭走 closeEvent → reject）"""
        AppConfig.save_card_dialog_size(self.size())
        super().done(result)

    def _on_repeat_clicked(self) -> None:
        """重复周期单选：被点击的成为唯一选中项"""
        for key, btn in self._repeat_choices.items():
            btn.setChecked(btn is self.sender())

    def _on_priority_clicked(self) -> None:
        """优先级单选：被点击的成为唯一选中项"""
        for key, btn in self._priority_choices.items():
            btn.setChecked(btn is self.sender())

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

    def _set_title_error(self, on: bool) -> None:
        """标题错误态红边（动态属性驱动，需重 polish 才生效）"""
        if self._title_edit.property("error") == on:
            return
        self._title_edit.setProperty("error", on)
        style = self._title_edit.style()
        style.unpolish(self._title_edit)
        style.polish(self._title_edit)

    def _clear_title_error(self) -> None:
        self._set_title_error(False)

    def _on_save(self) -> None:
        title = self._title_edit.text().strip()
        if not title:
            # 空标题不关闭：红边 + 聚焦明示原因（此前静默 return，按钮
            # 点了没反应像坏了）
            self._set_title_error(True)
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
            "repeat": next((k for k, b in self._repeat_choices.items()
                            if b.isChecked()), "never"),
            "priority": next((k for k, b in self._priority_choices.items()
                              if b.isChecked()), 0),
        }

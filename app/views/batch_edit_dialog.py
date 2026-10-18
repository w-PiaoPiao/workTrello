"""批量编辑对话框：对多选卡片一次改截止日期 / 优先级 / 标题

设计取舍：三项都用"不修改 / 动作"的下拉框而非复选框——多选中往往只有
一两项需要统一，明确写出"不修改"比"没勾选"更不容易误操作（勾选框的
默认态是灰的，用户很难从界面判断"没勾"到底是"不改"还是"忘了选"）。

对话框只负责收集意图，实际写入与撤销快照由控制器统一处理（同卡片编辑
对话框的 result 回传范式）。
"""

from __future__ import annotations

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from app.config import AppConfig
from app.i18n import priority_name, tr
from app.views import tokens
from app.views.controls import ComboBox, DateEdit
from app.views.quick_add_dialog import _input_dialog_style
from app.views.theme import AppTheme, primary_button_style

# 三项各自的"不修改"哨兵值（changes() 靠它决定回传哪些键）
_NO_CHANGE = "__keep__"


class BatchEditDialog(QDialog):
    """批量编辑：已选卡片的截止日期 / 优先级 / 标题改名"""

    def __init__(self, count: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("批量编辑"))
        self.setModal(True)
        self.setMinimumWidth(420)
        self._count = count

        root = QVBoxLayout(self)
        root.setContentsMargins(*tokens.dialog_margins(bottom_trim=2))
        root.setSpacing(8)

        hint = QLabel(tr("已选 {n} 张卡片 · 只应用改动过的项").format(n=count))
        hint.setObjectName("bulkHint")
        root.addWidget(hint)

        # ── 截止日期 ──
        cap_due = QLabel(tr("截止日期"))
        cap_due.setProperty("cap", True)
        root.addWidget(cap_due)
        due_row = QHBoxLayout()
        due_row.setSpacing(6)
        self._due_mode = ComboBox()
        cap_due.setBuddy(self._due_mode)
        self._due_mode.setAccessibleName(tr("截止日期"))
        self._due_mode.addItem(tr("不修改"), _NO_CHANGE)
        self._due_mode.addItem(tr("设为指定日期"), "set")
        self._due_mode.addItem(tr("清除日期"), "")
        self._due_mode.currentIndexChanged.connect(self._on_due_mode_changed)
        due_row.addWidget(self._due_mode)
        self._due_edit = DateEdit()
        self._due_edit.setDate(QDate.currentDate())
        due_row.addWidget(self._due_edit, 1)
        self._due_edit.setEnabled(False)
        root.addLayout(due_row)
        # 相对日期的快捷入口：批量改期最常落在"今天/明天/下周一"上
        quick = QHBoxLayout()
        quick.setSpacing(6)
        quick.addWidget(QLabel(" "))
        for label, offset in ((tr("今天"), 0), (tr("明天"), 1),
                              (tr("下周一"), None), (tr("一周后"), 7)):
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(
                lambda _=False, o=offset: self._set_due_offset(o))
            quick.addWidget(btn)
        quick.addStretch(1)
        root.addLayout(quick)

        # ── 优先级 ──
        cap_priority = QLabel(tr("优先级"))
        cap_priority.setProperty("cap", True)
        root.addWidget(cap_priority)
        self._priority_mode = ComboBox()
        cap_priority.setBuddy(self._priority_mode)
        self._priority_mode.setAccessibleName(tr("优先级"))
        self._priority_mode.addItem(tr("不修改"), _NO_CHANGE)
        for value in (0, 1, 2, 3):
            name = (tr("无") if value == 0
                    else "{} {}".format(AppConfig.PRIORITY_MARKS[value],
                                        priority_name(value)))
            self._priority_mode.addItem(name, str(value))
        root.addWidget(self._priority_mode)

        # ── 标题 ──
        cap_title = QLabel(tr("标题"))
        cap_title.setProperty("cap", True)
        root.addWidget(cap_title)
        self._rename_mode = ComboBox()
        cap_title.setBuddy(self._rename_mode)
        self._rename_mode.setAccessibleName(tr("标题"))
        self._rename_mode.addItem(tr("不修改"), _NO_CHANGE)
        self._rename_mode.addItem(tr("加前缀"), "prefix")
        self._rename_mode.addItem(tr("加后缀"), "suffix")
        self._rename_mode.addItem(tr("查找替换"), "replace")
        self._rename_mode.currentIndexChanged.connect(
            self._on_rename_mode_changed)
        root.addWidget(self._rename_mode)

        self._rename_box = QHBoxLayout()
        self._rename_box.setSpacing(6)
        self._find_edit = QLineEdit()
        self._find_edit.setPlaceholderText(tr("查找"))
        self._repl_edit = QLineEdit()
        self._repl_edit.setPlaceholderText(tr("替换为 / 前缀 / 后缀"))
        self._rename_box.addWidget(self._find_edit)
        self._rename_box.addWidget(self._repl_edit)
        root.addLayout(self._rename_box)
        self._find_edit.hide()
        self._repl_edit.setEnabled(False)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton(tr("取消"))
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        self._ok_btn = QPushButton(tr("应用"))
        self._ok_btn.setDefault(True)
        self._ok_btn.setCursor(Qt.PointingHandCursor)
        self._ok_btn.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(self._ok_btn)
        root.addLayout(btns)

        self._apply_controls_state()
        self.reapply_theme()

    # ── 交互 ──────────────────────────────────────────────

    def _set_due_offset(self, offset) -> None:
        """快捷日期按钮：切到"设为指定日期"并填入相对今天算出的日期"""
        from datetime import date, timedelta

        today = date.today()
        day = (today + timedelta(days=7 - today.weekday())
               if offset is None else today + timedelta(days=offset))
        idx = self._due_mode.findData("set")
        self._due_mode.setCurrentIndex(max(0, idx))
        self._due_edit.setDate(QDate.fromString(day.isoformat(), "yyyy-MM-dd"))
        self._apply_controls_state()

    def _on_due_mode_changed(self) -> None:
        self._apply_controls_state()

    def _on_rename_mode_changed(self) -> None:
        self._apply_controls_state()

    def _apply_controls_state(self) -> None:
        """按当前选择启用/禁用附属控件（禁用态比隐藏更少跳动）"""
        self._due_edit.setEnabled(self._due_mode.currentData() == "set")
        mode = self._rename_mode.currentData()
        is_replace = mode == "replace"
        active = mode not in (_NO_CHANGE, None)
        self._find_edit.setVisible(is_replace)
        self._repl_edit.setEnabled(active)
        self._repl_edit.setPlaceholderText(
            tr("替换为") if is_replace else tr("前缀/后缀内容"))

    # ── 结果 ──────────────────────────────────────────────

    def changes(self) -> dict:
        """只回传改动过的项；三项都没动则返回空 dict（控制器不留空撤销步）"""
        out: dict = {}
        due_mode = self._due_mode.currentData()
        if due_mode != _NO_CHANGE:
            out["due"] = ("" if due_mode == ""
                          else self._due_edit.date().toString("yyyy-MM-dd"))
        priority = self._priority_mode.currentData()
        if priority != _NO_CHANGE:
            out["priority"] = int(priority)
        rename_mode = self._rename_mode.currentData()
        if rename_mode not in (_NO_CHANGE, None):
            out["rename"] = {
                "mode": rename_mode,
                "find": self._find_edit.text(),
                "repl": self._repl_edit.text(),
            }
        return out

    # ── 主题 ──────────────────────────────────────────────

    def reapply_theme(self) -> None:
        c = AppTheme.colors()
        self.setStyleSheet(_input_dialog_style(c))
        self._ok_btn.setStyleSheet(primary_button_style(c))

"""快速添加 / 批量添加 / 新建列表的自定义输入对话框

QInputDialog 是系统样式，与精修过的 CardDialog 视觉落差明显；且速记
语法（明天 / 周五 / !P1 / #红）此前只在批量添加的提示文案里出现过，
快速添加这个最高频入口完全不可发现，也无法预览解析结果。这里统一
三处入口：单行输入带实时速记预览，多行输入带列表选择与行数统计。

两个对话框的样式都是构建期快照，故各自实现 reapply_theme 并注册主题
回调（弱引用持有，对话框销毁即自动摘除）——否则打开期间跟随系统切换
深浅色时，这里会停在旧配色，与已更新的输入框/勾选框混成半深半浅。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from app.config import AppConfig
from app.i18n import label_display, tr
from app.models.quick_syntax import parse_quick_input
from app.views.theme import AppTheme


def _fmt_fields(fields: dict) -> str:
    """速记解析结果 → 一行可读摘要（截止 / 优先级 / 标签）"""
    parts: list[str] = []
    if fields.get("due_date"):
        d = fields["due_date"]
        due = f"{int(d[5:7])}/{int(d[8:10])}" if len(d) >= 10 else d
        parts.append(tr("截止") + " " + due)
    priority = fields.get("priority")
    if priority:
        parts.append(AppConfig.PRIORITY_MARKS.get(priority, ""))
    if fields.get("labels"):
        names = [label_display(k) for k in fields["labels"]]
        parts.append(tr("标签") + " " + tr("、").join(names))
    return " · ".join(p for p in parts if p)


def _input_dialog_style(c: dict) -> str:
    """输入型对话框共用 QSS（caps / 提示 / 错误态 / 次按钮）"""
    return f"""
        QDialog {{ background: {c['bg_primary']}; }}
        QLabel[cap="true"] {{
            color: {c['text_secondary']};
            font-size: 12px;
            font-weight: bold;
        }}
        QLabel#syntaxTip, QLabel#bulkHint, QLabel#bulkCount {{
            color: {c['text_secondary']};
            font-size: 11px;
            background: transparent;
        }}
        QLabel#bulkCount[over="true"] {{ color: {c['danger']}; }}
        QLabel#syntaxHit {{
            color: {c['accent']};
            font-size: 11px;
            background: {c['accent_soft']};
            border-radius: 6px;
            padding: 3px 8px;
        }}
        QLabel#inputError {{
            color: {c['danger']};
            font-size: 11px;
            background: transparent;
        }}
        QLineEdit[error="true"], QPlainTextEdit[error="true"] {{
            border: 1.5px solid {c['danger']};
        }}
        QComboBox {{
            background: {c['bg_card']};
            color: {c['text_primary']};
            border: 1px solid {c['border']};
            border-radius: 8px;
            padding: 5px 10px;
        }}
        QPushButton {{
            background: {c['bg_card']};
            color: {c['text_primary']};
            border: 1px solid {c['border']};
            border-radius: 8px;
            padding: 6px 14px;
        }}
        QPushButton:hover {{ background: {c['bg_hover']}; }}
    """


def _primary_button_style(c: dict) -> str:
    """主操作按钮（添加 / 批量添加）"""
    return f"""
        QPushButton {{
            background: {c['accent']};
            color: white;
            border: none;
            border-radius: 8px;
            padding: 7px 22px;
            font-weight: bold;
        }}
        QPushButton:hover {{ background: {c['accent_hover']}; }}
    """


def _mark_error(widget) -> None:
    """错误态：红边 + 聚焦（与卡片对话框同一套反馈）"""
    widget.setProperty("error", True)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.setFocus()


def _clear_error(widget) -> None:
    if widget.property("error"):
        widget.setProperty("error", False)
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)


class QuickAddDialog(QDialog):
    """单行输入对话框（可选速记实时预览）

    Return 提交、Esc 取消；打开即聚焦全选。syntax_preview=True 时
    输入框下方实时显示速记解析结果（未命中任何语法时不显示）。
    ok_text 覆盖确认键文案——本对话框被"新建/重命名看板"复用，
    那里写"添加"是错的。
    """

    def __init__(self, title: str, label: str, placeholder: str = "",
                 syntax_preview: bool = False, parent=None,
                 initial_text: str = "", ok_text: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        # 无最小宽时对话框按内容收缩到 240px 上下，输入长句几乎看不到
        # 自己写了什么（卡片/设置对话框的窗口宽都在 460 以上）
        self.setMinimumWidth(360)
        self._syntax_preview = syntax_preview
        self._ok_btn = QPushButton(ok_text or tr("添加"))
        self._ok_btn.setDefault(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(8)

        cap = QLabel(label)
        cap.setProperty("cap", True)
        root.addWidget(cap)

        self._edit = QLineEdit()
        self._edit.setPlaceholderText(placeholder)
        if initial_text:
            self._edit.setText(initial_text)
        self._edit.returnPressed.connect(self._on_confirm)
        self._edit.textChanged.connect(self._on_text_changed)
        root.addWidget(self._edit)

        if syntax_preview:
            tip = QLabel(
                tr("速记：今天/明天/N天后/周X/9月20日 · !P1~!P3 · #红#蓝"))
            tip.setObjectName("syntaxTip")
            tip.setWordWrap(True)
            root.addWidget(tip)
        self._preview = QLabel()
        self._preview.setObjectName("syntaxHit")
        self._preview.hide()
        root.addWidget(self._preview)
        # 空输入反馈：此前 _on_confirm 静默 return，点"添加"毫无反应
        self._error = QLabel()
        self._error.setObjectName("inputError")
        self._error.hide()
        root.addWidget(self._error)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton(tr("取消"))
        cancel.clicked.connect(self.reject)
        self._ok_btn.clicked.connect(self._on_confirm)
        btns.addWidget(cancel)
        btns.addWidget(self._ok_btn)
        root.addLayout(btns)

        self.reapply_theme()
        AppTheme.register(self.reapply_theme)

    # ── 主题 ──────────────────────────────────────────────

    def reapply_theme(self) -> None:
        c = AppTheme.colors()
        self.setStyleSheet(_input_dialog_style(c))
        self._ok_btn.setStyleSheet(_primary_button_style(c))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._edit.setFocus()
        self._edit.selectAll()

    def _on_text_changed(self, text: str) -> None:
        if self._error.isVisible():
            self._error.hide()
        _clear_error(self._edit)
        self._update_preview(text)

    def _update_preview(self, text: str) -> None:
        if not self._syntax_preview or not text.strip():
            self._preview.hide()
            return
        _title, fields = parse_quick_input(text)
        summary = _fmt_fields(fields)
        if summary:
            self._preview.setText("→ " + summary)
            self._preview.show()
        else:
            self._preview.hide()

    def _on_confirm(self) -> None:
        if self.text().strip():
            self.accept()
            return
        _mark_error(self._edit)
        self._error.setText(tr("标题不能为空"))
        self._error.show()

    def text(self) -> str:
        return self._edit.text()


class BulkAddDialog(QDialog):
    """批量添加：每行一张卡（支持速记），列表选择内嵌一步完成

    此前是 QPlainTextEdit → 确定 → 再弹 QInputDialog 选列表两步走，
    系统样式也不统一；合并为一个对话框并在下方实时统计行数。
    """

    _MAX_LINES = 100

    def __init__(self, list_names: list[str], prefill: str = "",
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("批量添加卡片"))
        self.setModal(True)
        self._list_names = list_names
        self._ok_btn = QPushButton(tr("批量添加"))
        self._ok_btn.setDefault(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(8)

        hint = QLabel(tr("每行一张卡片，行内支持速记：明天 / 周五 / 3天后 /"
                         " 9月20日、!P1、#红"))
        hint.setObjectName("bulkHint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        # 计数与错误标签先建：_edit 的 textChanged 在 setPlainText(prefill)
        # 时就会回调 _update_count，晚建会 AttributeError（预填路径必现）
        self._count_label = QLabel()
        self._count_label.setObjectName("bulkCount")
        self._error = QLabel()
        self._error.setObjectName("inputError")
        self._error.hide()

        self._edit = QPlainTextEdit()
        self._edit.setPlaceholderText(
            tr("例：\n明天 交周报 !P1 #红\n周五 复盘会\n采购打印机"))
        self._edit.setFixedHeight(160)
        self._edit.textChanged.connect(self._on_text_changed)
        if prefill:
            self._edit.setPlainText(prefill)
        root.addWidget(self._edit)

        row = QHBoxLayout()
        list_cap = QLabel(tr("添加到列表："))
        list_cap.setProperty("cap", True)
        row.addWidget(list_cap)
        self._combo = QComboBox()
        self._combo.addItems(list_names)
        row.addWidget(self._combo, 1)
        row.addWidget(self._count_label)
        root.addLayout(row)
        root.addWidget(self._error)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton(tr("取消"))
        cancel.clicked.connect(self.reject)
        self._ok_btn.clicked.connect(self._on_confirm)
        btns.addWidget(cancel)
        btns.addWidget(self._ok_btn)
        root.addLayout(btns)

        # 备注框里 Return 换行，补 Ctrl+Return / ⌘+Return 提交
        save = QShortcut(QKeySequence("Ctrl+Return"), self)
        save.activated.connect(self._on_confirm)
        if AppConfig.IS_MACOS:
            save_meta = QShortcut(QKeySequence("Meta+Return"), self)
            save_meta.activated.connect(self._on_confirm)

        self.reapply_theme()
        AppTheme.register(self.reapply_theme)
        self._update_count()

    # ── 主题 ──────────────────────────────────────────────

    def reapply_theme(self) -> None:
        c = AppTheme.colors()
        self.setStyleSheet(_input_dialog_style(c))
        self._ok_btn.setStyleSheet(_primary_button_style(c))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._edit.setFocus()

    def _count_lines(self) -> int:
        return sum(1 for ln in self._edit.toPlainText().splitlines()
                   if ln.strip())

    def _on_text_changed(self) -> None:
        if self._error.isVisible():
            self._error.hide()
        _clear_error(self._edit)
        self._update_count()

    def _update_count(self) -> None:
        """行数统计：超出上限时显式告知只入库前 N 行

        此前上限只存在于控制器（accept 后 [:100]），界面照常显示
        "120 行"，用户以为全进去了。
        """
        n = self._count_lines()
        over = n > self._MAX_LINES
        if not n:
            self._count_label.setText("")
        elif over:
            self._count_label.setText(
                tr("共 {n} 行，仅添加前 {m} 行").format(
                    n=n, m=self._MAX_LINES))
        else:
            self._count_label.setText(tr("{n} 行").format(n=n))
        self._count_label.setProperty("over", over)
        style = self._count_label.style()
        style.unpolish(self._count_label)
        style.polish(self._count_label)

    def _on_confirm(self) -> None:
        if self._count_lines():
            self.accept()
            return
        _mark_error(self._edit)
        self._error.setText(tr("至少输入一行"))
        self._error.show()

    def text(self) -> str:
        return self._edit.toPlainText()

    def list_index(self) -> int:
        return max(0, self._combo.currentIndex())

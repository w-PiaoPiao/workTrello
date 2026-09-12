"""快速添加 / 批量添加 / 新建列表的自定义输入对话框

QInputDialog 是系统样式，与精修过的 CardDialog 视觉落差明显；且速记
语法（明天 / 周五 / !P1 / #红）此前只在批量添加的提示文案里出现过，
快速添加这个最高频入口完全不可发现，也无法预览解析结果。这里统一
三处入口：单行输入带实时速记预览，多行输入带列表选择与行数统计。
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
from app.models.quick_syntax import parse_quick_input
from app.views.theme import AppTheme


def _fmt_fields(fields: dict) -> str:
    """速记解析结果 → 一行可读摘要（截止 / 优先级 / 标签）"""
    parts: list[str] = []
    if fields.get("due_date"):
        d = fields["due_date"]
        if len(d) >= 10:
            parts.append(f"截止 {int(d[5:7])}/{int(d[8:10])}")
        else:
            parts.append(f"截止 {d}")
    priority = fields.get("priority")
    if priority:
        parts.append(AppConfig.PRIORITY_MARKS.get(priority, ""))
    if fields.get("labels"):
        names = [AppConfig.LABEL_NAMES.get(k, k) for k in fields["labels"]]
        parts.append("标签 " + "、".join(names))
    return " · ".join(p for p in parts if p)


class QuickAddDialog(QDialog):
    """单行输入对话框（可选速记实时预览）

    Return 提交、Esc 取消；打开即聚焦全选。syntax_preview=True 时
    输入框下方实时显示速记解析结果（未命中任何语法时不显示）。
    """

    def __init__(self, title: str, label: str, placeholder: str = "",
                 syntax_preview: bool = False, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self._syntax_preview = syntax_preview

        c = AppTheme.colors()
        self.setStyleSheet(f"""
            QDialog {{ background: {c['bg_primary']}; }}
            QLabel[cap="true"] {{
                color: {c['text_secondary']};
                font-size: 12px;
                font-weight: bold;
            }}
            QLabel#syntaxTip {{
                color: {c['text_secondary']};
                font-size: 11px;
                background: transparent;
            }}
            QLabel#syntaxHit {{
                color: {c['accent']};
                font-size: 11px;
                background: {c['accent_soft']};
                border-radius: 6px;
                padding: 3px 8px;
            }}
            QPushButton {{
                background: {c['bg_card']};
                color: {c['text_primary']};
                border: 1px solid {c['border']};
                border-radius: 8px;
                padding: 6px 14px;
            }}
            QPushButton:hover {{ background: {c['bg_hover']}; }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(8)

        cap = QLabel(label)
        cap.setProperty("cap", True)
        root.addWidget(cap)

        self._edit = QLineEdit()
        self._edit.setPlaceholderText(placeholder)
        self._edit.returnPressed.connect(self._on_confirm)
        self._edit.textChanged.connect(self._update_preview)
        root.addWidget(self._edit)

        if syntax_preview:
            tip = QLabel("速记：今天/明天/N天后/周X/9月20日 · !P1~!P3 · #红#蓝")
            tip.setObjectName("syntaxTip")
            tip.setWordWrap(True)
            root.addWidget(tip)
        self._preview = QLabel()
        self._preview.setObjectName("syntaxHit")
        self._preview.hide()
        root.addWidget(self._preview)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("添加")
        ok.setDefault(True)
        ok.clicked.connect(self._on_confirm)
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

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._edit.setFocus()
        self._edit.selectAll()

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
        self.setWindowTitle("批量添加卡片")
        self.setModal(True)
        self._list_names = list_names

        c = AppTheme.colors()
        self.setStyleSheet(f"""
            QDialog {{ background: {c['bg_primary']}; }}
            QLabel[cap="true"] {{
                color: {c['text_secondary']};
                font-size: 12px;
                font-weight: bold;
            }}
            QLabel#bulkHint {{
                color: {c['text_secondary']};
                font-size: 11px;
                background: transparent;
            }}
            QLabel#bulkCount {{
                color: {c['text_secondary']};
                font-size: 11px;
                background: transparent;
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
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(8)

        cap = QLabel("每行一张卡片，行内支持速记：明天 / 周五 / 3天后 /"
                     " 9月20日、!P1、#红")
        cap.setProperty("cap", True)
        cap.setWordWrap(True)
        root.addWidget(cap)

        self._edit = QPlainTextEdit()
        self._edit.setPlaceholderText(
            "例：\n明天 交周报 !P1 #红\n周五 复盘会\n采购打印机")
        self._edit.setFixedHeight(160)
        self._edit.textChanged.connect(self._update_count)
        if prefill:
            self._edit.setPlainText(prefill)
        root.addWidget(self._edit)

        row = QHBoxLayout()
        list_cap = QLabel("添加到列表：")
        list_cap.setProperty("cap", True)
        row.addWidget(list_cap)
        self._combo = QComboBox()
        self._combo.addItems(list_names)
        row.addWidget(self._combo, 1)
        self._count_label = QLabel()
        self._count_label.setObjectName("bulkCount")
        row.addWidget(self._count_label)
        root.addLayout(row)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("批量添加")
        ok.setDefault(True)
        ok.clicked.connect(self._on_confirm)
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

        # 备注框里 Return 换行，补 Ctrl+Return 提交
        save = QShortcut(QKeySequence("Ctrl+Return"), self)
        save.activated.connect(self._on_confirm)
        self._update_count()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._edit.setFocus()

    def _count_lines(self) -> int:
        return sum(1 for ln in self._edit.toPlainText().splitlines()
                   if ln.strip())

    def _update_count(self) -> None:
        n = self._count_lines()
        self._count_label.setText(f"{n} 行" if n else "")

    def _on_confirm(self) -> None:
        if self._count_lines():
            self.accept()

    def text(self) -> str:
        return self._edit.toPlainText()

    def list_index(self) -> int:
        return max(0, self._combo.currentIndex())

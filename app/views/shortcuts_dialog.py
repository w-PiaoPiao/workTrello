"""快捷键速查面板：按 ? 呼出（或菜单），双列对照展示

一次性对话框（每次打开现构造），文案取当前语言；快捷键按平台显示
⌘（macOS）或 Ctrl（Windows/Linux）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from app.config import AppConfig
from app.i18n import tr
from app.views.theme import AppTheme


def _mod() -> str:
    return "⌘" if AppConfig.IS_MACOS else "Ctrl"


def _shortcut_rows() -> list[tuple[str, str]]:
    """(操作, 按键) 行；顺序即展示顺序

    这里的每一行都必须在两个平台上真实注册（Ctrl+W / Ctrl+Shift+Z
    此前只在 macOS 侧存在，面板却对 Windows 用户照报无误）。
    """
    mod = _mod()
    click = tr("单击")
    redo = (f"{mod}+Shift+Z" if AppConfig.IS_MACOS
            else f"{mod}+Y / {mod}+Shift+Z")
    return [
        (tr("快速添加卡片"), f"{mod}+N"),
        (tr("搜索卡片"), f"{mod}+F"),
        (tr("撤销"), f"{mod}+Z"),
        (tr("重做"), redo),
        (tr("打开 / 编辑卡片"), tr("回车")),
        (tr("切换完成状态"), tr("空格")),
        (tr("卡片间移动焦点"), "↑ ↓ ← →"),
        (tr("保存卡片对话框"), f"{mod}+↩"),
        (tr("清空搜索 / 取消多选 / 收起看板"), "Esc"),
        (tr("收起为桌宠"), f"{mod}+W"),
        (tr("多选卡片"), f"{mod}+{click} / Shift+{click}"),
        (tr("快捷键速查"), "?"),
    ]


class ShortcutsDialog(QDialog):
    """快捷键速查（模态，一次性）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("快捷键"))
        self.setModal(True)
        self.setMinimumWidth(420)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(6)

        for action, key in _shortcut_rows():
            row = QFrame()
            row.setObjectName("scRow")
            lay = QHBoxLayout(row)
            lay.setContentsMargins(10, 6, 10, 6)
            lay.setSpacing(10)
            a = QLabel(action)
            a.setObjectName("scAction")
            a.setWordWrap(True)
            k = QLabel(key)
            k.setObjectName("scKey")
            lay.addWidget(a, 1)
            lay.addWidget(k, 0, Qt.AlignVCenter)
            root.addWidget(row)

        btns = QHBoxLayout()
        btns.addStretch(1)
        close = QPushButton(tr("完成"))
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.accept)
        btns.addWidget(close)
        root.addLayout(btns)

        self.reapply_theme()
        AppTheme.register(self.reapply_theme)

    def reapply_theme(self) -> None:
        """重下配色：样式是构建期快照，打开期间跟随系统换主题时需重刷"""
        c = AppTheme.colors()
        self.setStyleSheet(f"""
            QDialog {{ background: {c['bg_primary']}; }}
            QLabel#scAction {{
                color: {c['text_primary']};
                font-size: 13px;
                background: transparent;
            }}
            QLabel#scKey {{
                color: {c['accent']};
                font-size: 12px;
                font-weight: bold;
                background: {c['accent_soft']};
                border-radius: 6px;
                padding: 2px 8px;
            }}
            QFrame#scRow {{
                background: {c['bg_card']};
                border: 1px solid {c['border']};
                border-radius: 8px;
            }}
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

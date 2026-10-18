"""快捷键速查面板：按 ? 呼出（或菜单），单列分区展示

一次性对话框（每次打开现构造），文案取当前语言；快捷键按平台显示
⌘（macOS）或 Ctrl（Windows/Linux）。

为什么分两区
------------
面板此前只列键盘快捷键，**鼠标手势一行都没有**——而应用里有十来条纯鼠标
手势（点色条过滤、拖卡到归档/今日、拖文件建卡、拖列头搬列、悬停出 ✕ …），
它们只散落在各处 tooltip 里。这是可发现性问题的总闸门：不先知道 `?`，
用户就学不到任何东西。

键盘区再按作用域分三小节：全局快捷键随时可用，而 `1 2 3 0` / `M` /
方向键只在**卡片聚焦时**生效——混在一列里，用户分不清上下文（审计结论）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
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

from app.config import AppConfig
from app.i18n import tr
from app.views import tokens
from app.views.theme import AppTheme


def _mod() -> str:
    return "⌘" if AppConfig.IS_MACOS else "Ctrl"


def _keyboard_sections() -> list[tuple[str, list[tuple[str, str]]]]:
    """键盘区：(小节标题, [(操作, 按键), ...])

    小节按**作用域**分：全局 / 卡片上（卡片聚焦时）/ 卡片对话框。
    这里的每一行都必须在两个平台上真实注册（Ctrl+W / Ctrl+Shift+Z
    此前只在 macOS 侧存在，面板却对 Windows 用户照报无误）。
    """
    mod = _mod()
    click = tr("单击")
    redo = (f"{mod}+Shift+Z" if AppConfig.IS_MACOS
            else f"{mod}+Y / {mod}+Shift+Z")
    return [
        (tr("全局"), [
            (tr("快速添加卡片"), f"{mod}+N"),
            (tr("粘贴剪贴板文本建卡（每行一张）"), f"{mod}+V"),
            (tr("搜索卡片"), f"{mod}+F"),
            (tr("撤销"), f"{mod}+Z"),
            (tr("重做"), redo),
            (tr("清空搜索 / 取消多选 / 收起看板"), "Esc"),
            (tr("收起为桌宠"), f"{mod}+W"),
            (tr("快捷键速查"), "?"),
        ]),
        (tr("卡片上（先点一下卡片）"), [
            (tr("打开 / 编辑卡片"), tr("回车")),
            (tr("切换完成状态"), tr("空格")),
            (tr("卡片设优先级 高/中/低（0=清除）"), "1 2 3 0"),
            (tr("卡片移动到其他列表"), "M"),
            (tr("卡片间移动焦点"), "↑ ↓ ← →"),
            (tr("多选卡片"), f"{mod}+{click} / Shift+{click}"),
        ]),
        (tr("卡片对话框"), [
            (tr("保存卡片对话框"), f"{mod}+↩"),
        ]),
    ]


def _gesture_rows() -> list[tuple[str, str]]:
    """鼠标与手势区：(会发生什么, 怎么做)

    每条都从用户视角写"在哪做、会发生什么"——手势本身没有名字，
    只写"拖拽"等于没说。
    """
    return [
        (tr("按该标签过滤全板"), tr("点卡片左缘色条")),
        (tr("横向滚动看板（列内仍纵向滚卡片）"),
         tr("空白处 / 工具栏滚滚轮")),
        (tr("直接归档 / 加入今日"), tr("拖卡片到工具栏按钮")),
        (tr("每个文件建一张卡并挂为附件"), tr("从系统拖文件进列表")),
        (tr("重命名列表"), tr("双击列标题")),
        (tr("搬走整列"), tr("拖列头 / 列内空白")),
        (tr("搬动整个窗口"), tr("拖看板空白处")),
        (tr("落到折叠的列并自动展开"), tr("拖卡片到折叠列上")),
        (tr("删除卡片（✕ 在右上角）"), tr("悬停卡片")),
        (tr("预览备注；点徽章固定不关"), tr("悬停「有备注」徽章")),
        (tr("多选卡片；拖其中一张 = 整组搬"),
         f"{_mod()}+{tr('单击')} / Shift+{tr('单击')}"),
    ]


class ShortcutsDialog(QDialog):
    """快捷键速查（模态，一次性）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("快捷键"))
        self.setModal(True)
        self.setMinimumSize(460, 420)
        self.resize(480, 620)

        root = QVBoxLayout(self)
        root.setContentsMargins(
            *tokens.dialog_margins(bottom_trim=4, top=16))
        root.setSpacing(6)

        # 内容有 26+ 行，必须能滚：面板高度超过屏幕时否则下半截直接不可见
        scroll = QScrollArea()
        scroll.setObjectName("scScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        host.setObjectName("scHost")
        self._body = QVBoxLayout(host)
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSpacing(6)

        self._add_section(tr("键盘"))
        for title, rows in _keyboard_sections():
            self._add_group(title)
            for action, key in rows:
                self._add_row(action, key)
        self._add_section(tr("鼠标与手势"))
        for action, gesture in _gesture_rows():
            self._add_row(action, gesture)
        self._body.addStretch(1)
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

        self._hint = QLabel(tr("按 ? 随时呼出本面板"))
        self._hint.setObjectName("scHint")
        root.addWidget(self._hint)

        btns = QHBoxLayout()
        btns.addStretch(1)
        close = QPushButton(tr("关闭"))
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.accept)
        btns.addWidget(close)
        root.addLayout(btns)

        self.reapply_theme()
        AppTheme.register(self.reapply_theme)

    # ── 构建 ──────────────────────────────────────────────

    def _add_section(self, title: str) -> None:
        """分区大标题（键盘 / 鼠标与手势）"""
        label = QLabel(title)
        label.setObjectName("scSection")
        self._body.addWidget(label)

    def _add_group(self, title: str) -> None:
        """键盘区内的小节标题（全局 / 卡片上 / 卡片对话框）"""
        label = QLabel(title)
        label.setObjectName("scGroup")
        self._body.addWidget(label)

    def _add_row(self, action: str, key: str) -> None:
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
        k.setWordWrap(True)
        lay.addWidget(a, 1)
        lay.addWidget(k, 0, Qt.AlignVCenter)
        self._body.addWidget(row)

    # ── 主题 ──────────────────────────────────────────────

    def reapply_theme(self) -> None:
        """重下配色：样式是构建期快照，打开期间跟随系统换主题时需重刷

        按钮不设专属样式：快捷键速查是"看完就关"的对话框，按钮走
        AppTheme.global_qss() 的次级描边样式（与归档 / 日历对话框一致）。
        """
        c = AppTheme.colors()
        self.setStyleSheet(f"""
            QDialog {{ background: {c['bg_primary']}; }}
            QScrollArea#scScroll, QWidget#scHost {{
                background: transparent;
            }}
            QLabel#scSection {{
                color: {c['text_primary']};
                {tokens.font_css(15)};
                font-weight: bold;
                background: transparent;
            }}
            QLabel#scGroup {{
                color: {c['text_secondary']};
                {tokens.font_css(11)};
                font-weight: bold;
                letter-spacing: 1px;
                background: transparent;
            }}
            QLabel#scHint {{
                color: {c['text_secondary']};
                {tokens.font_css(11)};
                background: transparent;
            }}
            QLabel#scAction {{
                color: {c['text_primary']};
                {tokens.font_css(13)};
                background: transparent;
            }}
            QLabel#scKey {{
                color: {c['accent']};
                {tokens.font_css(12)};
                font-weight: bold;
                background: {c['accent_soft']};
                border-radius: {tokens.radius('chip')}px;
                {tokens.pad(2, 8)};
            }}
            QFrame#scRow {{
                background: {c['bg_card']};
                border: 1px solid {c['border']};
                border-radius: {tokens.radius('control')}px;
            }}
        """)

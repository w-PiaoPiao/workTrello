"""
归档对话框：查看已归档卡片并恢复

由控制器按需创建并保持引用；数据经 set_items 注入，
恢复操作通过 signal_restore_requested 交回控制器处理。
"""

from __future__ import annotations

from functools import cmp_to_key, lru_cache

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.config import AppConfig
from app.i18n import tr
from app.models.board import BoardList, Card
from app.views import icons, tokens
from app.views.controls import ComboBox
from app.views.theme import AppTheme


class _ElidedLabel(QLabel):
    """单行标签：宽度不足时省略号截断（归档标题 / 来源列表名）

    此前是普通 QLabel：最小宽度等于整段文本宽度，布局压不下去，长标题
    会把「恢复」按钮整个挤出可视区——归档了长标题卡片就等于恢复不了。
    sizeHint 由 Ignored 策略让位给布局（stretch 决定谁被压缩）。

    min_width 给"必须看得见"的标签留底：Ignored 策略 + stretch 0 的标签
    会被压缩到 0 宽彻底消失——来源列名就是这样在带长标题的行里不见的，
    而"按来源列排序"排的正是这个看不见的字段。
    """

    def __init__(self, text: str = "", parent=None, min_width: int = 0):
        super().__init__(text, parent)   # 先摆全文，首次布局后再按宽度截断
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored,
                           QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(min_width)
        self.setToolTip(text)

    def set_full_text(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._apply_elide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        if self.width() <= 0:      # 尚未参与布局：保持全文
            return
        fm = self.fontMetrics()
        self.setText(fm.elidedText(self._full, Qt.ElideRight, self.width()))


class _ArchiveRow(QFrame):
    """归档行（行控件自持样式，主题回调可整行重刷）

    行样式是构建时的配色快照：主题切换时若不重下，已打开的归档对话框
    行面停留在旧主题（今日清单浮窗同类问题已有先例处理）。
    """

    def __init__(self, lst: BoardList, card: Card, on_restore, on_purge):
        super().__init__()
        self.setObjectName("archiveRow")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(6)

        if card.done:
            # 完成标记改用矢量勾：✅ 是彩色 emoji，与同排的线性图标/文字
            # 不同调，且按平台渲染成不同字形与基线
            self._done_mark = QLabel()
            self._done_mark.setAccessibleName(tr("已完成"))
            self._done_mark.setPixmap(
                icons.pixmap("check", 16, "success"))
            lay.addWidget(self._done_mark, 0)
        else:
            self._done_mark = None
        self._title = _ElidedLabel(card.title)
        self._title.setObjectName("archiveTitle")
        # 来源列表名封顶：长列表名同样不得挤走「恢复」；下限保证它
        # 在带长标题的行里也看得见（排序维度之一，不能是隐形字段）。
        # 策略回落到 Preferred：Ignored 策略下布局会无视 minimumWidth，
        # 该标签仍会被压成一条缝（实测 52px 下限被压到 10px 上下）
        self._origin = _ElidedLabel(lst.title, min_width=52)
        self._origin.setObjectName("archiveOrigin")
        self._origin.setSizePolicy(QSizePolicy.Policy.Preferred,
                                   QSizePolicy.Policy.Preferred)
        self._origin.setMaximumWidth(120)
        btn = QPushButton(tr("恢复"))
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda _=False, cid=card.id: on_restore(cid))
        # 彻底删除：归档区是"已完成/已放弃"的沉淀池，攒久了必须能清理，
        # 否则只剩"全部恢复回看板"这一条出路
        self._del_btn = QPushButton(tr("删除"))
        self._del_btn.setObjectName("archiveDelete")
        self._del_btn.setCursor(Qt.PointingHandCursor)
        self._del_btn.setToolTip(tr("彻底删除这张卡片（可撤销）"))
        self._del_btn.clicked.connect(lambda _=False, cid=card.id: on_purge(cid))

        lay.addWidget(self._title, 1)
        lay.addWidget(self._origin, 0)
        lay.addWidget(btn)
        lay.addWidget(self._del_btn)
        self.reapply_theme()

    def reapply_theme(self) -> None:
        c = AppTheme.colors()
        if self._done_mark is not None:
            # 完成勾是 QPixmap 快照，主题切换时重画一次才不残留旧色
            self._done_mark.setPixmap(icons.pixmap("check", 16, "success"))
        self.setStyleSheet(f"""
            QFrame#archiveRow {{
                background: {c['bg_card']};
                border: 1px solid {c['border']};
                border-radius: {tokens.radius('control')}px;
            }}
            QFrame#archiveRow:hover {{ border: 1px solid {c['accent']}; }}
        """)
        self._title.setStyleSheet(
            f"color: {c['text_primary']}; background: transparent;")
        self._origin.setStyleSheet(
            f"color: {c['text_secondary']}; {tokens.font_css(11)};"
            " background: transparent;")
        # 删除键常态收敛为次要文本色，悬停才转危险红：一排红色按钮会让
        # "恢复"这个主操作在视觉上被压过去
        self._del_btn.setStyleSheet(f"""
            QPushButton#archiveDelete {{
                background: transparent;
                color: {c['text_secondary']};
                border: 1px solid transparent;
                border-radius: {tokens.radius('control')}px;
                {tokens.pad_preset(AppConfig.UI_PAD_SECONDARY)};
            }}
            QPushButton#archiveDelete:hover {{
                color: {c['danger']};
                border: 1px solid {c['danger']};
            }}
        """)


@lru_cache(maxsize=1)
def _collator():
    """系统语言排序器（取不到返回 None）"""
    try:
        from PySide6.QtCore import QCollator, QLocale
        coll = QCollator(QLocale())
        coll.setCaseSensitivity(Qt.CaseInsensitive)
        return coll
    except Exception:   # noqa: BLE001 — 缺 ICU/locale 数据时回退
        return None


def title_sort_key():
    """标题排序的 key 函数：优先按系统语言规则，退化时按小写字母序

    纯 `str.lower()` 对中文等于按 Unicode 码位排（"周报" 会排在 "月报"
    前），用户看来是乱的；QCollator 走系统 locale 的排序规则（中文按拼音）。
    排序规则依赖系统语言，因此用例只用 ASCII 标题断言具体顺序。
    """
    coll = _collator()
    if coll is None:
        return lambda text: text.lower()

    def _cmp(a: str, b: str) -> int:
        return coll.compare(a, b)

    return cmp_to_key(_cmp)


class ArchiveDialog(QDialog):
    """归档查看器：统计头 + 搜索/排序 + 归档卡片列表（可恢复/彻底删除）"""

    signal_restore_requested = Signal(str)   # card_id
    signal_purge_requested = Signal(str)     # card_id（彻底删除）
    signal_clear_requested = Signal()        # 清空归档

    # 排序维度：value → 比较键（缺省按传入顺序 = 看板顺序）
    _SORTS = ("default", "title", "origin", "done_first")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("归档"))
        self.setModal(False)
        self.setMinimumSize(520, 420)

        self._stats_label = QLabel()
        root = QVBoxLayout(self)
        root.setContentsMargins(*tokens.dialog_margins(bottom_trim=2))
        root.setSpacing(8)
        root.addWidget(self._stats_label)

        # 搜索 + 排序（归档攒多之后，翻找是主要动作）
        tools = QHBoxLayout()
        tools.setSpacing(8)
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText(tr("搜索归档卡片…"))
        self._search_edit.setAccessibleName(tr("搜索归档卡片…"))
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.textChanged.connect(lambda _t: self._render())
        tools.addWidget(self._search_edit, 1)
        # 排序下拉不设 accessibleName：需要一个"排序"词条，而 i18n 词典
        # 不在本阶段写作用域内（读屏靠条目文本本身播报当前值）
        self._sort_combo = ComboBox()   # 滚轮不改值（防悬停误触）
        self._sort_combo.setCursor(Qt.PointingHandCursor)
        for value, label in (("default", tr("默认顺序")),
                             ("title", tr("按标题")),
                             ("origin", tr("按来源列")),
                             ("done_first", tr("已完成在前"))):
            self._sort_combo.addItem(label, value)
        self._sort_combo.currentIndexChanged.connect(lambda _i: self._render())
        tools.addWidget(self._sort_combo)
        root.addLayout(tools)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._rows_host = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(2, 2, 2, 2)
        self._rows_layout.setSpacing(6)
        self._scroll.setWidget(self._rows_host)
        root.addWidget(self._scroll, 1)

        foot = QHBoxLayout()
        self._clear_btn = QPushButton(tr("清空归档"))
        self._clear_btn.setObjectName("archiveClear")
        self._clear_btn.setCursor(Qt.PointingHandCursor)
        self._clear_btn.setToolTip(tr("删除全部归档卡片（可撤销）"))
        self._clear_btn.clicked.connect(self._on_clear_clicked)
        foot.addWidget(self._clear_btn)
        foot.addStretch(1)
        self._close_btn = QPushButton(tr("关闭"))
        self._close_btn.setCursor(Qt.PointingHandCursor)
        self._close_btn.clicked.connect(self.close)
        foot.addWidget(self._close_btn)
        root.addLayout(foot)

        self._empty_label: QLabel | None = None
        self._empty_hint: QLabel | None = None
        self._last_items: list | None = None
        self._last_weekly = 0
        AppTheme.register(self.reapply_theme)
        self.reapply_theme()

    def retexts(self) -> None:
        """语言切换：标题/按钮/统计行刷新（有数据时整组重建行）"""
        self.setWindowTitle(tr("归档"))
        self._close_btn.setText(tr("关闭"))
        self._clear_btn.setText(tr("清空归档"))
        self._clear_btn.setToolTip(tr("删除全部归档卡片（可撤销）"))
        self._search_edit.setPlaceholderText(tr("搜索归档卡片…"))
        labels = {"default": tr("默认顺序"), "title": tr("按标题"),
                  "origin": tr("按来源列"), "done_first": tr("已完成在前")}
        for i in range(self._sort_combo.count()):
            value = self._sort_combo.itemData(i)
            if value in labels:
                self._sort_combo.setItemText(i, labels[value])
        if self._last_items is not None:
            self.set_items(self._last_items, self._last_weekly)

    def set_items(self, items: list[tuple[BoardList, Card]],
                  weekly_done: int) -> None:
        """注入归档数据（items: (所属列表, 卡片)）并重建行"""
        self._last_items = list(items)
        self._last_weekly = weekly_done
        self._render()

    # ── 过滤 / 排序 / 渲染 ────────────────────────────────

    def _visible_items(self) -> list[tuple[BoardList, Card]]:
        items = list(self._last_items or [])
        query = self._search_edit.text().strip().lower()
        if query:
            items = [(lst, c) for lst, c in items
                     if query in c.title.lower()
                     or query in (c.notes or "").lower()
                     or query in lst.title.lower()]
        mode = self._sort_combo.currentData() or "default"
        if mode == "title":
            key = title_sort_key()
            items.sort(key=lambda pair: key(pair[1].title))
        elif mode == "origin":
            key = title_sort_key()
            items.sort(key=lambda pair: key(pair[0].title))
        elif mode == "done_first":
            items.sort(key=lambda pair: not pair[1].done)   # 稳定排序
        return items

    def _render(self) -> None:
        items = self._visible_items()
        total = len(self._last_items or [])
        if self._search_edit.text().strip():
            self._stats_label.setText(
                tr("匹配 {n} / 共 {total} 张归档 · 本周完成 {m} 张").format(
                    n=len(items), total=total, m=self._last_weekly))
        else:
            self._stats_label.setText(
                tr("共 {n} 张归档 · 本周完成 {m} 张").format(
                    n=total, m=self._last_weekly))

        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._empty_label = None
        self._empty_hint = None

        if not items:
            # 空态两行：结论 + 下一步。此前只有一行结论，"怎么把卡片归档"
            # 与"归档后还能不能拿回来"都得用户自己猜
            empty = QLabel(tr("没有匹配的归档卡片")
                           if total else
                           tr("暂无归档卡片：右键卡片即可归档"))
            empty.setAlignment(Qt.AlignCenter)
            empty.setWordWrap(True)
            hint = QLabel(tr("换个关键词，或清空搜索框")
                          if total else
                          tr("归档后可以随时「恢复」回看板，或彻底删除"))
            hint.setAlignment(Qt.AlignCenter)
            hint.setWordWrap(True)
            self._empty_label = empty
            self._empty_hint = hint
            self._apply_empty_style()
            self._rows_layout.addWidget(empty)
            self._rows_layout.addWidget(hint)
            self._clear_btn.setEnabled(bool(total))
            return

        for lst, card in items:
            # 与今日清单行一致的"row card"卡面
            self._rows_layout.addWidget(
                _ArchiveRow(lst, card, self.signal_restore_requested.emit,
                            self.signal_purge_requested.emit))

        self._rows_layout.addStretch(1)
        self._clear_btn.setEnabled(True)

    def _on_clear_clicked(self) -> None:
        """清空归档：唯一一处保留模态确认的删除

        它一次抹掉整片归档区、且按钮就在「关闭」旁边；看板内的删除有
        撤销兜底、单卡删除也能立刻看出删了什么，"清空"则是按下就一片
        空白——给一次确认比事后让人去按 Ctrl+Z 更合适。
        """
        if not self._last_items:
            return
        answer = QMessageBox.question(
            self, tr("清空归档"),
            tr("彻底删除全部 {n} 张归档卡片？（可撤销）").format(
                n=len(self._last_items)))
        if answer == QMessageBox.Yes:
            self.signal_clear_requested.emit()

    def _apply_empty_style(self) -> None:
        if self._empty_label is None:
            return
        c = AppTheme.colors()
        self._empty_label.setStyleSheet(
            f"color: {c['text_disabled']};"
            f" {tokens.font_css(12)}; {tokens.pad_all(24)};")
        if self._empty_hint is not None:
            self._empty_hint.setStyleSheet(
                f"color: {c['text_disabled']}; {tokens.font_css(11)};"
                " background: transparent;")

    def reapply_theme(self) -> None:
        c = AppTheme.colors()
        self.setStyleSheet(f"""
            QDialog {{ background: {c['bg_primary']}; }}
            QLabel {{ color: {c['text_primary']}; }}
            QPushButton {{
                background: {c['bg_card']};
                color: {c['text_primary']};
                border: 1px solid {c['border']};
                border-radius: {tokens.radius('control')}px;
                {tokens.pad_preset(AppConfig.UI_PAD_SECONDARY)};
            }}
            QPushButton:hover {{ background: {c['bg_hover']}; }}
            QPushButton#archiveClear {{
                background: transparent;
                color: {c['text_secondary']};
                border: 1px solid {c['border']};
            }}
            QPushButton#archiveClear:hover {{
                color: {c['danger']};
                border: 1px solid {c['danger']};
            }}
            QPushButton#archiveClear:disabled {{
                color: {c['text_disabled']};
                border-color: {c['border']};
            }}
        """)
        self._stats_label.setStyleSheet(
            f"color: {c['text_secondary']}; {tokens.font_css(12)};")
        self._search_edit.setStyleSheet(f"""
            QLineEdit {{
                background: {c['bg_card']};
                color: {c['text_primary']};
                border: 1px solid {c['border']};
                border-radius: {tokens.radius('control')}px;
                {tokens.pad_preset(AppConfig.UI_PAD_INPUT)};
            }}
        """)
        self._apply_empty_style()
        # 行样式是构建时快照：主题切换时对现存行整行重刷
        for i in range(self._rows_layout.count()):
            w = self._rows_layout.itemAt(i).widget()
            if isinstance(w, _ArchiveRow):
                w.reapply_theme()

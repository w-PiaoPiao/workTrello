"""
卡片编辑对话框：标题 / 备注（Markdown 预览）/ 清单 / 标签色 / 截止日期 /
重复 / 优先级 / 工作目录 / 附件 / 完成勾选
"""

from __future__ import annotations

import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QDate, QSize, Qt, QUrl
from PySide6.QtGui import (
    QDesktopServices,
    QImage,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.config import AppConfig
from app.i18n import label_display, repeat_display, tr
from app.models.board import Card
from app.models.markdown_lite import render_markdown
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
        self.setToolTip(label_display(key))
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
        self.setText(label_display(self._key)[:1])
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
        self.setWindowTitle(tr("编辑卡片") if card else tr("新建卡片"))
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
        cap = QLabel(tr("标题"))
        cap.setProperty("cap", True)
        root.addWidget(cap)
        self._title_edit = QLineEdit()
        self._title_edit.textChanged.connect(self._clear_title_error)
        if card:
            self._title_edit.setText(card.title)
        root.addWidget(self._title_edit)

        # 备注（标题行右侧：插入当前时间 + Markdown 预览切换）
        self._flat_btn_qss = f"""
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
        """
        notes_header = QHBoxLayout()
        cap2 = QLabel(tr("备注"))
        cap2.setProperty("cap", True)
        notes_header.addWidget(cap2)
        notes_header.addStretch(1)
        self._insert_time_btn = QPushButton(tr("⏱ 插入当前时间"))
        self._insert_time_btn.setFlat(True)
        self._insert_time_btn.setCursor(Qt.PointingHandCursor)
        self._insert_time_btn.setToolTip(
            tr("在备注光标处插入当前时间（如 09-08 14:30）"))
        self._insert_time_btn.setStyleSheet(self._flat_btn_qss)
        self._insert_time_btn.clicked.connect(self._on_insert_time)
        notes_header.addWidget(self._insert_time_btn)
        self._preview_btn = QPushButton(tr("👁 预览"))
        self._preview_btn.setFlat(True)
        self._preview_btn.setCursor(Qt.PointingHandCursor)
        self._preview_btn.setToolTip(tr("按 Markdown 渲染备注预览"))
        self._preview_btn.setStyleSheet(self._flat_btn_qss)
        self._preview_btn.setCheckable(True)
        self._preview_btn.toggled.connect(self._on_toggle_preview)
        notes_header.addWidget(self._preview_btn)
        root.addLayout(notes_header)
        self._notes_edit = QPlainTextEdit()
        self._notes_edit.setFixedHeight(90)
        self._notes_edit.setPlaceholderText(
            tr("补充说明、链接、清单…\n支持 Markdown：# 标题 **加粗** - [ ] 待办\n（记进度时点右上角「⏱ 插入当前时间」）"))
        if card:
            self._notes_edit.setPlainText(card.notes)
        root.addWidget(self._notes_edit)
        # 预览（滚动区包裹，长文不出界；高度与编辑框一致）
        self._notes_preview_scroll = QScrollArea()
        self._notes_preview_scroll.setWidgetResizable(True)
        self._notes_preview_scroll.setFrameShape(QScrollArea.NoFrame)
        self._notes_preview_scroll.setFixedHeight(90)
        self._notes_preview_scroll.hide()
        self._notes_preview = QLabel()
        self._notes_preview.setWordWrap(True)
        self._notes_preview.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._notes_preview.setTextInteractionFlags(
            Qt.TextBrowserInteraction)
        self._notes_preview_scroll.setWidget(self._notes_preview)
        root.addWidget(self._notes_preview_scroll)

        # 清单：可勾选子任务（带进度统计，全部完成才算卡面全绿）
        cap_cl = QLabel(tr("清单"))
        cap_cl.setProperty("cap", True)
        root.addWidget(cap_cl)
        self._check_rows: list[tuple[QCheckBox, QLineEdit]] = []
        self._checklist_host = QWidget()
        self._checklist_layout = QVBoxLayout(self._checklist_host)
        self._checklist_layout.setContentsMargins(0, 0, 0, 0)
        self._checklist_layout.setSpacing(4)
        root.addWidget(self._checklist_host)
        cl_btn_row = QHBoxLayout()
        self._add_check_btn = QPushButton(tr("＋ 添加清单项"))
        self._add_check_btn.setFlat(True)
        self._add_check_btn.setCursor(Qt.PointingHandCursor)
        self._add_check_btn.setStyleSheet(self._flat_btn_qss)
        self._add_check_btn.clicked.connect(
            lambda: self._add_check_row("", False, focus=True))
        cl_btn_row.addWidget(self._add_check_btn)
        self._check_progress_label = QLabel()
        self._check_progress_label.setStyleSheet(
            f"color: {c['text_secondary']}; font-size: 11px;"
            " background: transparent;")
        cl_btn_row.addStretch(1)
        cl_btn_row.addWidget(self._check_progress_label)
        root.addLayout(cl_btn_row)
        if card:
            for item in card.checklist:
                self._add_check_row(item.get("text", ""),
                                    bool(item.get("done")))
        self._update_check_progress()

        # 标签色
        cap3 = QLabel(tr("标签"))
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
        cap4 = QLabel(tr("截止日期"))
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
        self._due_none_label = QLabel(tr("未设置"))
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

        self._done_check = QCheckBox(tr("标记为已完成"))
        if card:
            self._done_check.setChecked(card.done)
        row.addWidget(self._done_check, 1, 1)

        self._star_check = QCheckBox(tr("加入今日聚焦"))
        self._star_check.setToolTip(
            tr("星标后卡片会出现在「今日聚焦」视图和桌宠角标中"))
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
        cap5 = QLabel(tr("重复"))
        cap5.setProperty("cap", True)
        root.addWidget(cap5)
        repeat_row = QHBoxLayout()
        repeat_row.setSpacing(6)
        self._repeat_combo = QComboBox()
        self._repeat_combo.setCursor(Qt.PointingHandCursor)
        for key in AppConfig.REPEAT_ORDER:
            self._repeat_combo.addItem(repeat_display(key) or tr("不重复"), key)
        self._repeat_combo.currentIndexChanged.connect(
            self._on_repeat_changed)
        repeat_row.addWidget(self._repeat_combo)
        # 自定义间隔天数（仅 repeat=custom 时可见）
        self._repeat_interval_spin = QSpinBox()
        self._repeat_interval_spin.setRange(1, 365)
        self._repeat_interval_spin.setSuffix(tr(" 天"))
        self._repeat_interval_spin.setToolTip(tr("每 N 天重复一次"))
        self._repeat_interval_spin.setVisible(False)
        repeat_row.addWidget(self._repeat_interval_spin)
        repeat_row.addStretch(1)
        root.addLayout(repeat_row)
        selected = (card.repeat if card is not None
                    and card.repeat in AppConfig.REPEAT_ORDER else "never")
        idx = self._repeat_combo.findData(selected)
        self._repeat_combo.setCurrentIndex(max(0, idx))
        if card is not None:
            try:
                self._repeat_interval_spin.setValue(
                    max(1, int(card.repeat_interval)))
            except (TypeError, ValueError):
                pass
        self._on_repeat_changed(self._repeat_combo.currentIndex())

        # 优先级：今日聚焦内按 高 > 中 > 低 排序展示
        cap6 = QLabel(tr("优先级"))
        cap6.setProperty("cap", True)
        root.addWidget(cap6)
        priority_row = QHBoxLayout()
        priority_row.setSpacing(6)
        self._priority_choices: dict[int, QPushButton] = {}
        for key, name in ((0, tr("无")), (1, tr("高")), (2, tr("中")), (3, tr("低"))):
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

        # 工作目录（可选）：常在外接移动硬盘上，对话框只存路径不校验
        # 存在性——是否可达交给打开动作现场判断
        cap7 = QLabel(tr("工作目录"))
        cap7.setProperty("cap", True)
        root.addWidget(cap7)
        workdir_row = QHBoxLayout()
        workdir_row.setSpacing(6)
        self._workdir = (card.workdir if card else "")
        self._workdir_label = QLabel()
        self._workdir_browse_btn = QPushButton(tr("浏览…"))
        self._workdir_browse_btn.setFlat(True)
        self._workdir_browse_btn.setCursor(Qt.PointingHandCursor)
        self._workdir_clear_btn = QPushButton(tr("清除"))
        self._workdir_clear_btn.setFlat(True)
        self._workdir_clear_btn.setCursor(Qt.PointingHandCursor)
        self._workdir_browse_btn.setStyleSheet(self._flat_btn_qss)
        self._workdir_clear_btn.setStyleSheet(self._flat_btn_qss)
        self._workdir_browse_btn.clicked.connect(self._on_browse_workdir)
        self._workdir_clear_btn.clicked.connect(self._on_clear_workdir)
        workdir_row.addWidget(self._workdir_label, 1)
        workdir_row.addWidget(self._workdir_browse_btn)
        workdir_row.addWidget(self._workdir_clear_btn)
        root.addLayout(workdir_row)
        self._apply_workdir_state()

        # 附件（可选）：文件或粘贴图片；新附件仅记录来源路径，落库复制
        # 由控制器在保存时完成（对话框取消则不留任何文件）
        cap8 = QLabel(tr("附件"))
        cap8.setProperty("cap", True)
        root.addWidget(cap8)
        self._attachments: list[dict] = []
        if card:
            self._attachments = [dict(a) for a in card.attachments]
        self._attach_rows_host = QWidget()
        self._attach_rows_layout = QVBoxLayout(self._attach_rows_host)
        self._attach_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._attach_rows_layout.setSpacing(4)
        root.addWidget(self._attach_rows_host)
        attach_btn_row = QHBoxLayout()
        self._attach_add_btn = QPushButton(tr("📎 添加附件…"))
        self._attach_add_btn.setFlat(True)
        self._attach_add_btn.setCursor(Qt.PointingHandCursor)
        self._attach_add_btn.setStyleSheet(self._flat_btn_qss)
        self._attach_add_btn.clicked.connect(self._on_add_attachment)
        attach_btn_row.addWidget(self._attach_add_btn)
        self._attach_paste_btn = QPushButton(tr("📋 粘贴图片"))
        self._attach_paste_btn.setFlat(True)
        self._attach_paste_btn.setCursor(Qt.PointingHandCursor)
        self._attach_paste_btn.setToolTip(tr("把剪贴板中的图片存为卡片附件"))
        self._attach_paste_btn.setStyleSheet(self._flat_btn_qss)
        self._attach_paste_btn.clicked.connect(self._on_paste_image)
        attach_btn_row.addWidget(self._attach_paste_btn)
        attach_btn_row.addStretch(1)
        root.addLayout(attach_btn_row)
        self._rebuild_attach_rows()

        # 按钮
        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton(tr("取消"))
        cancel.clicked.connect(self.reject)
        ok = QPushButton(tr("保存"))
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

    def _on_repeat_changed(self, index: int) -> None:
        """重复周期切换：自定义时展开间隔天数输入"""
        key = self._repeat_combo.itemData(index)
        self._repeat_interval_spin.setVisible(key == "custom")

    # ── 清单（可勾选子任务）───────────────────────────────

    def _add_check_row(self, text: str, done: bool, focus: bool = False) -> None:
        """追加一行清单项（勾选框 + 文本框 + 删除钮）"""
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        check = QCheckBox()
        check.setChecked(done)
        check.toggled.connect(self._update_check_progress)
        edit = QLineEdit(text)
        edit.setPlaceholderText(tr("清单项内容"))
        edit.returnPressed.connect(
            lambda: self._add_check_row("", False, focus=True))
        remove = QPushButton("✕")
        remove.setFixedSize(22, 22)
        remove.setFlat(True)
        remove.setCursor(Qt.PointingHandCursor)
        remove.setToolTip(tr("删除该清单项"))
        remove.setStyleSheet(f"""
            QPushButton {{
                color: {AppTheme.colors()['text_secondary']};
                background: transparent;
                border: none;
                font-size: 10px;
            }}
            QPushButton:hover {{ color: {AppTheme.colors()['danger']}; }}
        """)
        remove.clicked.connect(lambda: self._remove_check_row(row))
        lay.addWidget(check)
        lay.addWidget(edit, 1)
        lay.addWidget(remove)
        self._checklist_layout.addWidget(row)
        self._check_rows.append((check, edit))
        row._pair_index = len(self._check_rows) - 1   # type: ignore[attr-defined]
        self._update_check_progress()
        if focus:
            edit.setFocus()

    def _remove_check_row(self, row: QWidget) -> None:
        pair = getattr(row, "_pair_index", None)
        if pair is None or not (0 <= pair < len(self._check_rows)):
            return
        check, edit = self._check_rows.pop(pair)
        # 摘除行内控件后销毁行壳，避免空 QWidget 壳残留
        row.setParent(None)
        row.deleteLater()
        check.setParent(None)
        check.deleteLater()
        edit.setParent(None)
        edit.deleteLater()
        # 序号重排（简单重挂索引）
        for i, (_c, e) in enumerate(self._check_rows):
            shell = e.parentWidget()
            if shell is not None:
                shell._pair_index = i   # type: ignore[attr-defined]
        self._update_check_progress()

    def _update_check_progress(self, *_args) -> None:
        done = sum(1 for c, _e in self._check_rows if c.isChecked())
        total = len(self._check_rows)
        self._check_progress_label.setText(
            tr("{done}/{total} 已完成").format(done=done, total=total)
            if total else "")

    def _collect_checklist(self) -> list[dict]:
        out: list[dict] = []
        for check, edit in self._check_rows:
            text = edit.text().strip()
            if text:
                out.append({"text": text, "done": check.isChecked()})
        return out

    # ── 附件 ──────────────────────────────────────────────

    def _rebuild_attach_rows(self) -> None:
        """按 _attachments 重建附件行（图标 + 名称 + 打开 + 移除）"""
        while self._attach_rows_layout.count():
            item = self._attach_rows_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for idx, att in enumerate(self._attachments):
            row = QWidget()
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(6)
            icon = "🖼" if att.get("is_image") else "📄"
            name = QLabel(f"{icon} {att.get('name', '')}")
            name.setStyleSheet(
                f"color: {AppTheme.colors()['text_primary']};"
                " font-size: 12px; background: transparent;")
            open_btn = QPushButton(tr("打开"))
            open_btn.setFlat(True)
            open_btn.setCursor(Qt.PointingHandCursor)
            open_btn.setStyleSheet(self._flat_btn_qss)
            open_btn.clicked.connect(
                lambda _=False, p=att.get("path", ""):
                QDesktopServices.openUrl(QUrl.fromLocalFile(p)))
            remove = QPushButton("✕")
            remove.setFixedSize(22, 22)
            remove.setFlat(True)
            remove.setCursor(Qt.PointingHandCursor)
            remove.setToolTip(tr("移除附件"))
            remove.setStyleSheet(f"""
                QPushButton {{
                    color: {AppTheme.colors()['text_secondary']};
                    background: transparent;
                    border: none;
                    font-size: 10px;
                }}
                QPushButton:hover {{ color: {AppTheme.colors()['danger']}; }}
            """)
            remove.clicked.connect(
                lambda _=False, i=idx: self._remove_attachment(i))
            lay.addWidget(name, 1)
            lay.addWidget(open_btn)
            lay.addWidget(remove)
            self._attach_rows_layout.addWidget(row)

    def _remove_attachment(self, index: int) -> None:
        if not (0 <= index < len(self._attachments)):
            return
        self._attachments.pop(index)
        self._rebuild_attach_rows()

    def _on_add_attachment(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("添加附件"), str(Path.home()))
        if not path:
            return
        p = Path(path)
        self._attachments.append({
            "id": uuid.uuid4().hex,
            "name": p.name,
            "path": str(p),
            "is_image": p.suffix.lower() in
            (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"),
            "pending": True,
        })
        self._rebuild_attach_rows()

    def _on_paste_image(self) -> None:
        """剪贴板图片 → 临时 PNG，保存时由控制器复制入库"""
        clipboard = QApplication.clipboard()
        image = clipboard.image() if clipboard is not None else QImage()
        if image.isNull():
            self._notify_empty_clipboard()
            return
        fd, tmp = tempfile.mkstemp(suffix=".png", prefix="petboard_paste_")
        import os
        os.close(fd)
        if not image.save(tmp, "PNG"):
            return
        self._attachments.append({
            "id": uuid.uuid4().hex,
            "name": tr("粘贴图片 {time}.png").format(
                time=datetime.now().strftime("%m%d-%H%M")),
            "path": tmp,
            "is_image": True,
            "pending": True,
        })
        self._rebuild_attach_rows()

    def _notify_empty_clipboard(self) -> None:
        from PySide6.QtWidgets import QToolTip
        QToolTip.showText(self.cursor().pos(), tr("剪贴板中没有图片"))

    # ── 备注 Markdown 预览 ────────────────────────────────

    def _on_toggle_preview(self, on: bool) -> None:
        if on:
            html = render_markdown(
                self._notes_edit.toPlainText(),
                AppTheme.colors()["text_secondary"])
            self._notes_preview.setText(html or
                                        tr("（无内容）"))
            self._notes_edit.hide()
            self._notes_preview_scroll.show()
        else:
            self._notes_preview_scroll.hide()
            self._notes_edit.show()

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

    def _apply_workdir_state(self) -> None:
        """已设置=中段省略的路径（tooltip 存全路径），未设置=灰斜体"未设置"；
        清除按钮仅在已设置时可见"""
        c = AppTheme.colors()
        if self._workdir:
            fm = self._workdir_label.fontMetrics()
            self._workdir_label.setText(
                fm.elidedText(self._workdir, Qt.ElideMiddle, 300))
            self._workdir_label.setToolTip(self._workdir)
            self._workdir_label.setStyleSheet(f"""
                QLabel {{
                    color: {c['text_secondary']};
                    font-size: 12px;
                    background: transparent;
                }}
            """)
        else:
            self._workdir_label.setText(tr("未设置"))
            self._workdir_label.setToolTip("")
            self._workdir_label.setStyleSheet(f"""
                QLabel {{
                    color: {c['text_disabled']};
                    font-size: 13px;
                    font-style: italic;
                    background: transparent;
                }}
            """)
        self._workdir_clear_btn.setVisible(bool(self._workdir))

    def _on_browse_workdir(self) -> None:
        """浏览选择工作目录；现有路径可达时以其为起始目录"""
        start = (self._workdir
                 if self._workdir and Path(self._workdir).is_dir()
                 else str(Path.home()))
        chosen = QFileDialog.getExistingDirectory(
            self, tr("选择工作目录"), start)
        if not chosen:
            return
        self._workdir = chosen
        self._apply_workdir_state()

    def _on_clear_workdir(self) -> None:
        self._workdir = ""
        self._apply_workdir_state()

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
            "repeat": self._repeat_combo.currentData() or "never",
            "repeat_interval": self._repeat_interval_spin.value(),
            "priority": next((k for k, b in self._priority_choices.items()
                              if b.isChecked()), 0),
            "workdir": self._workdir.strip(),
            "checklist": self._collect_checklist(),
            "attachments": [dict(a) for a in self._attachments],
        }

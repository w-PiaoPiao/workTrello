"""资金分配监视泳道导出：Excel 工作表（行=卡片，列=类型/备注/各环节）

列序取三条支线环节的并集——以常务会全序列为主干，把分管独有的
"财政二次去函"插在"会签下达文件"之前（与分管路径的实际位置一致），
保证三种牵头卡与配合卡在同一张表里对齐可比；卡片没有的环节留空。
单元格填环节的办理时间（日期），未办理/未填留空。
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.models.board import FUND_KIND_LABELS, FUND_STEP_TEMPLATES, BoardList

logger = logging.getLogger(__name__)

# 环节并集列序（见模块 docstring）：常务会前 15 环节 + 财政二次去函 + 尾段 3 环节
FUND_EXPORT_STEPS: list[str] = (
    FUND_STEP_TEMPLATES["regular"][:15]
    + ["财政二次去函"]
    + FUND_STEP_TEMPLATES["regular"][15:]
)

_HEADERS = ["卡片标题", "类型", "备注"] + FUND_EXPORT_STEPS


def build_rows(board_list: BoardList) -> list[list[str]]:
    """资金泳道 → 表格行；每行 [标题, 类型, 备注, 各环节办理日期]。

    跳过归档卡与未启用监视的卡片；配合分配的环节列全空。
    """
    rows: list[list[str]] = []
    for card in board_list.cards:
        if card.archived or not card.fund:
            continue
        fund = card.fund
        if fund.get("role") == "lead":
            kind = FUND_KIND_LABELS.get(fund.get("kind"), "")
            role = f"牵头·{kind}" if kind else "牵头"
        else:
            role = "配合分配"
        dates = {s.get("name"): (s.get("date") or "")
                 for s in fund.get("steps", [])}
        row = [card.title, role, card.notes]
        row += [dates.get(name, "") for name in FUND_EXPORT_STEPS]
        rows.append(row)
    return rows


def export_xlsx(board_list: BoardList, path: str | Path) -> int:
    """写入 Excel 工作簿，返回导出的卡片数。

    表头加粗、冻结首行与首列（滚动环节列时卡片标题恒可见）、列宽适配。
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    rows = build_rows(board_list)
    wb = Workbook()
    ws = wb.active
    ws.title = "资金分配监视"
    ws.append(_HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append(row)
    widths = [28, 14, 32] + [14] * len(FUND_EXPORT_STEPS)
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "B2"
    wb.save(path)
    return len(rows)

"""轻量 Markdown → HTML 渲染（供 QLabel 富文本展示）

QLabel 只支持 HTML4 子集且无排版引擎，这里做"够用就好"的子集：
- 块级：#~### 标题、- 列表（含 - [ ] / - [x] 待办）、> 引用、``` 代码块、--- 分隔线
- 行内：**加粗** *斜体* ~~删除线~~ `行内代码` [文字](链接) 裸链接

设计约束：
- 先整体 HTML 转义再逐条替换，正则都写死、不存在注入面
- 输出用 <br> 拼接（QLabel 无 <p> 边距概念，靠换行最稳）
- 纯函数无 Qt 依赖，可独立单测
"""

from __future__ import annotations

import html
import re

_RE_HEADING = re.compile(r"^(#{1,3})\s+(.*)$")
_RE_TODO_OPEN = re.compile(r"^[-*+]\s+\[\s?\](\s+)(.*)$", re.I)
_RE_TODO_DONE = re.compile(r"^[-*+]\s+\[[xX✓]\](\s+)(.*)$")
_RE_BULLET = re.compile(r"^[-*+]\s+(.*)$")
_RE_QUOTE = re.compile(r"^>\s?(.*)$")
_RE_HR = re.compile(r"^(-{3,}|\*{3,})$")
_RE_CODE_FENCE = re.compile(r"^```")
_RE_BOLD = re.compile(r"\*\*(.+?)\*\*")
_RE_ITALIC = re.compile(r"(?<!\*)\*([^*\s][^*]*?)\*(?!\*)")
_RE_STRIKE = re.compile(r"~~(.+?)~~")
_RE_CODE = re.compile(r"`([^`]+)`")
_RE_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_RE_URL = re.compile(
    r"(?<![\"'>=\w])(https?://[^\s<>\"']+)")


def _inline(text: str) -> str:
    """行内元素替换（输入已 HTML 转义）"""
    text = _RE_CODE.sub(r"<code>\1</code>", text)
    text = _RE_BOLD.sub(r"<b>\1</b>", text)
    text = _RE_ITALIC.sub(r"<i>\1</i>", text)
    text = _RE_STRIKE.sub(r"<s>\1</s>", text)
    text = _RE_LINK.sub(r'<a href="\2">\1</a>', text)
    text = _RE_URL.sub(r'<a href="\1">\1</a>', text)
    return text


def render_markdown(text: str, secondary_color: str = "") -> str:
    """Markdown 文本 → QLabel 可用的 HTML 片段

    secondary_color 供引用文字着色（传空则不染色）。
    """
    if not text.strip():
        return ""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    in_code = False
    for line in lines:
        if _RE_CODE_FENCE.match(line.strip()):
            in_code = not in_code
            continue
        esc = html.escape(line, quote=False)
        if in_code:
            if line.strip():
                out.append(f"<code>{esc}</code>")
            continue
        stripped = esc.strip()
        if not stripped:
            out.append("&nbsp;")
            continue
        m = _RE_HEADING.match(line)
        if m:
            level, body = len(m.group(1)), _inline(html.escape(m.group(2), quote=False))
            if level == 1:
                out.append(f"<b><big>{body}</big></b>")
            elif level == 2:
                out.append(f"<b>{body}</b>")
            else:
                out.append(f"<b><small>{body}</small></b>")
            continue
        if _RE_HR.match(stripped):
            out.append("<hr>")
            continue
        m = _RE_TODO_DONE.match(line)
        if m:
            body = _inline(html.escape(m.group(2), quote=False))
            out.append(f"☑ <s>{body}</s>")
            continue
        m = _RE_TODO_OPEN.match(line)
        if m:
            out.append("☐ " + _inline(html.escape(m.group(2), quote=False)))
            continue
        m = _RE_BULLET.match(line)
        if m:
            out.append("• " + _inline(html.escape(m.group(1), quote=False)))
            continue
        m = _RE_QUOTE.match(line)
        if m:
            body = _inline(html.escape(m.group(1), quote=False))
            if secondary_color:
                out.append(f'<i><span style="color:{secondary_color};">'
                           f"{body}</span></i>")
            else:
                out.append(f"<i>{body}</i>")
            continue
        out.append(_inline(esc))
    return "<br>".join(out)

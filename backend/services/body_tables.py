"""表格表名规范化：**每个表格上方统一带一行表名**。

格式：`**表：<表名>**`（Markdown 加粗一行），紧贴表格上方。
- 已有表名（`**表：…**` / `表：…` / `表N …`）不重复添加
- 缺表名时按"所属节标题 + 该节第 N 张表"推断
- 表格定义：连续的 `|` 行，且第二行是分隔行（`| --- |`）
"""
from __future__ import annotations

import re

_CAPTION_RE = re.compile(r"^\s*\**\s*表\s*[：:0-9\-–]")
_HEADING_RE = re.compile(r"^#{2,6}\s+(.+?)\s*$")
_SEP_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


def ensure_table_captions(content: str) -> tuple[str, int]:
    """给缺少表名的表格上方插入 `**表：名**`。返回 (新正文, 插入条数)。"""
    lines = str(content or "").split("\n")
    out: list[str] = []
    added = 0
    section = ""
    table_in_section = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        head = _HEADING_RE.match(line.strip())
        if head:
            section = head.group(1).strip()
            table_in_section = 0
        is_table_start = (
            line.strip().startswith("|")
            and i + 1 < len(lines)
            and _SEP_RE.match(lines[i + 1])
        )
        if is_table_start:
            table_in_section += 1
            # 看上一行（跳过空行）是不是表名
            prev = -1
            for back in range(len(out) - 1, -1, -1):
                if out[back].strip():
                    prev = back
                    break
            has_caption = prev >= 0 and bool(_CAPTION_RE.match(out[prev].strip()))
            if not has_caption:
                name = section or "表格"
                if table_in_section > 1:
                    name = f"{name}（{table_in_section}）"
                if out and out[-1].strip():
                    out.append("")
                out.append(f"**表：{name}**")
                out.append("")
                added += 1
        out.append(line)
        i += 1
    return "\n".join(out), added

"""正文与大纲对齐 + 表格规范化。

产品口径：正文结构必须跟「章节大纲」一致（每节一个 `## 标题`），
不允许出现大纲外的旧标题、重复标题、不合规表格。
"""
from __future__ import annotations

import re

_HEADING_RE = re.compile(r"(?m)^##\s+(.+?)\s*$")
# 任何 >=2 级标题都视为"分节边界"（旧内容的 ### / #### 也要能被识别并移除）
_ANY_HEADING_RE = re.compile(r"(?m)^#{2,6}\s+(.+?)\s*$")


def _split_sections(content: str) -> tuple[str, list[tuple[str, str]]]:
    """拆成 (前言, [(标题, 该节正文)])。

    任何 >=2 级标题都算边界：`## 大纲节` 是正式节，`###/#### 旧标题` 会被
    align_to_outline 判为"大纲外内容"从而移除（内容先进 revision）。
    """
    text = str(content or "")
    marks = [(m.group(1).strip(), m.start(), m.end()) for m in _ANY_HEADING_RE.finditer(text)]
    if not marks:
        return text.strip(), []
    preface = text[: marks[0][1]].strip()
    sections = []
    for i, (title, start, end) in enumerate(marks):
        body_end = marks[i + 1][1] if i + 1 < len(marks) else len(text)
        sections.append((title, text[end:body_end].strip()))
    return preface, sections


def normalize_tables(content: str) -> tuple[str, int]:
    """修 Markdown 表：分隔行列数对齐表头、单元格里的 | 转义。返回 (新正文, 修复数)。"""
    lines = str(content or "").splitlines()
    out: list[str] = []
    fixed = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            width = len(header)
            body_rows = []
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                if len(cells) < width:
                    cells += [""] * (width - len(cells))
                elif len(cells) > width:
                    cells = cells[: width - 1] + [" ".join(cells[width - 1:])]
                body_rows.append("| " + " | ".join(cells) + " |")
                j += 1
            new_block = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * width) + " |"] + body_rows
            if new_block != lines[i:j]:
                fixed += 1
            out.extend(new_block)
            i = j
            continue
        out.append(line)
        i += 1
    return "\n".join(out), fixed


def align_to_outline(content: str, outline: list[dict]) -> tuple[str, dict]:
    """把正文重排为大纲顺序：保留同名节、丢弃大纲外旧内容（返回统计，不静默丢内容）。"""
    titles = [str(sec.get("title") or "").strip() for sec in outline if str(sec.get("title") or "").strip()]
    preface, sections = _split_sections(content)
    kept: dict[str, str] = {}
    dropped: list[str] = []
    for title, body in sections:                       # 同名只保留第一个非空
        if title in titles and title not in kept and body.strip():
            kept[title] = body.strip()
        else:
            dropped.append(title)
    parts: list[str] = []
    if preface:
        parts.append(preface)
    for title in titles:
        body = kept.get(title) or "（本节待写）"
        parts.append(f"## {title}\n\n{body}")
    rebuilt = "\n\n".join(parts).strip() + "\n"
    return rebuilt, {"kept": len(kept), "dropped": dropped, "sections": len(titles)}

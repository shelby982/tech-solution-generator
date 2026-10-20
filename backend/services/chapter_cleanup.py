"""清理正文里的"待核对/待补充"占位内容。

产品口径：**正文始终保持一份完整正常的内容**；事实缺口不进正文，
应留在待办/质检里。历史正文里由旧版合稿拼进来的
「### 待核对与补充」整段、以及纯占位的 `【待补充：xx】` 行，需要一次性清掉。
"""
from __future__ import annotations

import re

# 整段标题（可能是 ##/###）
_SECTION_RE = re.compile(r"(?m)^#{2,4}\s*待核对与补充\s*$")
# 纯占位行（整行只有一个占位符，允许前面有列表符号）
_PLACEHOLDER_LINE_RE = re.compile(r"(?m)^\s*(?:[-*+]\s*)?【待补充[：:][^】]*】\s*$")


def strip_pending_sections(content: str) -> tuple[str, list[str]]:
    """去掉"待核对与补充"整段 + 纯占位行，返回 (清理后正文, 被删掉的条目)。"""
    text = str(content or "")
    removed: list[str] = []
    lines = text.splitlines()
    kept: list[str] = []
    skipping = False
    for line in lines:
        if _SECTION_RE.match(line.strip()):
            skipping = True
            continue
        if skipping:
            # 遇到同级或更高级标题 → 段落结束
            if re.match(r"^#{1,4}\s+\S", line.strip()):
                skipping = False
            else:
                if _PLACEHOLDER_LINE_RE.match(line):
                    removed.append(line.strip())
                continue
        if _PLACEHOLDER_LINE_RE.match(line):
            removed.append(line.strip())
            continue
        kept.append(line)
    cleaned = "\n".join(kept)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, removed


def has_pending(content: str) -> bool:
    """正文里是否还有待核对痕迹（用于前端提示）。"""
    text = str(content or "")
    return bool(_SECTION_RE.search(text) or _PLACEHOLDER_LINE_RE.search(text))

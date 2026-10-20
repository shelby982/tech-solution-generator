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


async def record_generation_gaps(db, project_id: int, thread_id: str, block_id: str, gaps: list[str]) -> int:
    """把生成时的"事实缺口"落库成待办（chapter_gaps，与质检发现分开）。"""
    import hashlib

    saved = 0
    for gap in gaps or []:
        text = str(gap or '').strip()[:400]
        if len(text) < 6:
            continue
        fid = hashlib.sha1(f'{thread_id}|{block_id}|{text}'.encode()).hexdigest()[:16]
        await db.execute(
            "INSERT OR IGNORE INTO chapter_gaps (id, project_id, thread_id, block_id, message, status) "
            "VALUES (?, ?, ?, ?, ?, 'open')",
            (fid, project_id, thread_id, block_id, text),
        )
        saved += 1
    await db.commit()
    return saved


async def load_generation_gaps(db, thread_id: str, block_id: str) -> list[dict]:
    """读取某章"依据核对"待办（未处理的）。"""
    cursor = await db.execute(
        "SELECT id, message FROM chapter_gaps WHERE thread_id = ? AND block_id = ? AND status = 'open' ORDER BY created_at",
        (thread_id, block_id),
    )
    rows = await cursor.fetchall()
    return [{'id': r['id'], 'text': r['message']} for r in rows]


def looks_like_basis(text: str) -> bool:
    """缺口是否属"依据缺失"（政策/标准/数据/招标/验收/评分…）。"""
    return any(word in str(text or '') for word in _BASIS_KW)


_BASIS_KW = ('招标', '规范书', '标准', '政策', '数据', '原文', '依据', '测算', '验收', '评分', '条款', '清单')


def classify_placeholders(content: str) -> dict[str, list[str]]:
    """把正文里的【待补充：xx】分成两类。

    - basis：**依据缺失** → 进"待办·依据核对"
    - table：**表格待填**（占位在 Markdown 表格行内）→ 留在正文表格里，不进待办
    """
    import re as _re

    basis: list[str] = []
    table: list[str] = []
    for raw in str(content or '').splitlines():
        line = raw.strip()
        found = _re.findall(r'【待补充[：:]\s*([^】]+)】', line)
        if not found:
            continue
        in_table = line.startswith('|')
        for item in found:
            text = item.strip()
            if text:
                (table if in_table else basis).append(text)
    return {'basis': basis, 'table': table}

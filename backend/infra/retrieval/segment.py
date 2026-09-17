"""infra/retrieval/segment.py — 按提炼要求定位文档里的「区段」。

用户的提炼要求（「按照主招标文件中的标包2的技术评分要求的点…拆分章节」）说的是
**用文件里的哪一段**。实测一份 164 页采购文件里，标包2 的完整评分标准只有
5,757 字符（占全文 7.3%），其余是招标公告、投标人须知、标包1/3 的评审标准和
合同范本。

章级 BM25（``locate.py``）表达不了这个区段：它跨 34 个章节，而那些章节的标题里
一个「标包2」都没有——是评分表被切碎后的正文碎片。但文档本身有稳定的结构锚点
（「下列评审标准适用的标的/标包：标包2：高可靠技术专题研究与验证标包」），按
前缀词 + 编号就能切出整段。

所以：先抽出要求里的范围标识，再按同类锚点把全文切片，最后挑出编号匹配的段。
纯字符串，不调模型；抽不到标识或找不到锚点时返回 ``None``，调用方退回章级定位。
"""

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 范围标识：前缀式「标包2」与后缀式「第2标段」两种写法都认。
# 前后都锚不到词族时不会命中，所以「2.2.4」「2023年」「35人」不会被误判。
_SCOPE_RE = re.compile(
    r"(?:([0-9０-９一二三四五六七八九十]+)\s*(标段|标包|标的|分包))"
    r"|(?:(标段|标包|标的|分包)\s*([0-9０-９一二三四五六七八九十]+))"
)

# 锚点行用的词族（与 _SCOPE_RE 同一套）。裸「包」不在此列——它会命中「包装」。
_PREFIXES = ("标包", "标的", "标段", "分包")

_ANCHOR_RE = re.compile(
    rf"(?:{'|'.join(_PREFIXES)})\s*[0-9０-９一二三四五六七八九十]+\s*[：:]"
)

# 锚点行的标题进 prompt 时的截断长度。实测锚点行常把正文一起吃进来
# （「标包 2：高可靠技术专题研究与验证标包 商务评审要素相关材料…」）。
_ANCHOR_TITLE_CHARS = 40

_CN_DIGIT = {
    "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}


def _cn_to_int(s: str) -> int | None:
    """解析 0~99 的中文数字；超出范围返回 None（标包编号不会那么大）。"""
    if not s:
        return None
    if "十" in s:
        head, _, tail = s.partition("十")
        if head and head not in _CN_DIGIT:
            return None
        if tail and tail not in _CN_DIGIT:
            return None
        tens = _CN_DIGIT[head] if head else 1
        ones = _CN_DIGIT[tail] if tail else 0
        return tens * 10 + ones
    if len(s) == 1:
        return _CN_DIGIT.get(s)
    return None


def _normalize_number(raw: str) -> str:
    """全角数字与中文数字归一成 ASCII 数字串；解析不了返回空串。"""
    s = re.sub(r"\s+", "", raw or "")
    if not s:
        return ""
    s = "".join(
        chr(ord(ch) - 0xFEE0) if "０" <= ch <= "９" else ch
        for ch in s
    )
    if s.isdigit():
        return s.lstrip("0") or "0"
    value = _cn_to_int(s)
    return str(value) if value is not None else ""


def extract_scope_tokens(instruction: str) -> list[tuple[str, str]]:
    """从提炼要求里抽「范围标识」，如「标包2」→ ``("标包", "2")``。

    抽不到返回空列表 —— 调用方据此退回章级定位，行为与改造前一致。
    """
    tokens: list[tuple[str, str]] = []
    for m in _SCOPE_RE.finditer(instruction or ""):
        number = _normalize_number(m.group(1) or m.group(4))
        if not number:
            continue
        token = (m.group(2) or m.group(3), number)
        if token not in tokens:
            tokens.append(token)
    return tokens


# ─────────────────────────────────────────────
# 区段
# ─────────────────────────────────────────────

@dataclass
class Segment:
    """文档里的一个区段。

    selected=False 的段只把 ``title`` 交给模型看（见 build_spec_digest）。
    ``start``/``end`` 是在拼接全文里的字符偏移，用于日志与验收核对。
    """
    title: str
    content: str
    selected: bool = False
    start: int = 0
    end: int = 0


def _build_full(sections: list[dict]) -> str:
    """把各章正文按顺序拼成一份全文，锚点在这个坐标系里定位。

    只用 ``content`` 不用 ``title``：实测锚点行落在正文里（表格被解析器拆成
    碎片时，锚点连带前后文字一起落进某章的 raw_content）。
    """
    parts = []
    for s in sections:
        parts.append(s.get("content") or "")
    return "\n".join(parts)


def _find_anchors(full: str) -> list[tuple[int, str]]:
    """返回 ``(行首偏移, 锚点标题)``，同一行只算一处。"""
    anchors: list[tuple[int, str]] = []
    last_line_start = -1
    for m in _ANCHOR_RE.finditer(full):
        line_start = full.rfind("\n", 0, m.start()) + 1
        if line_start == last_line_start:
            continue
        last_line_start = line_start
        line_end = full.find("\n", m.end())
        if line_end == -1:
            line_end = len(full)
        # 标题从锚点匹配处开始，前面同行的表格碎片不要
        anchors.append((line_start, full[m.start():line_end].strip()[:_ANCHOR_TITLE_CHARS]))
    return anchors


def cut_segments(sections: list[dict]) -> list[Segment]:
    """按锚点把全文切成区段：第一个锚点之前、锚点之间、最后一个锚点之后。"""
    return _cut(_build_full(sections or []))


def _cut(full: str) -> list[Segment]:
    anchors = _find_anchors(full)
    if not anchors:
        return []

    segments: list[Segment] = []
    if anchors[0][0] > 0:
        segments.append(Segment(
            title="（文档开头）", content=full[:anchors[0][0]],
            start=0, end=anchors[0][0],
        ))
    for i, (start, title) in enumerate(anchors):
        end = anchors[i + 1][0] if i + 1 < len(anchors) else len(full)
        segments.append(Segment(title=title, content=full[start:end], start=start, end=end))
    return segments


def select_segments(
    segments: list[Segment],
    scope_tokens: list[tuple[str, str]],
) -> list[Segment]:
    """挑出编号匹配的段。先按「同前缀词 + 同编号」精确匹配，不中再放宽到只比编号。"""
    def _tokens_of(seg: Segment) -> list[tuple[str, str]]:
        return extract_scope_tokens(seg.title)

    wanted = set(scope_tokens)
    picked = [s for s in segments if wanted & set(_tokens_of(s))]
    if picked:
        return picked

    wanted_numbers = {n for _, n in scope_tokens}
    return [s for s in segments if wanted_numbers & {n for _, n in _tokens_of(s)}]


def locate_segments(
    sections: list[dict],
    instruction: str,
) -> list[Segment] | None:
    """按提炼要求定位区段，全文切成若干段、匹配的段标 ``selected=True``。

    sections: [{"title": str, "content": str, "level": int}, ...]

    返回 ``None`` 表示这条路走不通（要求里没有范围标识，或文档里没有同类锚点，
    或锚点编号都对不上），调用方应退回 ``locate_sections`` —— 退回去的行为与
    改造前完全一致，不会比现在更差。
    """
    scope_tokens = extract_scope_tokens(instruction)
    if not scope_tokens:
        return None

    full = _build_full(sections or [])
    segments = _cut(full)
    if not segments:
        logger.info("提炼要求里有范围标识，但文档没有同类锚点，退回章级定位")
        return None

    picked = select_segments(segments, scope_tokens)
    if not picked:
        logger.info(f"文档锚点里没有 {scope_tokens} 对应的区段，退回章级定位")
        return None

    for seg in picked:
        seg.selected = True

    selected_chars = sum(len(s.content) for s in picked)
    logger.info(
        f"提炼要求定位到 {len(picked)}/{len(segments)} 个区段"
        f"（选中 {selected_chars} / 全文 {len(full)} 字符）"
    )
    return segments


__all__ = [
    "Segment",
    "extract_scope_tokens",
    "cut_segments",
    "select_segments",
    "locate_segments",
]

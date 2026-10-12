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

# 「第一个锚点之前」那一段的标题。多文件时会被加上文件名后缀（见 cut_segments）。
_DOC_HEAD = "（文档开头）"
# 多文件时，完全没有锚点的那份文件整份作为一个**未选中**段，标题用这个前缀。
# 不带前缀（直接用文件名）会有一个副作用：文件名里恰好含「标包N」时会被范围匹配选中
# 而带进整份正文 —— 那种情况确实是用户点名的文件，但把标题与文件名分开更不容易误判。
_NO_ANCHOR = "（无匹配锚点）"

# ── 评分类别筛选 ──────────────────────────────
# 用户常写「标包2 的**技术**评分点」，而范围词只表达了"哪个标包"。
# 不加这一层，标包 2 的商务评审要素与技术评审要素会一起被选中，且**按文档顺序**
# 商务在前 → 模型据此组织出商务目录（实测：用户要技术评分点，出来 58 节全是商务项）。
# 只认这四类，且需与范围定位同现才生效。
_CATEGORY_HINTS: dict[str, tuple[str, ...]] = {
    "tech": ("技术评分标准", "技术评审要素", "技术标"),
    "commerce": ("商务评分标准", "商务评审要素", "商务标"),
    "price": ("投标报价评分标准", "报价评分", "价格评分"),
    "other": ("其他因素评分标准",),
}
_CATEGORY_WORDS = {"tech": "技术", "commerce": "商务", "price": "报价", "other": "其他"}


def extract_category(instruction: str) -> str:
    """从提炼要求里抽「评分类别」：tech / commerce / price / other，抽不到返回 ""。

    「技术招标文件」这类**文件名**不算类别选择（用户在说材料名），所以先把
    「XX文件」形式剥掉再判断，避免把"按技术招标文件拆"误判成"只要技术类"。
    """
    text = instruction or ""
    text = re.sub(r"[\u4e00-\u9fffA-Za-z0-9（）()]*文件", " ", text)
    for key, word in _CATEGORY_WORDS.items():
        if word in text:
            return key
    return ""

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


def _group_by_file(sections: list[dict]) -> list[tuple[str, list[dict]]]:
    """按 ``source_file`` 把章节分组，**保持出现顺序**。

    多份要求文件是拼接在一起的：``_build_full`` 拼出来的全文里没有任何文件边界，
    而最后一段的 end 是「全文末尾」—— 于是一份文件里的锚点段会把其后**所有文件**
    的正文整段吞进来（实测：选中段 20275 字符里只有约 200 属于该锚点）。
    按文件分组后分别切段，段就不可能跨文件。

    没有 ``source_file``（单文件、老 checkpoint）时天然只有一组，行为与改造前一致。
    """
    groups: list[tuple[str, list[dict]]] = []
    index: dict[str, int] = {}
    for s in sections:
        name = str(s.get("source_file") or "")
        if name not in index:
            index[name] = len(groups)
            groups.append((name, []))
        groups[index[name]][1].append(s)
    return groups


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
    """按锚点把全文切成区段：第一个锚点之前、锚点之间、最后一个锚点之后。

    **按文件分别切**（见 ``_group_by_file``）：段绝不跨文件。``start``/``end`` 仍然
    是拼接全文里的偏移（各文件的偏移累加），便于日志与验收核对。
    """
    sections = list(sections or [])
    groups = _group_by_file(sections)
    if len(groups) <= 1:
        return _cut(_build_full(sections))

    per_file: list[tuple[str, list[Segment], str, list[dict]]] = []
    for name, group in groups:
        text = _build_full(group)
        per_file.append((name, _cut(text), text, group))

    if not any(segs for _name, segs, _text, _grp in per_file):
        # 所有文件都没有同类锚点 → 与单文件语义一致，让调用方退回章级定位
        return []

    segments: list[Segment] = []
    offset = 0
    for name, segs, text, group in per_file:
        if segs:
            for seg in segs:
                seg.start += offset
                seg.end += offset
                # 多文件时「文档开头」要区分是哪一份，否则几份文件的头部段在日志/选择里同名
                if seg.title == _DOC_HEAD and name:
                    seg.title = f"{_DOC_HEAD}·{name}"
                segments.append(seg)
        else:
            # 这份文件没有任何锚点：**按章节各给一段**（全部未选中）。
            # 不补的话它在 digest 里彻底消失（模型连它有哪些章节都看不到）；
            # 按章节给则「其余部分」能列出它的章节清单，而正文不进预算
            # （未选中段只贡献标题 —— 见 build_spec_digest）。
            cursor = 0
            for s in group:
                content = s.get("content") or ""
                segments.append(Segment(
                    title=str(s.get("title") or f"{_NO_ANCHOR}·{name}"),
                    content=content,
                    start=offset + cursor,
                    end=offset + cursor + len(content),
                ))
                cursor += len(content) + 1      # +1 对应组内 join 的 "\n"
        # +1 对应 _build_full 里 join 的分隔符
        offset += len(text) + 1
    return segments


def _cut(full: str) -> list[Segment]:
    anchors = _find_anchors(full)
    if not anchors:
        return []

    segments: list[Segment] = []
    if anchors[0][0] > 0:
        segments.append(Segment(
            title=_DOC_HEAD, content=full[:anchors[0][0]],
            start=0, end=anchors[0][0],
        ))
    for i, (start, title) in enumerate(anchors):
        end = anchors[i + 1][0] if i + 1 < len(anchors) else len(full)
        segments.append(Segment(title=title, content=full[start:end], start=start, end=end))
    return segments


def select_segments(
    segments: list[Segment],
    scope_tokens: list[tuple[str, str]],
    category: str = "",
) -> list[Segment]:
    """挑出编号匹配的段。先按「同前缀词 + 同编号」精确匹配，不中再放宽到只比编号。

    ``category``（tech/commerce/price/other）：用户在要求里点明了评分类别时，
    只保留标题或正文命中该类别的段。标包级锚点切出来的段常同时覆盖商务与技术
    （同一节表格连着），不筛就按文档顺序把商务排前面，模型会据此组织出商务目录。
    """
    def _tokens_of(seg: Segment) -> list[tuple[str, str]]:
        return extract_scope_tokens(seg.title)

    wanted = set(scope_tokens)
    picked = [s for s in segments if wanted & set(_tokens_of(s))]
    if not picked:
        wanted_numbers = {n for _, n in scope_tokens}
        picked = [s for s in segments if wanted_numbers & {n for _, n in _tokens_of(s)}]

    if category and category in _CATEGORY_HINTS:
        # 按类别**排序**而不是过滤。过滤会误伤：真正含"技术评分标准"的那段，标题
        # 只有「标包2：…」（包级锚点切出来的），标题里根本没有类别词 —— 一筛就把它
        # 连同技术规范书一起丢掉（实测只剩 1024 字的评审要素材料）。
        # 排序则保住全部候选，只把命中该类别的排前面；digest 的预算优先给靠前的段，
        # 于是商务材料不再占住预算大头。
        labeled = [
            (
                any(w in (s.title or "") for w in _CATEGORY_HINTS[category]),
                any(w in (s.content or "") for w in _CATEGORY_HINTS[category]),
                s,
            )
            for s in picked
        ]
        if any(t or c for t, c, _ in labeled):
            labeled.sort(key=lambda item: (item[0], item[1]), reverse=True)
            logger.info(
                "评分类别「%s」：%d 段中标题命中 %d、正文命中 %d（命中者优先占预算）",
                _CATEGORY_WORDS.get(category, category), len(picked),
                sum(1 for t, _, _ in labeled if t), sum(1 for _, c, _ in labeled if c),
            )
            return [s for _, _, s in labeled]
    return picked


def diagnose_scope_miss(sections: list[dict], instruction: str) -> str:
    """区段定位没成时，给用户一句**可操作**的原因（空串表示要求里根本没点范围）。

    只用于文案：调用方把它并进 ``spec.outline_error``，闸门 1 会展示。
    以前这里只有 logger.info —— 用户写「按标包2 拆」而文档写法不被覆盖时，
    目录实际按全文派生，界面零提示，用户以为范围生效了。
    """
    tokens = extract_scope_tokens(instruction)
    if not tokens:
        return ""
    pretty = "、".join(f"{prefix}{number}" for prefix, number in tokens)
    groups = _group_by_file(sections or [])
    if any(_find_anchors(_build_full(group)) for _name, group in groups):
        return f"要求文件里的锚点编号与「{pretty}」对不上"
    return f"要求文件里没有「标包/标段/标的/分包」这类可切分的锚点，无法按「{pretty}」定位"


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
    segments = cut_segments(sections)
    if not segments:
        logger.info("提炼要求里有范围标识，但文档没有同类锚点，退回章级定位")
        return None

    picked = select_segments(segments, scope_tokens, extract_category(instruction))
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
    "extract_category",
    "Segment",
    "diagnose_scope_miss",
    "extract_scope_tokens",
    "cut_segments",
    "select_segments",
    "locate_segments",
]

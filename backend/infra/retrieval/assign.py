"""素材 → 章节 归属（素材匹配阶段的引擎）。

**方向**：以章节为 corpus，为**每段素材**找它归属的章节 —— 与旧的"逐章检索 top-k + LLM 重排"
相反。这本来就是 ``keyword.assign_chunks_to_sections`` 的用途（注释写着"反向分配"），
但它此前只被 ``routes/materials.py`` import、**从未被调用**，实际跑的是反方向（调用数 = O(章节数)）。

两层，P0 全部**零模型调用**：

1. **规则/结构**：素材片段的首行标题 ↔ 章节标题。解析素材时写入的就是 ``f"{sec.title}\\n{content}"``
   （``routes/materials.py``），所以首行天然是切片标题，规则层可以直接用。
2. **语义**：BM25 反向分配（``assign_chunks_to_sections``）。章节文本 = 标题 + requirement +
   key_points + indicators（来自提炼的 8 字段）。

**低于阈值就不归属** —— 不硬塞弱关联结果。旧实现在没有素材时也会给每章塞 5 段"弱关联"评语，
既浪费 token 又给自造内容盖了"来自素材"的章。

产出 :class:`~domain.material.models.MaterialEvidence`：**带素材原文 + 出处 + 性质 + 来源 + 归属方式**，
供撰写阶段引用（而不是只给一句评语）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from domain.material.models import (
    BY_RULE,
    BY_SEMANTIC,
    KIND_BACKGROUND,
    KIND_FACT,
    KIND_SOLUTION,
    SOURCE_OURS,
    SOURCE_TENDER,
    MaterialEvidence,
)
from infra.retrieval.keyword import assign_chunks_to_sections

# 招标侧角色：这些文件是"要求"，可作背景/条款引用，**不可**充当"我方技术表述"
TENDER_ROLES = ("spec", "main_rfp", "requirement", "scoring", "evaluation")

# 章节文本预算：BM25 语料越长，单条片段的分数越被稀释
SECTION_TEXT_BUDGET = 1200

# ⚠️ 语料规模：rank_bm25 在**只有 2 个文档**时 IDF 恒为 0（log(N-df+0.5)-log(df+0.5) = 0），
# 语义层会整体失效。真实章节目录是 11~33 节，不受影响；只有 1~2 章的极简项目靠规则层与
# 后续 LLM 归属层兜底（单测夹具因此至少用 3 章）。
#
# 语义归属阈值（BM25 原始分）。实测标定（3 章语料）：
#   无关文本（食堂管理 / 公司简介）→ 0.0；同主题命中 → 0.9 ~ 1.8。
# 取 0.5 既收得住真实命中，又能把 0 分的无关片段挡在外面（宁缺毋滥）。
DEFAULT_MIN_SEMANTIC_SCORE = 0.5

# 规则命中要求的最小标题重叠度（2 字词的重叠比例）
MIN_TITLE_OVERLAP = 0.5

# 招标文件片段**允许挂载**的章节特征：只有"理解/背景"类章节可以把招标原文当作依据
# （方案、架构、指标等章节必须由我方素材支撑，否则应答会退化成复述招标要求 —— 见方案 §2.4）
TENDER_ALLOWED_TITLE_RE = re.compile(
    r"项目理解|需求理解|现状|背景|概述|总述|范围|目标|建设内容|痛点|必要性"
)

_PUNCT_RE = re.compile(r"[\s\-—_·:：,，.。;；、/\\()（）\[\]【】<>《》\"'“”‘’|+]+")
# 目录里常见的编号/层级前缀：第一章 / 1.2 / 一、/（三）/ 1)
# 编号前缀：第 3 章 / 3.2 / 一、/（三）/ 1)。
# - 允许"第 3 章"里第与数字之间有空格（原文常这么写）
# - 末位 (?![a-zA-Z]) 防止把 "5G 网络" 削成 "g网络"
# - 这个正则要在**去标点之前**跑，否则 "一、" 的顿号已经被清掉、前缀就认不出来了
# 注意分支顺序：多级编号（3.2）必须排在"数字+顿号"之前，否则 "3.2" 会被当成 "3." 只削掉一级
_NUMBER_PREFIX_RE = re.compile(
    r"^(?:第\s*[\d一二三四五六七八九十百]+\s*[章节节部分篇]"
    r"|\d+(?:\.\d+)*[.、]?(?![a-zA-Z])"
    r"|[\d一二三四五六七八九十]+\s*[、.)）])"
)


def normalize_title(text: str) -> str:
    """标题归一化：去括号补充说明 → 去标点空白 → 去编号前缀 → 转小写。

    顺序很重要：编号前缀要在**去标点之前**削（否则 "一、" 的顿号先被清掉、前缀认不出来）；
    而 "第 3 章" 这类带空格的写法由前缀正则自己容忍空格。两种写法都实测过。
    """
    s = (text or "").strip()
    s = s.splitlines()[0] if s else ""
    s = re.sub(r"[（(][^）)]*[）)]", "", s)          # 括号补充说明不参与匹配
    s = _NUMBER_PREFIX_RE.sub("", s.strip())        # 先削编号前缀（"一、" 的顿号这时还在）
    s = _PUNCT_RE.sub("", s)                        # 再清标点/空白
    return s.lower()


def chunk_title(content: str) -> str:
    """素材片段的首行标题（解析时写入），没有则空串。"""
    for line in (content or "").splitlines():
        line = line.strip()
        if line:
            return line[:60]
    return ""


def is_tender_role(role: str) -> bool:
    return (role or "").strip().lower() in TENDER_ROLES


def allows_tender_evidence(section_title: str) -> bool:
    """该章节是否允许引用招标文件原文（只有理解/背景类章节允许）。"""
    return bool(TENDER_ALLOWED_TITLE_RE.search(section_title or ""))


_FACT_HINT_RE = re.compile(
    r"(?:GB/?T\s*\d|标准|条款|协议|版本|接口|并发|时延|吞吐|TPS|QPS|可用率|容量|"
    r"\d+(?:\.\d+)?\s*(?:ms|s|秒|分钟|小时|天|%|万|亿|GB|TB|PB|个|台|节点|用户|并发))",
    re.I,
)
_SOLUTION_HINT_RE = re.compile(
    r"架构|方案|设计|部署|拓扑|分层|流程|实施|迁移|方法论|运维|服务|培训|组织|保障|演进", re.I
)
_BACKGROUND_HINT_RE = re.compile(r"背景|现状|需求|理解|概述|目标|范围|痛点|约束", re.I)


def classify_kind(content: str, *, source_kind: str = SOURCE_OURS) -> str:
    """素材性质：事实性 / 方案性 / 背景性（决定撰写阶段怎么用：可核 / 可引 / 可溯）。"""
    text = content or ""
    if _BACKGROUND_HINT_RE.search(text):
        return KIND_BACKGROUND
    if _FACT_HINT_RE.search(text):
        return KIND_FACT
    if _SOLUTION_HINT_RE.search(text):
        return KIND_SOLUTION
    # 招标侧文本没有"我方实现"含义，按背景性处理更安全
    return KIND_BACKGROUND if source_kind == SOURCE_TENDER else KIND_SOLUTION


def _tokens(text: str) -> list[str]:
    from infra.retrieval.keyword import tokenize

    return tokenize(text)


def _title_match(a: str, b: str) -> float:
    """章节标题与素材片段首行标题的匹配度（0 = 不命中）。

    两条防误挂的硬约束（实测踩过：招标文件的「技术评分表」被挂到「技术架构设计」）：
    1. 字符包含：一侧完整包含另一侧（各 ≥4 字）→ 高置信（"数据层设计" ⊂ "数据层设计说明"）
    2. 词重叠：**至少 2 个共同词**，且以较短一侧为分母 ≥ MIN_TITLE_OVERLAP
       —— 单个共同词往往是"技术/方案/要求"这类通用词，不构成归属证据。
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(a) >= 4 and len(b) >= 4 and (a in b or b in a):
        return 0.9
    ta, tb = set(_tokens(a)), set(_tokens(b))
    shared = ta & tb
    if len(shared) < 2:
        return 0.0
    shorter = min(len(ta), len(tb))
    ratio = len(shared) / shorter if shorter else 0.0
    return ratio if ratio >= MIN_TITLE_OVERLAP else 0.0


@dataclass
class AssignResult:
    """归属结果 + 统计（统计用于界面与验收指标，见方案 §4.D.5）。"""
    evidence: dict[str, list[MaterialEvidence]] = field(default_factory=dict)
    total_chunks: int = 0
    assigned_chunks: int = 0
    by_rule: int = 0
    by_semantic: int = 0
    unassigned_chunk_ids: list[int] = field(default_factory=list)

    @property
    def unassigned(self) -> int:
        return len(self.unassigned_chunk_ids)

    @property
    def zero_call_ratio(self) -> float:
        """零调用命中占比（规则 + 语义）。"""
        return (self.by_rule + self.by_semantic) / self.assigned_chunks if self.assigned_chunks else 0.0

    def stats(self) -> dict:
        return {
            "total_chunks": self.total_chunks,
            "assigned_chunks": self.assigned_chunks,
            "by_rule": self.by_rule,
            "by_semantic": self.by_semantic,
            "unassigned": self.unassigned,
            "chapters_with_evidence": sum(1 for v in self.evidence.values() if v),
            "zero_call_ratio": round(self.zero_call_ratio, 3),
        }


def build_section_texts(sections: list, outline_matrix: dict | None = None) -> list[str]:
    """章节文本 = 标题 + requirement + key_points + indicators（提炼 8 字段里与"要什么"最相关的三项）。"""
    matrix = outline_matrix or {}
    texts: list[str] = []
    for s in sections:
        sid = getattr(s, "id", None) or (s.get("id") if isinstance(s, dict) else "")
        title = getattr(s, "title", None) or (s.get("title") if isinstance(s, dict) else "") or ""
        row = matrix.get(sid) or {}
        if not isinstance(row, dict):
            row = getattr(row, "to_dict", lambda: {})()
        parts = [title, row.get("requirement") or "", row.get("key_points") or "",
                 row.get("indicators") or ""]
        text = "\n".join(p for p in parts if p)
        texts.append(text[:SECTION_TEXT_BUDGET])
    return texts


def assign_evidence(
    chunks: list[dict],
    sections: list,
    outline_matrix: dict | None = None,
    *,
    min_semantic_score: float = DEFAULT_MIN_SEMANTIC_SCORE,
    only_chunk_ids: set[int] | None = None,
    items_by_section: dict[str, list[dict]] | None = None,
) -> AssignResult:
    """把素材片段归属到章节。

    chunks: 每项至少含 ``id`` / ``content``，可选 ``material_id`` / ``filename`` / ``material_role``
    sections: ``Section``（或含 id/title 的 dict）列表，顺序即目录顺序
    outline_matrix: ``{section_id: row_dict}``，用于补充章节文本
    min_semantic_score: 语义归属阈值，低于它视为"未归属"（宁缺毋滥）
    only_chunk_ids: 只处理这些片段（增量：新上传的素材），其余保持原样不动
    items_by_section: ``{block_id: [应答要求条目…]}``（见 domain/spec/response_items.py）。
        给了就把每段素材挂到具体条目上（``item_ids``），挂不上则留空 = 只作章节级依据。
    """
    result = AssignResult()
    if not chunks or not sections:
        result.total_chunks = len(chunks or [])
        result.unassigned_chunk_ids = [int(c.get("id")) for c in (chunks or []) if c.get("id") is not None]
        return result

    section_ids = [
        getattr(s, "id", None) or (s.get("id") if isinstance(s, dict) else "")
        for s in sections
    ]
    section_titles = [
        normalize_title(getattr(s, "title", None) or (s.get("title") if isinstance(s, dict) else "") or "")
        for s in sections
    ]
    for sid in section_ids:
        result.evidence.setdefault(sid, [])

    todo = [c for c in chunks if only_chunk_ids is None or c.get("id") in only_chunk_ids]

    # ── ① 规则层：片段首行标题 ↔ 章节标题 ─────────────────────
    pending: list[dict] = []
    for c in todo:
        cid = c.get("id")
        content = c.get("content") or ""
        role = c.get("material_role") or ""
        source_kind = SOURCE_TENDER if is_tender_role(role) else SOURCE_OURS
        ct = normalize_title(chunk_title(content))
        best_idx, best_score = -1, 0.0
        for i, st in enumerate(section_titles):
            if not ct or not st:
                continue
            if source_kind == SOURCE_TENDER and not allows_tender_evidence(
                getattr(sections[i], "title", "") or (sections[i].get("title") if isinstance(sections[i], dict) else "")
            ):
                continue          # 招标原文不可作为"我方技术表述"的依据
            score = _title_match(ct, st)
            if score > best_score:
                best_idx, best_score = i, score
        if best_idx >= 0 and best_score >= MIN_TITLE_OVERLAP:
            result.evidence[section_ids[best_idx]].append(MaterialEvidence(
                chunk_id=cid,
                material_id=int(c.get("material_id") or 0),
                content=content,
                filename=c.get("filename") or "",
                locator=build_locator(c),
                page=int(c.get("page") or 0),
                kind=classify_kind(content, source_kind=source_kind),
                source_kind=source_kind,
                by=BY_RULE,
                confidence=float(round(min(1.0, best_score), 3)),
                score=float(best_score),
            ))
            result.by_rule += 1
        else:
            pending.append(c)

    # ── ② 语义层：BM25 反向分配（章节为 corpus）───────────────
    if pending:
        section_texts = build_section_texts(sections, outline_matrix)
        bm25, corpus_tokens = _section_index(section_texts)
        for c in pending:
            role = c.get("material_role") or ""
            source_kind = SOURCE_TENDER if is_tender_role(role) else SOURCE_OURS
            if source_kind == SOURCE_TENDER:
                # 招标片段只在"允许的章节"里参与语义归属：把不允许的章节分数压掉
                scores = _scores(c, corpus_tokens, bm25)
                if not scores:
                    continue
                for i, sec in enumerate(sections):
                    title = getattr(sec, "title", "") or (sec.get("title") if isinstance(sec, dict) else "")
                    if not allows_tender_evidence(title):
                        scores[i] = 0.0
                score = max(scores) if scores else 0.0
                best_idx = max(range(len(scores)), key=lambda i: scores[i]) if scores else -1
            else:
                score = _best_section_score(c, corpus_tokens, bm25)
                best_idx = _best_section_index(c, corpus_tokens, bm25)
            if score < min_semantic_score or best_idx < 0:
                continue                      # 宁缺毋滥：低于阈值不归属
            result.evidence[section_ids[best_idx]].append(MaterialEvidence(
                chunk_id=c.get("id"),
                material_id=int(c.get("material_id") or 0),
                content=c.get("content") or "",
                filename=c.get("filename") or "",
                locator=build_locator(c),
                page=int(c.get("page") or 0),
                kind=classify_kind(c.get("content") or "", source_kind=source_kind),
                source_kind=source_kind,
                by=BY_SEMANTIC,
                confidence=float(round(min(1.0, score / 10.0), 3)),
                score=float(score),
            ))
            result.by_semantic += 1

    # ── ③ 条目级挂载（可选，零调用）────────────────────────
    if items_by_section:
        for sid, evs in result.evidence.items():
            items = items_by_section.get(sid) or []
            if not items:
                continue
            for ev in evs:
                ev.item_ids = mount_items(ev.content, items)

    for evs in result.evidence.values():
        evs.sort(key=lambda e: (e.confidence, e.score), reverse=True)
    result.total_chunks = len(todo)
    result.assigned_chunks = result.by_rule + result.by_semantic
    assigned_ids = {
        e.chunk_id for evs in result.evidence.values() for e in evs
    }
    result.unassigned_chunk_ids = [c.get("id") for c in todo if c.get("id") not in assigned_ids]
    return result


def _section_index(section_texts: list[str]):
    """章节语料的 BM25 索引（**整轮只建一次** —— 每个片段重建一次是纯浪费）。"""
    from rank_bm25 import BM25Okapi

    corpus = [_tokens(t) for t in section_texts]
    if not any(corpus):
        return None, corpus
    return BM25Okapi(corpus), corpus


def _scores(chunk: dict, corpus_tokens: list[list[str]], bm25):
    if bm25 is None or not corpus_tokens:
        return []
    query = _tokens(chunk.get("content") or "")
    if not query:
        return []
    return list(bm25.get_scores(query))


# 条目挂载：素材片段 → 该章的哪条要求。阈值偏高是刻意的 —— 挂错条目比不挂更糟
# （撰写阶段会按条目逐条作答，挂错等于拿别的素材回答这条要求）。
MIN_ITEM_SCORE = 0.34

# "具体词"：数字+单位、标准号、协议/算法名、英文缩写 —— 命中它们比命中"技术/方案"这类通用词可信得多
_SPECIFIC_TOKEN_RE = re.compile(
    r"(?:GB/?T\s*\d+(?:\.\d+)*|\d+(?:\.\d+)?\s*(?:ms|s|秒|分钟|小时|天|%|万|亿|GB|TB|PB|个|台|节点|用户|并发|TPS|QPS))"
    r"|[A-Z]{2,}(?:[/-][A-Z0-9]+)*",
)
_GENERIC_TOKENS = {
    "技术", "方案", "要求", "项目", "系统", "设计", "实现", "相关", "进行", "提供", "支持",
    "能力", "服务", "内容", "部分", "以及", "包括", "通过", "具有", "采用", "满足",
    # 提炼产出的套话里高频出现的虚词/兜底词：命中它们等于没命中（见 _item_clauses）
    "说明", "具体", "如下", "以下", "其中", "相应", "本次", "上述", "等等", "情况",
}


# 参数单位/量纲签名：`2500 用户` 与 `2000 用户` 值不同但**同一个参数量纲**，
# 命中它比命中"技术/方案"这类通用词可信得多（实测：不加签名会把 0.9 分的真命中压到 0.3 而漏挂）。
_UNIT_SIG_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(ms|s|秒|分钟|小时|天|%|万|亿|GB|TB|PB|个|台|节点|用户|并发|TPS|QPS)",
    re.I,
)


def _specific_tokens(text: str) -> set[str]:
    text = text or ""
    tokens = {t.lower() for t in _SPECIFIC_TOKEN_RE.findall(text)}
    tokens |= {m.lower() for m in _UNIT_SIG_RE.findall(text)}
    return tokens


# 子句最短长度：太短的子句（"具体包括："）两边都容易"命中"，是噪声不是依据
MIN_CLAUSE_CHARS = 6


def _item_clauses(item_text: str, *, min_chars: int = MIN_CLAUSE_CHARS) -> list[str]:
    """条目的**实质子句**（按 ，、；： 拆）。

    为什么要拆：real 数据里一条要求常常是"数据安全管理：支持数据库审计、数据脱敏、
    数据水印、接口安全监测…"这样的长句。整条打分时，素材只提到"脱敏"也只能拿到
    很低的分（分母是被要求的所有词），于是**该条的素材挂不上去**。
    实测（真实项目 69 章 / 643 条要求 / 37 段素材）：整条打分只挂上 8 条，
    按实质子句打分挂上 21 条，且抽查top命中可解释（"数据安全管理"←数据安全治理方案）。
    """
    clauses = []
    for raw in re.split(r"[，,、；;：:]", item_text or ""):
        c = (raw or "").strip()
        if len(c) < min_chars:
            continue
        if not {w for w in _tokens(c) if w not in _GENERIC_TOKENS and len(w) > 1}:
            continue    # 全是"具体包括/相关内容"这类通用词：命中它等于没命中
        clauses.append(c)
    return clauses


def _overlap_score(content: str, item_text: str) -> float:
    c, t = content or "", item_text or ""
    if not c.strip() or not t.strip():
        return 0.0
    score = 0.0
    shared_specific = _specific_tokens(c) & _specific_tokens(t)
    score += 0.6 * min(1.0, len(shared_specific))
    item_tokens = {w for w in _tokens(t) if w not in _GENERIC_TOKENS and len(w) > 1}
    if item_tokens:
        overlap = len(item_tokens & set(_tokens(c))) / len(item_tokens)
        score += 0.6 * overlap
    return round(min(1.0, score), 3)


def item_match_score(content: str, item_text: str, *, split_clauses: bool = False) -> float:
    """素材片段与单条要求的匹配分（0~1）。规则型、零调用、可解释。

    ``split_clauses=True``：长条目按**实质子句**取最高分（挂载用这条路径，
    见 _item_clauses 的实测理由）。默认 False = 整条比对（阈值标定的基准）。
    """
    if not split_clauses:
        return _overlap_score(content, item_text)
    clauses = _item_clauses(item_text)
    if not clauses:
        return _overlap_score(content, item_text)
    return max(_overlap_score(content, c) for c in clauses)


def mount_items(content: str, items: list[dict], *, min_score: float = MIN_ITEM_SCORE) -> list[str]:
    """把一段素材挂到章节内的具体条目上（可多条；都不够格就返回空 = 只做章节级依据）。"""
    scored = [
        (item_match_score(content, it.get("text") or "", split_clauses=True), it.get("item_id"))
        for it in (items or [])
    ]
    hit = [iid for sc, iid in scored if iid and sc >= min_score]
    if not hit:
        return []
    # 只保留最高分那一档（避免一条素材糊到所有条目上）；同分可多条
    best = max(sc for sc, iid in scored if iid and sc >= min_score)
    return [iid for sc, iid in scored if iid and sc >= best - 1e-9]


def build_locator(chunk: dict) -> str:
    """出处：``P12 · 第 8 段 · 服务团队``（页码/段号/切片标题，有什么给什么）。"""
    parts: list[str] = []
    page = chunk.get("page")
    try:
        if page:
            parts.append(f"P{int(page)}")
    except (TypeError, ValueError):
        pass
    idx = chunk.get("chunk_index")
    if idx is not None:
        try:
            parts.append(f"第 {int(idx) + 1} 段")
        except (TypeError, ValueError):
            pass
    title = chunk_title(chunk.get("content") or "")
    if title:
        parts.append(title[:20])
    return " · ".join(parts) if parts else ""


def _best_section_score(chunk: dict, corpus_tokens: list[list[str]], bm25) -> float:
    """该片段对最匹配章节的 BM25 原始分（用于阈值与置信度）。"""
    scores = _scores(chunk, corpus_tokens, bm25)
    return float(max(scores)) if scores else 0.0


def _best_section_index(chunk: dict, corpus_tokens: list[list[str]], bm25) -> int:
    scores = _scores(chunk, corpus_tokens, bm25)
    if not scores:
        return -1
    return max(range(len(scores)), key=lambda i: scores[i])

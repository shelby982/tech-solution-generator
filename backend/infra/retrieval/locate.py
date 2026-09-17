"""infra/retrieval/locate.py — 按提炼要求定位文档中的相关章节。

用户的提炼要求（「按照主招标文件中的标包2的技术评分要求的点…拆分章节」）说的是
**用文件的哪一部分**，而不只是「目录怎么组织」。整份文件灌进 prompt 时，招标公告、
投标人资格、开标时间、合同范本会把真正要的那几章淹没——实测一份 164 页采购文件
解析出的目录里，标包2 的评分要求只是其中几十条。

所以先按关键词把相关子树挑出来，再交给目录派生节点。定位是纯检索、不调模型：
便宜、可单测，且失败时能安全退回全量。
"""

import logging

from .keyword import build_bm25_index, tokenize

logger = logging.getLogger(__name__)

# 参与打分的内容长度上限。定位主要看标题，正文只用来消歧，
# 全量正文分词在千级章节的文档上会明显变慢。
_CONTENT_CHARS = 300

# 标题重复次数 = 权重。标题命中比正文命中更能说明「这一章就是讲这个的」。
_TITLE_WEIGHT = 3

# 定位结果的上限。要求写得宽泛时命中会很多，取打分最高的这些条。
DEFAULT_MAX_SECTIONS = 120


def locate_sections(
    sections: list[dict],
    instruction: str,
    *,
    max_sections: int = DEFAULT_MAX_SECTIONS,
    title_weight: int = _TITLE_WEIGHT,
) -> list[int]:
    """按 ``instruction`` 定位相关章节，**按相关度从高到低**返回下标。

    返回相关度序而不是原文序，是因为下游 ``build_spec_digest`` 按传入顺序吃
    8000 字预算：相关章节若排在文档后段，原文序下会被预算整个切掉 ——
    实测标包2 的评审标准落在 325 章里的第 89~161 章，按原文序根本轮不到。

    sections: [{"title": str, "content": str, "level": int}, ...]

    定位不到、要求为空、或命中为空时，退回**原文序的全量下标** ——
    宁可用全量让下游自己截断，也不能因为定位失败把该有的章节丢掉。
    """
    if not sections:
        return []

    all_indices = list(range(len(sections)))

    query_tokens = tokenize(instruction or "")
    if not query_tokens:
        return all_indices

    corpus = [_tokens_of(s, title_weight) for s in sections]
    if not any(corpus):
        return all_indices

    index, _ = build_bm25_index_raw(corpus)
    if index is None:
        return all_indices

    scores = index.get_scores(query_tokens)
    hits = sorted(
        (i for i in all_indices if scores[i] > 0),
        key=lambda i: scores[i],
        reverse=True,
    )
    if not hits:
        logger.info("提炼要求未能定位到任何章节，退回全量目录")
        return all_indices

    hits = hits[:max_sections]
    ordered = _with_ancestors_inline(sections, hits)

    logger.info(
        f"提炼要求定位到 {len(ordered)}/{len(sections)} 个章节"
        f"（命中 {len(hits)} 条，上限 {max_sections}）"
    )
    return ordered


def _tokens_of(section: dict, title_weight: int) -> list[str]:
    title_tokens = tokenize(section.get("title") or "")
    content = (section.get("content") or "")[:_CONTENT_CHARS]
    return title_tokens * title_weight + tokenize(content)


def build_bm25_index_raw(corpus: list[list[str]]):
    """对已分好词的语料建 BM25 索引。

    ``keyword.build_bm25_index`` 接收的是 ``{"content": str}`` 形态的 chunks，
    这里手上已经是 token 列表，直接建索引省一次分词。
    """
    if not corpus or not any(corpus):
        return None, corpus
    try:
        from rank_bm25 import BM25Okapi

        return BM25Okapi(corpus), corpus
    except Exception as e:  # pragma: no cover - 依赖缺失等异常路径
        logger.warning(f"BM25 索引构建失败，退回全量目录：{e}")
        return None, corpus


def _with_ancestors_inline(sections: list[dict], hits: list[int]) -> list[int]:
    """把命中章节的上级插到它前面，保持「由外到内」的可读顺序。

    只有叶子命中时，派生的目录会缺少归属，所以沿 level 往回找最近的更高级章节；
    上级本身几乎不会命中（标题通常不含用户问的那些词），所以按需插入而不是预筛。
    """
    ordered: list[int] = []
    seen: set = set()
    for i in hits:
        for ancestor in _ancestor_chain(sections, i):
            if ancestor not in seen:
                ordered.append(ancestor)
                seen.add(ancestor)
        if i not in seen:
            ordered.append(i)
            seen.add(i)
    return ordered


def _ancestor_chain(sections: list[dict], index: int) -> list[int]:
    """从最外层到最内层，返回 index 的祖先下标。"""
    chain: list[int] = []
    level = sections[index].get("level") or 1
    j = index - 1
    while j >= 0 and level > 1:
        ancestor_level = sections[j].get("level") or 1
        if ancestor_level < level:
            chain.append(j)
            level = ancestor_level
        j -= 1
    chain.reverse()
    return chain

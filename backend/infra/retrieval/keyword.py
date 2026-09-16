"""infra/retrieval/keyword.py — BM25 + jieba 关键词检索。

移自 backend/services/retrieval.py 的 retrieve_chunks，重命名为 keyword_search 以与
infra/retrieval/rerank.py 区分。
"""
import jieba
from rank_bm25 import BM25Okapi


def _tokenize(text: str) -> list[str]:
    """jieba 中文分词，过滤单字符停用词和纯空白。"""
    return [w for w in jieba.cut(text) if len(w.strip()) > 1]


def tokenize(text: str) -> list[str]:
    """公开的分词入口，供需要在 BM25 之外做词重叠的调用方复用同一套分词规则。"""
    return _tokenize(text)


def assign_chunks_to_sections(
    chunks: list[dict],
    section_texts: list[str],
) -> dict[int, list[dict]]:
    """
    反向分配：以章节为 corpus，为每条 chunk 找出最匹配的 section_idx。
    返回 {section_idx: [chunks...]}，未命中任何章节的归到 key=-1。
    保证传入的每条 chunk 都会被分配到结果中的某个桶，不丢失。
    """
    if not chunks:
        return {}
    tokenized_sections = [_tokenize(t) for t in section_texts]
    if not tokenized_sections or not any(tokenized_sections):
        return {-1: list(chunks)}
    section_bm25 = BM25Okapi(tokenized_sections)

    assignment: dict[int, list[dict]] = {}
    for chunk in chunks:
        q = _tokenize(chunk["content"])
        if not q:
            assignment.setdefault(-1, []).append(chunk)
            continue
        scores = section_bm25.get_scores(q)
        best_idx = max(range(len(scores)), key=lambda i: scores[i])
        if scores[best_idx] > 0:
            assignment.setdefault(best_idx, []).append(chunk)
        else:
            assignment.setdefault(-1, []).append(chunk)
    return assignment


def build_bm25_index(chunks: list[dict]) -> tuple[BM25Okapi, list[list[str]]]:
    """构建 BM25 索引，返回 (index, tokenized_corpus)。"""
    tokenized_corpus = [_tokenize(c["content"]) for c in chunks]
    if not tokenized_corpus:
        return None, tokenized_corpus
    index = BM25Okapi(tokenized_corpus)
    return index, tokenized_corpus


def keyword_search(
    chunks: list[dict],
    query: str,
    top_k: int = 5,
    bm25_index: BM25Okapi | None = None,
) -> list[dict]:
    """
    BM25 + jieba 中文分词，返回 Top-K 相关 chunks。
    可传入预构建的 bm25_index 避免重复构建。
    """
    if not chunks:
        return []

    tokenized_query = _tokenize(query)
    if not tokenized_query:
        return chunks[:top_k]

    if bm25_index is None:
        bm25_index, _ = build_bm25_index(chunks)
        if bm25_index is None:
            return chunks[:top_k]

    scores = bm25_index.get_scores(tokenized_query)
    ranked_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    return [chunks[i] for i in ranked_indices[:top_k] if scores[i] > 0]

"""infra/retrieval/keyword.py — BM25 + jieba 关键词检索。

移自 backend/services/retrieval.py 的 retrieve_chunks，重命名为 keyword_search 以与
infra/retrieval/rerank.py 区分。services/retrieval.py 暂保留，Phase 7 删除。
"""
import jieba
from rank_bm25 import BM25Okapi


def _tokenize(text: str) -> list[str]:
    """jieba 中文分词，过滤单字符停用词和纯空白。"""
    return [w for w in jieba.cut(text) if len(w.strip()) > 1]


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

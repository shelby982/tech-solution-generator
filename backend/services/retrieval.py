# backend/services/retrieval.py
def retrieve_chunks(
    chunks: list[dict],
    query: str,
    top_k: int = 5,
) -> list[dict]:
    """关键词交集计分，返回 Top-K 相关 chunks。"""
    if not chunks:
        return []
    query_words = set(query.lower().split())

    def score(chunk: dict) -> int:
        cw = set(chunk["content"].lower().split())
        return len(query_words & cw)

    ranked = sorted(chunks, key=score, reverse=True)
    return ranked[:top_k]

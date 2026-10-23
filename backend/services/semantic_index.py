"""R4 语义检索（离线版）：字符 bigram TF-IDF + 余弦相似度。

不依赖向量模型/外部服务：
  · 中文按 2-gram 切分（对同义改写不敏感程度弱于向量，但远好于纯关键词）
  · 用候选池当语料算 IDF（词越少见越有区分度）
  · 余弦相似度作为第三路检索信号
"""
from __future__ import annotations

import math
import re
from collections import Counter

_PUNCT = re.compile(r"[\s，。；：、（）()【】《》\"'“”‘’\-—_=·•.,;:!?/\\|]+")


def bigrams(text: str) -> Counter:
    body = _PUNCT.sub("", str(text or ""))
    if len(body) < 2:
        return Counter({body: 1}) if body else Counter()
    return Counter(body[index:index + 2] for index in range(len(body) - 1))


def build_idf(documents: list[str]) -> dict[str, float]:
    """用候选池构造 IDF 表（越小越常见）。"""
    df: Counter = Counter()
    for text in documents:
        df.update(set(bigrams(text)))
    total = max(1, len(documents))
    return {gram: math.log(1 + total / (1 + count)) for gram, count in df.items()}


def vector(text: str, idf: dict[str, float]) -> dict[str, float]:
    grams = bigrams(text)
    if not grams:
        return {}
    result = {}
    for gram, count in grams.items():
        weight = idf.get(gram)
        if weight:
            result[gram] = (1 + math.log(count)) * weight
    return result


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(value * b.get(gram, 0.0) for gram, value in a.items())
    if not dot:
        return 0.0
    norm_a = math.sqrt(sum(value * value for value in a.values()))
    norm_b = math.sqrt(sum(value * value for value in b.values()))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def score_documents(query: str, documents: list[str]) -> list[float]:
    """一次算一批（内部建 IDF）。返回与 documents 等长的相似度。"""
    if not documents:
        return []
    idf = build_idf(documents)
    query_vec = vector(query, idf)
    return [round(cosine(query_vec, vector(doc, idf)), 4) for doc in documents]

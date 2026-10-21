"""图示质量硬校验：图中的每个节点必须能在正文里找到依据（防止"图编造"）。

规则：
- 节点名去掉标点/空白后，取 2~6 字的核心词
- 核心词能在本节正文（去空白）里找到 → 通过
- 否则记为"图与文不一致"
纯规则，不调模型。
"""
from __future__ import annotations

import re

_STRIP = re.compile(r"[\s，。、；：（）()【】\[\]「」『』\"'·—\-–_/\\|]+")


def _normalize(text: str) -> str:
    return _STRIP.sub("", str(text or ""))


def _core_terms(node: str) -> list[str]:
    """节点名的核心词：整体 + 前 4 字 + 后 4 字（至少 2 字）。"""
    compact = _normalize(node)
    if len(compact) < 2:
        return []
    terms = {compact}
    if len(compact) > 4:
        terms.add(compact[:4])
        terms.add(compact[-4:])
    return [term for term in terms if len(term) >= 2]


def check_nodes_in_text(nodes: list[str], text: str) -> list[str]:
    """返回"找不到依据"的节点名列表（空列表 = 全部有依据）。"""
    body = _normalize(text)
    if not body:
        return [str(node) for node in nodes or []]
    missing: list[str] = []
    for node in nodes or []:
        terms = _core_terms(node)
        if not terms:
            continue
        if not any(term in body for term in terms):
            missing.append(str(node))
    return missing

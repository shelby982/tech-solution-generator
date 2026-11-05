"""章节相关素材匹配（R1）：章节画像 → 关键词/类型两路检索 → 可解释打分。

不做向量（第一版）：关键词 + 类型 + 要点覆盖 + 来源可信度，同义词表补"换词命中"。
"""
from __future__ import annotations

import re

# 同义词组（章节用词 ↔ 素材用词）
_SYNONYMS = (
    ("业绩", "案例", "同类项目", "实施经验", "合同业绩"),
    ("资质", "资格", "证书", "认证", "营业执照"),
    ("现状", "目前", "当前", "基本情况", "既有"),
    ("目标", "指标", "考核", "验收标准", "成效"),
    ("差距", "问题", "不足", "短板", "痛点"),
    ("数据", "规模", "数量", "业务量", "统计"),
    ("安全", "等级保护", "合规", "防护"),
    ("数据管理", "分类分级", "权限控制", "治理"),
    ("实施", "工期", "进度", "部署", "上线"),
    ("运维", "服务", "保障", "响应"),
    ("政策", "法规", "标准", "规范", "依据"),
    ("范围", "边界", "内容", "工作项"),
)
_STOP = set("的了和与及或是在对为中上下为以于等这那其有并而也就都还要会可将把被从到与")
_TYPE_KEYWORDS = {
    "table": ("表", "清单", "对比", "矩阵", "一览"),
    "data": ("数据", "指标", "数值", "规模", "数量"),
    "clause": ("条款", "要求", "应当", "须"),
    "qualification": ("业绩", "资质", "资格"),
    "policy": ("政策", "法规", "标准"),
}


# 领域词表：只认这些词，避免「本项目的」这类垃圾 n-gram 命中
_VOCAB = (
    "现状", "目标", "差距", "问题", "不足", "背景", "需求", "理解", "范围", "边界",
    "业绩", "案例", "资质", "资格", "证书", "认证", "经验", "能力", "团队", "人员",
    "数据", "规模", "数量", "指标", "考核", "评分", "标准", "规范", "制度", "政策", "法规", "条款",
    "安全", "等级保护", "合规", "防护", "分类分级", "权限", "审计", "日志", "备份", "容灾",
    "系统", "平台", "功能", "性能", "并发", "接口", "集成", "部署", "实施", "工期", "进度",
    "验收", "培训", "运维", "服务", "响应", "保障", "质量", "风险管理", "难点", "措施", "方案",
    "里程碑", "交付", "成果", "组织", "项目管理", "流程", "用户", "角色", "预算", "成本",
)


def _terms(text: str) -> set[str]:
    """只保留领域词表里的词 + 英文/百分比（不生成垃圾 n-gram）。"""
    body = str(text or "")
    words: set[str] = set()
    for token in _VOCAB:
        if token in body:
            words.add(token)
    for token in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", body):
        words.add(token.lower())
    for token in re.findall(r"\d+(?:\.\d+)?\s*%", body):
        words.add(token.replace(" ", ""))
    return words


def _expand(terms: set[str]) -> set[str]:
    """同义词扩展：命中某一组里任一词，就把整组加进去。"""
    expanded = set(terms)
    for group in _SYNONYMS:
        if any(word in expanded for word in group):
            expanded.update(group)
    return expanded


def section_profile(section: dict, plan: dict | None = None) -> dict:
    """章节画像：标题 + 要点 + 交付物 + 依据 → 关键词 / 想要的类型。"""
    title = str((section or {}).get("title") or "")
    points = [str(x) for x in ((section or {}).get("points") or [])]
    deliverables = (section or {}).get("deliverables") or []
    types_wanted: list[str] = []
    for item in deliverables:
        kind = str((item or {}).get("kind") or "")
        if kind == "table":
            types_wanted.append("table")
        elif kind == "figure":
            types_wanted.append("image")
        elif kind == "text":
            types_wanted.append("data")
    text = " ".join([title, *points, " ".join(str(it.get("title") or "") for it in deliverables)])
    keywords = _expand(_terms(text))
    for kind, words in _TYPE_KEYWORDS.items():
        if any(word in text for word in words):
            types_wanted.append(kind)
    return {"title": title, "points": points, "keywords": keywords,
            "types_wanted": sorted(set(types_wanted)), "raw": text}


_TRUST = {"main_rfp": 1.0, "spec": 1.0, "scoring": 1.0, "web_official": 0.9,
          "web": 0.6, "source": 0.8}


def score_chunk(profile: dict, chunk: dict, semantic: float | None = None) -> tuple[float, dict]:
    """返回 (分值, 解释)。解释用于前端展示"为什么推荐"。"""
    text = str(chunk.get("text") or "")
    terms = _terms(text)
    keywords = profile["keywords"]
    hits = [word for word in keywords if word in terms and len(word) >= 2]
    # 关键词匹配度：按"词的权重"累加（越具体的词权重越高，最多 3 分）
    weight = 0.0
    for word in hits:
        weight += 1.0 + (0.5 if len(word) >= 3 else 0.0) + (0.5 if len(word) >= 4 else 0.0)
    keyword_score = min(1.0, weight / 3)

    # 类型匹配
    tags = set(chunk.get("tags") or [])
    types_wanted = set(profile["types_wanted"])
    if types_wanted and tags:
        type_score = len(tags & types_wanted) / len(types_wanted)
    elif types_wanted:
        type_score = 0.3 if "data" in types_wanted and re.search(r"\d", text) else 0.0
    else:
        type_score = 0.0

    # 要点覆盖（章节 points 命中几条）
    covered = 0
    for point in profile["points"]:
        point_terms = _expand(_terms(point))
        if any(word in terms for word in point_terms):
            covered += 1
    coverage = covered / max(1, len(profile["points"]))

    trust = _TRUST.get(str(chunk.get("role") or ""), 0.5)
    if chunk.get("source_kind") == "web":
        trust = 0.6
    sexp = max(0.0, min(1.0, semantic or 0.0))
    score = (0.33 * keyword_score + 0.22 * sexp + 0.18 * type_score
             + 0.15 * coverage + 0.12 * trust)
    if not hits and not covered and sexp < 0.12:
        score *= 0.4                       # 关键词/要点/语义都弱 → 强降权
    # 低价值片段（封面/表单/资格清单/模板）：除"要资格业绩"的节外，几乎不推荐
    tags = set(chunk.get("tags") or [])
    if "boilerplate" in tags and "qualification" not in types_wanted:
        score *= 0.2
    # 信息量：正文型（有句子、有长度）优先于清单碎片
    body = str(chunk.get("text") or "")
    if len(body) >= 120 and ("。" in body or "；" in body):
        score *= 1.08
    elif len(body) < 40:
        score *= 0.6
    reason = []
    if hits:
        reason.append("命中「" + "、".join(sorted(hits, key=len, reverse=True)[:3]) + "」")
    if tags & types_wanted:
        reason.append("类型匹配：" + "、".join(sorted(tags & types_wanted)))
    if covered:
        reason.append(f"覆盖要点 {covered}/{len(profile['points'])}")
    if sexp >= 0.15:
        reason.append(f"语义相近 {int(sexp * 100)}%")
    reason.append("来源：" + ("招标文件" if str(chunk.get("role")) in ("main_rfp", "spec") else
                              "联网来源" if chunk.get("source_kind") == "web" else "素材"))
    return round(score, 3), {"reason": " · ".join(reason), "hits": sorted(hits)[:6],
                             "coverage": covered}


def recommend(profile: dict, chunks: list[dict], *, limit: int = 10, min_score: float = 0.18) -> list[dict]:
    from services.semantic_index import score_documents

    chunks = list(chunks or [])
    sem = score_documents(profile.get("raw") or profile.get("title") or "",
                          [str(chunk.get("text") or "") for chunk in chunks])
    scored = []
    wants_table = "table" in set(profile.get("types_wanted") or [])
    for index, chunk in enumerate(chunks):
        text = str(chunk.get("text") or "")
        # 原始 Markdown 表格不是“证据段落”：只有本节明确要表格时才允许进入推荐
        if not wants_table and text.count("|") >= 4:
            continue
        raw = str(profile.get("raw") or "")
        # 评分标准/评分办法/分值表不是证据；除非本节本身就在写评分响应
        if "评分" not in raw and re.search(r"评分标准|评分办法|评审因素|评分因素|\(\s*\d+\s*分\s*\)|\d+\s*分\)", text):
            continue
        # 模板/目录/封面/承诺函没有证据价值
        if "模板" not in raw and re.search(r"投标文件格式|格式要求|目录|封面|承诺函", text):
            continue
        score, explain = score_chunk(profile, chunk, sem[index] if index < len(sem) else 0.0)
        if score < min_score:
            continue
        scored.append({**chunk, "score": score, **explain})
    scored.sort(key=lambda item: item["score"], reverse=True)
    seen: set[str] = set()
    unique: list[dict] = []
    for item in scored:
        key = str(item.get("text") or "")[:24]
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
        if len(unique) >= limit:
            break
    return unique

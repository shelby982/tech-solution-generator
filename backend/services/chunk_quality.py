"""片段质量（C1）：尺寸归一 + 噪声过滤 + 指纹去重。

现状问题（实测 1634 段）：中位 87 字、<60 字占 39%、最长 2.7 万字、目录行 31 段。
规则：
  · 噪声（目录行/页眉脚/页码/重复行/样本表）→ 丢弃
  · 太短（<80 字）→ 与下一段合并（标题作为首行）
  · 太长（>800 字）→ 按 条款 > 段落 > 句号 切；表格整块保留（≤1200）
  · 去重：内容指纹（去空白+标点）相同只留一条
"""
from __future__ import annotations

import hashlib
import re

MIN_LEN = 80
MAX_LEN = 800
TABLE_MAX = 1200

# 噪声判据
_NOISE_PATTERNS = (
    re.compile(r"\.{3,}\s*\d*$"),                      # 目录行：……12
    re.compile(r"^[\s\-—_=·•.、]*\d+[\s\-—_=·•.、]*$"),   # 纯页码/分隔
    re.compile(r"^(目\s*录|第\s*\d+\s*页|共\s*\d+\s*页)"),
    re.compile(r"(仅供(参考|示例)|样本|模板，请勿)"),
    re.compile(r"^[\s\W]{0,20}$"),                     # 无实质字符
)
# 表格判据
_TABLE = re.compile(r"^\s*\|.*\|\s*$", re.M)
_CLAUSE = re.compile(r"(?m)^\s*(第[一二三四五六七八九十百0-9]+条|[\d.]+、|\d+\.\d+)")
_SENTENCE = re.compile(r"(?<=[。；！？])")
_PUNCT = re.compile(r"[\s，。；：、（）()【】\[\]《》\"'“”‘’\-—_=·•.]")


_TAG_RULES = (
    ("clause", re.compile(r"(第[一二三四五六七八九十百0-9]+条|应(当)?(提供|提交|满足|符合)|须(提供|提交|具备|满足)|不得)")),
    ("data", re.compile(r"\d+(?:\.\d+)?\s*(?:%|个|项|人|万|亿|台|套|小时|天|分|元|页|次)")),
    ("qualification", re.compile(r"(业绩|资质|资格|证书|认证|营业执照|合同|验收|案例)")),
    ("policy", re.compile(r"(《[^》]{2,40}》|政策|法规|标准|规范)")),
    ("table", re.compile(r"\|.*\|")),
)


def tag_of(text: str) -> list[str]:
    """片段类型标签（C2）：clause / data / qualification / policy / table。"""
    body = str(text or "")
    import json as _json
    return [tag for tag, pattern in _TAG_RULES if pattern.search(body)]


def fingerprint(text: str) -> str:
    return hashlib.sha1(_PUNCT.sub("", str(text or "")).encode("utf-8")).hexdigest()


def is_noise(text: str) -> bool:
    body = str(text or "").strip()
    if len(body) < 8:
        return True
    return any(pattern.search(body) for pattern in _NOISE_PATTERNS)


def _split_long(text: str) -> list[str]:
    """超长片段：按条款边界 → 段落边界 → 句号切，不切断句子。"""
    body = str(text or "").strip()
    if len(body) <= MAX_LEN:
        return [body]
    limit = TABLE_MAX if _TABLE.search(body) else MAX_LEN
    if len(body) <= limit:
        return [body]
    pieces: list[str] = []
    # ① 条款边界
    cuts = [m.start() for m in _CLAUSE.finditer(body)]
    if len(cuts) >= 2:
        bounds = cuts + [len(body)]
        for index in range(len(bounds) - 1):
            piece = body[bounds[index]:bounds[index + 1]].strip()
            if piece:
                pieces.append(piece)
    # ② 段落 / 句号兜底
    if not pieces:
        buffer = ""
        for sentence in _SENTENCE.split(body):
            if len(buffer) + len(sentence) <= limit:
                buffer += sentence
                continue
            if buffer.strip():
                pieces.append(buffer.strip())
            buffer = sentence
        if buffer.strip():
            pieces.append(buffer.strip())
    # ③ 仍超长 → 强制按长度切（尽量在句号处）
    result: list[str] = []
    for piece in pieces:
        while len(piece) > limit:
            window = piece[:limit]
            cut = max(window.rfind("。"), window.rfind("；"), window.rfind("\n"))
            cut = cut if cut > limit // 2 else limit
            result.append(piece[:cut].strip())
            piece = piece[cut:].strip()
        if piece:
            result.append(piece)
    return result


_NUMBERING = re.compile(r"^\s*(第[一二三四五六七八九十百]+[章节条]|[\d]+(?:\.[\d]+)*)\s*")


def heading_path(titles: list[str]) -> list[str]:
    """由一串节标题推断层级路径（C3）：按编号深度维护栈。

    「第三章 投标人资格要求」→ path=["第三章 投标人资格要求"]
    「3.2 资质要求」          → path=["第三章 …", "3.2 资质要求"]
    """
    stack: list[tuple[int, str]] = []
    paths: list[str] = []
    for title in titles or []:
        name = str(title or "").strip()
        if not name:
            paths.append("")
            continue
        match = _NUMBERING.match(name)
        if match:
            token = match.group(1)
            if token.startswith("第"):
                # 第X章=1 / 第X节=2 / 第X条=3
                depth = 3 if token.endswith("条") else 2 if token.endswith("节") else 1
            else:
                depth = token.count(".") + 1
        else:
            # 无编号：视为与上一个同级（保持栈深）
            depth = stack[-1][0] if stack else 1
        while stack and stack[-1][0] >= depth:
            stack.pop()
        stack.append((depth, name))
        paths.append(" > ".join(item[1] for item in stack))
    return paths


def normalize(chunks: list[dict], *, target: int = MAX_LEN) -> list[dict]:
    """chunks: [{content, page}] → 归一后的 [{content, page, tags}]。"""
    cleaned: list[dict] = []
    for chunk in chunks or []:
        text = str(chunk.get("content") or "").strip()
        if not text or is_noise(text):
            continue
        cleaned.append({"content": text, "page": int(chunk.get("page") or 0),
                        "path": str(chunk.get("path") or "")})

    # 短片段与下一段合并（标题合并到正文）
    merged: list[dict] = []
    buffer = None
    for chunk in cleaned:
        if buffer is None:
            buffer = dict(chunk)
            continue
        if len(buffer["content"]) < MIN_LEN:
            buffer["content"] = f"{buffer['content']}\n{chunk['content']}".strip()
            buffer["page"] = buffer["page"] or chunk["page"]
        else:
            merged.append(buffer)
            buffer = dict(chunk)
    if buffer:
        merged.append(buffer)

    # 超长切分 → 相邻短块合并 → 指纹去重
    split_chunks: list[dict] = []
    for chunk in merged:
        for piece in _split_long(chunk["content"]):
            text = piece.strip()
            if not text or is_noise(text):
                continue
            split_chunks.append({"content": text, "page": chunk["page"],
                                 "path": chunk.get("path") or ""})

    compacted: list[dict] = []
    buffer = None
    for chunk in split_chunks:
        if buffer is None:
            buffer = dict(chunk)
            continue
        if len(buffer["content"]) < MIN_LEN and len(buffer["content"]) + len(chunk["content"]) <= TABLE_MAX:
            buffer["content"] = f"{buffer['content']}\n{chunk['content']}".strip()
        else:
            compacted.append(buffer)
            buffer = dict(chunk)
    if buffer:
        compacted.append(buffer)

    result: list[dict] = []
    seen: set[str] = set()
    for chunk in compacted:
        text = str(chunk["content"]).strip()
        if not text or is_noise(text):
            continue
        key = fingerprint(text)
        if key in seen:
            continue
        seen.add(key)
        result.append({"content": text, "page": chunk["page"], "fingerprint": key,
                       "tags": tag_of(text), "path": chunk.get("path") or ""})
    return result


def stats(chunks: list[dict]) -> dict:
    lengths = [len(str(c.get("content") or "")) for c in chunks or []]
    if not lengths:
        return {"count": 0}
    lengths.sort()
    return {
        "count": len(lengths),
        "avg": round(sum(lengths) / len(lengths)),
        "median": lengths[len(lengths) // 2],
        "max": lengths[-1],
        "min": lengths[0],
        "lt60": sum(1 for x in lengths if x < 60),
        "gt800": sum(1 for x in lengths if x > 800),
    }


# ── 重新切片（保住已选素材）──────────────────────────────────────────────
async def recut_material(db, material_id: int) -> dict:
    """按新规则重建某素材的片段；已选素材按"内容指纹"映射到新片段。"""
    rows = await (await db.execute(
        "SELECT content, page, status FROM material_chunks WHERE material_id=? ORDER BY chunk_index",
        (material_id,))).fetchall()
    if not rows:
        return {"material_id": material_id, "skipped": True}

    # 记录旧"已选"片段的内容（用指纹匹配）
    selected_texts = [r[0] for r in rows if str(r[2]) == "selected"]
    # C3：用每段首行（章节标题）推断章节路径
    titles = [str(r[0] or "").splitlines()[0].strip() if str(r[0] or "").strip() else "" for r in rows]
    paths = heading_path(titles)
    normalized = normalize([{"content": r[0], "page": r[1],
                             "path": paths[index] if index < len(paths) else ""}
                            for index, r in enumerate(rows)])
    if not normalized:
        return {"material_id": material_id, "skipped": True}

    # 新片段里，与旧"已选"片段高度相似（difflib ≥0.55）→ 标为 selected
    # 注意：旧的已选片段可能很短（如"招标文件"），短文本不能做包含匹配，会误伤大量片段
    import difflib

    def _norm(text: str) -> str:
        return _PUNCT.sub("", str(text or ""))

    old_kept = [_norm(t) for t in selected_texts if len(_norm(t)) >= 20]

    def covered(piece: str) -> bool:
        current = _norm(piece)
        if not current or not old_kept:
            return False
        return any(difflib.SequenceMatcher(None, current, previous).ratio() >= 0.55 for previous in old_kept)

    await db.execute("DELETE FROM material_chunks WHERE material_id=?", (material_id,))
    saved = 0
    for index, chunk in enumerate(normalized):
        status = "selected" if selected_texts and covered(chunk["content"]) else "candidate"
        saved += 1 if status == "selected" else 0
        import json as _json
        await db.execute(
            "INSERT INTO material_chunks (material_id, chunk_index, content, page, status, tags, path) "
            "VALUES (?,?,?,?,?,?,?)",
            (material_id, index, chunk["content"], chunk["page"], status,
             _json.dumps(chunk.get("tags") or [], ensure_ascii=False),
             str(chunk.get("path") or "")[:200]))
    await db.commit()
    return {"material_id": material_id, "before": len(rows), "after": len(normalized),
            "selected_kept": saved, "stats": stats(normalized)}

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


_HTML_TAG = re.compile(r"<\s*/?\s*(br|p|div|span|li|ul|ol|table|tr|td|th|strong|b|em|i|font|img)[^>]*>", re.I)
# 低价值片段（封面/表单/资格清单/模板）：占篇幅但对正文没有信息价值
_BOILERPLATE = (
    re.compile(r"(招标人[:：]|投标人[:：]|招标代理机构[:：]|采购人[:：])"),
    re.compile(r"(盖单位公章|电子印章|签字|法定代表人|日期[:：]?\s*年|年\s*月\s*日)"),
    re.compile(r"(投标文件格式|响应文件格式|格式[一二三四五六七八九十]|范本)"),
    re.compile(r"(投标人被.{0,20}(不接受投标|市场禁入)|未解除的)"),
    re.compile(r"(符合性审查|资格审查合格|初步评审)"),
)


def strip_html(text: str) -> str:
    """去掉 PDF/网页带进来的 <br> 等标签（预览与素材里都不该出现）。"""
    cleaned = _HTML_TAG.sub("\n", str(text or ""))
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


# 单条命中即可判为低价值（否决条款 / 资格清单 / 格式模板 / 封面）
_STRONG_BOILERPLATE = (
    re.compile(r"(投标人被.{0,40}(不接受投标|市场禁入)|且未解除的|否决其投标)"),
    re.compile(r"(投标文件格式|响应文件格式|格式[一二三四五六七八九十]|范本)"),
    re.compile(r"(招标人[:：]|投标人[:：]|招标代理机构[:：]).{0,40}(地址|联系人|日期)"),
    re.compile(r"(盖单位公章|电子印章|法定代表人[:：]?\s*\(?签字)"),
    re.compile(r"^\s*[（(]\s*\d+\s*[)）]"),
)


def is_boilerplate(text: str) -> bool:
    body = str(text or "")
    if any(pattern.search(body) for pattern in _STRONG_BOILERPLATE):
        # 但不能把"正文里引用了条款号"误判：正文型（有句号分段、长度>200）且不含格式/资格词时不算
        if len(body) > 260 and not re.search(r"(格式|范本|公章|签字|否决|禁入|未解除)", body):
            return False
        return True
    hits = sum(1 for pattern in _BOILERPLATE if pattern.search(body))
    return hits >= 2 or (hits == 1 and len(body) < 160)


_TAG_RULES = (
    ("clause", re.compile(r"(第[一二三四五六七八九十百0-9]+条|应(当)?(提供|提交|满足|符合)|须(提供|提交|具备|满足)|不得)")),
    ("data", re.compile(r"\d+(?:\.\d+)?\s*(?:%|个|项|人|万|亿|台|套|小时|天|分|元|页|次)")),
    ("qualification", re.compile(r"(业绩|资质|资格|证书|认证|营业执照|合同|验收|案例)")),
    ("policy", re.compile(r"(《[^》]{2,40}》|政策|法规|标准|规范)")),
    ("table", re.compile(r"\|.*\|")),
)


# 程序性条款（招标流程/时间/保证金等）：对写正文没有信息价值
_PROCEDURAL = re.compile(
    r"(招标文件获取|获取招标文件|投标截止|开标时间|开标地点|澄清|答疑|保证金|"
    r"投标有效期|评标委员会|中标通知|投标文件的递交|递交投标文件|异议|投诉|"
    r"招标公告发布|资格审查方式|招标分类|获取方式|报名)")
# 表格空壳：表格行里大多为空单元格
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.M)


def _empty_table_ratio(text: str) -> float:
    rows = _TABLE_ROW.findall(str(text or ""))
    if not rows:
        return 0.0
    empty = 0
    for row in rows:
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        if all(not cell or set(cell) <= set("-: ") for cell in cells):
            empty += 1
    return empty / len(rows)


def tag_of(text: str) -> list[str]:
    """片段类型标签（C2）：clause / data / qualification / policy / table / boilerplate。"""
    body = str(text or "")
    tags = [tag for tag, pattern in _TAG_RULES if pattern.search(body)]
    if is_boilerplate(body):
        tags.append("boilerplate")
    if _PROCEDURAL.search(body):
        tags.append("procedural")
    if _empty_table_ratio(body) >= 0.5:
        tags.append("empty_table")
    return tags


def substance_score(text: str) -> float:
    """实质内容分（0~1）：有要求类动词 + 有句子 + 有具体名词，越高越值得备料。"""
    body = str(text or "")
    if len(body) < 40:
        return 0.0
    verbs = len(re.findall(r"(要求|应(当)?|须|具备|实现|支持|覆盖|包括|保证|满足|提供|指标|功能|性能|安全|管理|设计)", body))
    sentences = body.count("。") + body.count("；")
    digits = len(re.findall(r"\d", body))
    score = min(1.0, verbs / 8) * 0.5 + min(1.0, sentences / 3) * 0.3 + min(1.0, digits / 10) * 0.2
    return round(score, 3)


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


def _clean_title(title: str) -> str:
    text = strip_html(str(title or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:80]


def heading_path(titles: list[str]) -> list[str]:
    """由一串节标题推断层级路径（C3）：按编号深度维护栈。

    「第三章 投标人资格要求」→ path=["第三章 投标人资格要求"]
    「3.2 资质要求」          → path=["第三章 …", "3.2 资质要求"]
    """
    stack: list[tuple[int, str]] = []
    paths: list[str] = []
    for title in titles or []:
        name = _clean_title(title)
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
        text = strip_html(str(chunk.get("content") or ""))
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

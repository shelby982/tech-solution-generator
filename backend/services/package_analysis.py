"""标包识别：**从招标文件正文里找事实**，不衍生、不提炼。

用户口径（决定这个模块的边界）：
  「需要从招标文件里面去找到标的对应的标包或标段。这需要按照招标文件的内容去做检索，
    检索完成之后，可能需要借助模型去做一下整理和分类。但这一块要以招标文件的事实为准，
    模型不能去做额外的衍生或者提炼，一切以招标文件内容为准。」
  「一个文件可能会属于多个标包，这块没有严格的对应关系。」

因此本模块：
  · **只抽取原文里出现的**「标的 / 标包 / 标段」表述，并记下**出处**（文件 + 页码 + 原句）；
  · 找不到就是找不到（返回空列表），**不猜测、不补全**；
  · 模型（可选）只允许做"整理/分类"（合并同一标包的多种写法），不允许新增标包。
    本版先只做规则；`normalize_with_llm` 留作后续接入点。
"""
from __future__ import annotations

import re
from typing import Iterable

# 中文数字（招标文件里「标的二」「第3标包」这类写法很常见）
_CN = "一二三四五六七八九十两"
_NUM = rf"(?:\d{{1,2}}|[{_CN}]{{1,3}})"

# 「标的1」「标的 一」「标包2」「标段三」「第3标包」「包号2」
_RE_TARGET = re.compile(rf"标\s*的\s*[:：]?\s*({_NUM})")
_RE_PACKAGE = re.compile(rf"标\s*包\s*[:：]?\s*({_NUM})")
_RE_SECTION = re.compile(rf"标\s*段\s*[:：]?\s*({_NUM})")
_RE_ORDINAL = re.compile(rf"第\s*({_NUM})\s*标\s*(包|段)")
_RE_PAIR = re.compile(rf"标\s*的\s*[:：]?\s*({_NUM})\s*标\s*包\s*[:：]?\s*({_NUM})")

_CN_DIGITS = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def cn_to_int(text: str) -> str:
    """把「一」「二」「十」「十二」这类中文数字转成阿拉伯数字字符串；已是数字就原样返回。

    只处理 1–99 这种招标文件里实际会出现的范围；超出范围原样返回（不猜）。
    """
    s = (text or "").strip()
    if s.isdigit():
        return s
    if not s or any(ch not in _CN_DIGITS for ch in s):
        return s
    if s == "十":
        return "10"
    if s.startswith("十"):
        return str(10 + _CN_DIGITS.get(s[1], 0))
    if "十" in s:
        head, _, tail = s.partition("十")
        return str(_CN_DIGITS.get(head, 0) * 10 + (_CN_DIGITS.get(tail, 0) if tail else 0))
    return str(_CN_DIGITS.get(s, s))


def _quote(text: str, start: int, end: int, width: int = 60) -> str:
    """取命中处的原句（左右各截一段，压成一行）—— 证据必须能让用户回到原文核对。"""
    left = max(0, start - width)
    right = min(len(text), end + width)
    snippet = re.sub(r"\s+", " ", text[left:right]).strip()
    return snippet[:160]


def extract_package_mentions(text: str) -> list[dict]:
    """从一段正文里抽取"标的/标包/标段"的**原文事实**。

    返回 ``[{target, package_no, kind, quote}]``：
      · ``target``/``package_no`` 是原文里写的编号（中文数字已转阿拉伯数字）
      · ``kind`` 说明它来自哪种表述（target/package/section/ordinal/pair）
      · ``quote`` 是原句片段（含命中处）
    同一段里重复出现会去重（同一 target+package_no+kind 只留第一条原句）。
    """
    out: list[dict] = []
    seen: set[tuple] = set()
    src = text or ""
    # 先找出所有「标的X标包Y」的跨度：这些跨度里的"标的X"/"标包Y"是**同一条事实的残缺视角**，
    # 不该再各出一条（实测：一句话会产出 3 条，界面看起来像有 3 个标包）。
    # 这不是推断 —— 只是同一个命中不重复计数；跨度之外的单独表述照旧保留（那是独立事实）。
    pair_spans = [(m.start(), m.end()) for m in _RE_PAIR.finditer(src)]

    def _inside_pair(start: int, end: int) -> bool:
        return any(start >= ps and end <= pe for ps, pe in pair_spans)

    def push(target: str, package_no: str, kind: str, start: int, end: int) -> None:
        key = (target, package_no, kind)
        if key in seen:
            return
        seen.add(key)
        out.append({
            "target": target,
            "package_no": package_no,
            "kind": kind,
            "quote": _quote(src, start, end),
        })

    for m in _RE_PAIR.finditer(src):                    # 标的1标包2（最完整）
        push(cn_to_int(m.group(1)), cn_to_int(m.group(2)), "pair", m.start(), m.end())
    for m in _RE_TARGET.finditer(src):
        if not _inside_pair(m.start(), m.end()):
            push(cn_to_int(m.group(1)), "", "target", m.start(), m.end())
    for m in _RE_PACKAGE.finditer(src):
        if not _inside_pair(m.start(), m.end()):
            push("", cn_to_int(m.group(1)), "package", m.start(), m.end())
    for m in _RE_SECTION.finditer(src):
        if not _inside_pair(m.start(), m.end()):
            push("", cn_to_int(m.group(1)), "section", m.start(), m.end())
    for m in _RE_ORDINAL.finditer(src):                 # 第3标包 / 第2标段
        num = cn_to_int(m.group(1))
        kind = "ordinal_package" if m.group(2) == "包" else "ordinal_section"
        push("", num, kind, m.start(), m.end())
    return out


def _confidence(mentions: list[dict]) -> str:
    """置信度只看"原文里出现的次数与完整度"，不做任何推断。

    · 高：既有 标的X标包Y 的成对表述（pair），或同一 (标的,标包) 被提到 ≥3 次
    · 中：被提到 2 次，或同时出现了"标的"与"标包"两种表述
    · 低：只出现 1 次，或只有单一表述（可能只是目录/引用）
    """
    kinds = {m["kind"] for m in mentions}
    if "pair" in kinds or len(mentions) >= 3:
        return "high"
    if len(mentions) >= 2 or {"target", "package"} & kinds == {"target", "package"}:
        return "medium"
    return "low"


def analyze_packages(chunks: Iterable[dict], materials: Iterable[dict] | None = None) -> list[dict]:
    """把 chunks 里的标包事实聚合成"标包清单"。

    ``chunks``: ``[{material_id, chunk_index, page, content}]``
    ``materials``: ``[{id, filename}]``（用于把 material_id 翻成文件名给人看）

    返回 ``[{target, package_no, package_name, mentions, evidence, confidence, source_material_ids}]``：
      · ``evidence`` 是原文片段列表（每条含 material_id / filename / page / quote / kind）
      · ``package_name`` **留空**：名字要用户确认或后续从原文标题里取，本函数不编造
      · 同名不同来源会合并计数；**没有命中的 chunk 不会产生任何条目**
    """
    names = {str(m.get("id")): (m.get("filename") or "") for m in (materials or [])}
    buckets: dict[tuple[str, str], list[dict]] = {}
    for ch in chunks or []:
        mentions = extract_package_mentions(ch.get("content") or "")
        if not mentions:
            continue
        mid = str(ch.get("material_id") or "")
        for mn in mentions:
            key = (mn["target"], mn["package_no"])
            buckets.setdefault(key, []).append({
                "material_id": mid,
                "filename": names.get(mid, ""),
                "page": ch.get("page"),
                "quote": mn["quote"],
                "kind": mn["kind"],
            })

    out: list[dict] = []
    for (target, package_no), ev in buckets.items():
        all_ev = buckets[(target, package_no)]
        out.append({
            "target": target,
            "package_no": package_no,
            "package_name": "",                      # 不编造：留给用户在界面上填/确认
            "mentions": len(all_ev),
            "confidence": _confidence([{"kind": e["kind"]} for e in all_ev]),
            "source_material_ids": sorted({e["material_id"] for e in all_ev if e["material_id"]}),
            "evidence": all_ev[:5],                  # 最多留 5 条原文出处，够核对即可
        })
    # 稳定排序：先标的、再标包
    out.sort(key=lambda x: (x["target"] or "", x["package_no"] or ""))
    return out


# ── 落库与执行 ────────────────────────────────────────────────────────────────
# 两处都要写：
#   · project_packages  —— 标包清单（分析结果，可重新生成）
#   · material_packages —— 文件↔标包的多对多关联（用户口径：一个文件可属多个标包）
async def save_analysis(db, project_id: int, packages: list[dict]) -> dict:
    """把分析结果落库（覆盖式）。返回 ``{"packages": n, "links": m}``。"""
    import json as _json

    await db.execute("DELETE FROM project_packages WHERE project_id = ?", (project_id,))
    links = 0
    for p in packages or []:
        target = str(p.get("target") or "")
        package_no = str(p.get("package_no") or "")
        await db.execute(
            """INSERT INTO project_packages
               (project_id, target, package_no, package_name, confidence, mentions,
                source_material_ids, evidence, updated_at)
               VALUES (?,?,?,?,?,?,?,?, CURRENT_TIMESTAMP)""",
            (project_id, target, package_no, str(p.get("package_name") or ""),
             str(p.get("confidence") or "low"), int(p.get("mentions") or 0),
             _json.dumps(p.get("source_material_ids") or [], ensure_ascii=False),
             _json.dumps(p.get("evidence") or [], ensure_ascii=False)),
        )
    # 文件↔标包：只保留本次分析命中的（另外允许用户手工改 —— 手工改动走单独的接口）
    await db.execute("DELETE FROM material_packages WHERE project_id = ?", (project_id,))
    for p in packages or []:
        for mid in p.get("source_material_ids") or []:
            if not mid:
                continue
            await db.execute(
                """INSERT OR IGNORE INTO material_packages (material_id, project_id, target, package_no)
                   VALUES (?,?,?,?)""",
                (int(mid), project_id, str(p.get("target") or ""), str(p.get("package_no") or "")),
            )
            links += 1
    await db.commit()
    return {"packages": len(packages or []), "links": links}


async def load_analysis(db, project_id: int) -> list[dict]:
    """读回已落库的标包清单（不重新分析）。"""
    import json as _json

    rows = await (await db.execute(
        """SELECT target, package_no, package_name, confidence, mentions,
                  source_material_ids, evidence
             FROM project_packages WHERE project_id = ?
            ORDER BY target, package_no""", (project_id,))).fetchall()
    out = []
    for r in rows:
        out.append({
            "target": r[0], "package_no": r[1], "package_name": r[2],
            "confidence": r[3], "mentions": r[4],
            "source_material_ids": _json.loads(r[5] or "[]"),
            "evidence": _json.loads(r[6] or "[]"),
        })
    return out


async def run_analysis(db, project_id: int, *, use_llm: bool = False,
                       call_model=None) -> tuple[list[dict], str]:
    """从库里读 chunks + materials → 分析 → 落库 → 返回结果。

    没有 chunks（文件丢了/没解析）时返回**空清单**并如实说明，不猜。
    """
    materials = await (await db.execute(
        "SELECT id, filename, role FROM materials WHERE project_id = ? AND deleted_at IS NULL",
        (project_id,))).fetchall()
    mats = [{"id": r[0], "filename": r[1], "role": r[2]} for r in materials]
    chunks = await (await db.execute(
        """SELECT c.material_id, c.chunk_index, c.page, c.content
             FROM material_chunks c JOIN materials m ON m.id = c.material_id
            WHERE m.project_id = ? AND m.deleted_at IS NULL
            ORDER BY c.material_id, c.chunk_index""", (project_id,))).fetchall()
    chs = [{"material_id": r[0], "chunk_index": r[1], "page": r[2], "content": r[3]} for r in chunks]
    packages = analyze_packages(chs, mats)
    note = ""
    if use_llm and packages and call_model is not None:
        # 模型只做合并/命名；结果经溯源校验，编造的一律丢弃（见 validate_merge_proposal）
        packages, note = await merge_with_llm(
            packages, [str(c.get("content") or "")[:2000] for c in chs[:5]], call_model)
    await save_analysis(db, project_id, packages)
    return packages, note


async def load_material_packages(db, project_id: int) -> dict:
    """文件 ↔ 标包（多对多）：``{material_id: [{"target","package_no"}]}``。"""
    rows = await (await db.execute(
        "SELECT material_id, target, package_no FROM material_packages WHERE project_id = ?",
        (project_id,))).fetchall()
    out: dict[str, list[dict]] = {}
    for mid, target, package_no in rows:
        out.setdefault(str(mid), []).append({"target": target, "package_no": package_no})
    return out


async def set_material_packages(db, project_id: int, material_id: int,
                                packages: list[dict]) -> int:
    """设置**某一份文件**归属的标包（覆盖式；人工修正走这里）。

    用户口径：一个文件可以属于多个标包，没有严格对应关系 —— 所以这里是集合，
    而且允许为空（= 未指定标包，不是错误）。
    """
    await db.execute(
        "DELETE FROM material_packages WHERE project_id = ? AND material_id = ?",
        (project_id, material_id))
    n = 0
    for p in packages or []:
        target = str((p or {}).get("target") or "")
        package_no = str((p or {}).get("package_no") or "")
        if not target and not package_no:
            continue
        await db.execute(
            """INSERT OR IGNORE INTO material_packages (material_id, project_id, target, package_no)
               VALUES (?,?,?,?)""",
            (material_id, project_id, target, package_no))
        n += 1
    await db.commit()
    return n


# ── 模型整理（只允许"合并 + 命名"，且必须能溯源）──────────────────────────────
# 用户口径：「可能需要借助模型去做一下整理和分类。但这一块要以招标文件的事实为准，
# 模型不能去做额外的衍生或者提炼，一切以招标文件内容为准。」
def _norm(s: str) -> str:
    """比较用归一化：去掉所有空白（原文里「标的 1 标包 2」很常见）。"""
    return re.sub(r"\s+", "", str(s or ""))


def validate_merge_proposal(facts: list[dict], proposal: dict,
                            source_texts: list[str] | None = None) -> list[dict]:
    """把模型提案变成**只含可溯源信息**的标包清单（纯函数，安全层）。

    规则：
      1. 模型**不能新增标包**：每个 group 必须匹配到已检索到的事实
         （target+package_no 相同，或其 aliases 命中某条事实的原文片段）；
      2. 命中的多条事实**合并**成一条（别名归一）；
      3. ``package_name`` 必须是原文里出现的**连续子串**，否则**留空**（不编造名字）；
      4. 提案没覆盖到的**事实原样保留**（不丢事实）；
      5. 证据 / 提到次数 / 置信度取并集与最高。
    """
    facts = list(facts or [])
    by_key = {(str(f.get("target") or ""), str(f.get("package_no") or "")): f for f in facts}
    haystack = _norm(" ".join(str(t) for t in (source_texts or []))) + " " + _norm(
        " ".join(str((ev or {}).get("quote") or "") for f in facts for ev in (f.get("evidence") or [])))

    used: set[tuple[str, str]] = set()
    out: list[dict] = []
    conf_rank = {"low": 0, "medium": 1, "high": 2}

    for g in (proposal or {}).get("groups") or []:
        if not isinstance(g, dict):
            continue
        target = str(g.get("target") or "")
        package_no = str(g.get("package_no") or "")
        aliases = [str(a) for a in (g.get("aliases") or []) if a]

        hits: list[dict] = []
        if (target, package_no) in by_key:
            hits.append(by_key[(target, package_no)])
        # 别名**同样要收**：密钥命中不等于已覆盖全部写法
        # （实测：group 写 package_no=2、别名写「标的1标包2」，只按密钥匹配会漏掉后者）
        for f in facts:
            key_f = (str(f.get("target") or ""), str(f.get("package_no") or ""))
            if key_f in used or any(f is h for h in hits):
                continue
            quotes = _norm(" ".join(str((ev or {}).get("quote") or "") for ev in (f.get("evidence") or [])))
            if any(_norm(a) and _norm(a) in quotes for a in aliases):
                hits.append(f)
        if not hits:
            continue                      # ← 模型新增的标包：**直接丢弃**

        # 名字必须在原文里出现；否则留空（不编造）
        raw_name = _norm(g.get("package_name") or "")
        name = str(g.get("package_name") or "").strip() if (raw_name and raw_name in haystack) else ""

        merged_ev: list[dict] = []
        srcs: list[str] = []
        mentions = 0
        best = "low"
        for f in hits:
            used.add((str(f.get("target") or ""), str(f.get("package_no") or "")))
            merged_ev += list(f.get("evidence") or [])
            srcs += list(f.get("source_material_ids") or [])
            mentions += int(f.get("mentions") or 0)
            if conf_rank.get(str(f.get("confidence")), 0) > conf_rank.get(best, 0):
                best = str(f.get("confidence"))
        # 合并后 target/package_no 取"事实里最具体"的那个（都用空就保持空）
        target2 = target or next((str(f.get("target") or "") for f in hits if f.get("target")), "")
        pkg2 = package_no or next((str(f.get("package_no") or "") for f in hits if f.get("package_no")), "")
        out.append({
            "target": target2, "package_no": pkg2, "package_name": name,
            "mentions": mentions, "confidence": best,
            "source_material_ids": sorted({s for s in srcs if s}),
            "evidence": merged_ev[:5],
            "merged_from": [f"{f.get('target') or ''}|{f.get('package_no') or ''}" for f in hits],
        })

    # 未被提案覆盖的事实原样保留（模型漏掉的不能丢）
    for f in facts:
        if (str(f.get("target") or ""), str(f.get("package_no") or "")) not in used:
            keep = dict(f)
            keep.setdefault("merged_from", [f"{keep.get('target') or ''}|{keep.get('package_no') or ''}"])
            out.append(keep)
    out.sort(key=lambda x: (x.get("target") or "", x.get("package_no") or ""))
    # 出口归一：无论来自模型合并还是"未被覆盖的事实"，字段都齐全（调用方不用猜）
    for p in out:
        p.setdefault("package_name", "")
        p.setdefault("mentions", 0)
        p.setdefault("confidence", "low")
        p.setdefault("evidence", [])
        p.setdefault("source_material_ids", [])
    return out


async def merge_with_llm(facts: list[dict], source_texts: list[str] | None,
                         call_model) -> tuple[list[dict], str]:
    """让模型整理标包（**只做合并/命名**）。返回 ``(packages, note)``。

    ``call_model`` 是注入的异步调用（便于单测注入假模型）。任何失败都**降级为规则结果**，
    绝不让"模型不可用"影响标包识别本身。
    """
    facts = list(facts or [])
    if not facts:
        return facts, ""
    try:
        proposal = await call_model({"facts": facts, "source_texts": source_texts or []})
    except Exception as e:                     # 模型不可用 → 规则结果照旧
        return facts, f"模型整理失败（{type(e).__name__}），已按原文检索结果展示"
    merged = validate_merge_proposal(facts, proposal or {}, source_texts)
    # 只有确实发生了合并/命名才说"整理过"，避免用户以为模型做了什么
    renamed = sum(1 for p in merged if p.get("package_name"))
    merged_n = sum(1 for p in merged if len(p.get("merged_from") or []) > 1)
    if not renamed and not merged_n:
        return facts, ""
    return merged, f"已由模型整理：合并 {merged_n} 组，命名 {renamed} 个（名字均取自原文）"

"""评审 issue 级定点重写：只改"问题对应的那一小段"，不动其他内容。

流程：
  ① 定位：从 issue.point 里的引用串/长中文片段，在正文中找到原文片段（句子级）
  ② 生成：把"原文片段 + 问题 + 建议"交给模型，只要求输出替换后的片段
  ③ 预览：返回 before/after（不落库）→ 用户确认后由前端调 apply 落库（写 revision）
"""
from __future__ import annotations

import re

SYSTEM = (
    "你是标书正文编辑，只做**局部定点修正**。\n"
    "输入会给出：问题（评审意见）、建议、以及正文里的**待改片段**。\n"
    "要求：\n"
    "1) 只输出修正后的**片段文本**本身，不要解释、不要标题、不要加【来源】以外的新标注；\n"
    "2) 保留原文中的引用标注（【来源：…】）、数字与专有名词，不得编造新事实；\n"
    "3) 如果问题指向「缺少内容」，用概括但完整的表述补足，不要留占位符；\n"
    "4) 长度与原文相近（±40%），不要扩写成整节。\n"
)

_QUOTED = re.compile(r"[「“\"]([^」”\"]{4,60})[」”\"]")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff，、；：（）0-9A-Za-z%\.\-]{10,80}")
_SENTENCE = re.compile(r"[^。；！？\n]{0,300}[。；！？]?")


def candidate_quotes(point: str) -> list[str]:
    """从评审问题里抽出可定位的原文片段（引用串优先，其次长中文片段）。"""
    text = str(point or "")
    quotes = [match.group(1).strip() for match in _QUOTED.finditer(text)]
    quotes += [match.group(0).strip() for match in _CJK_RUN.finditer(text)]
    # 去掉过短/纯标点的候选，按长度倒序（长的更可能是原文引用）
    cleaned = [quote for quote in quotes if len(quote) >= 8 and re.search(r"[\u4e00-\u9fff]", quote)]
    return sorted(dict.fromkeys(cleaned), key=len, reverse=True)


def locate(content: str, point: str) -> dict | None:
    """在正文里定位问题对应的片段（句子级，带上下文）。"""
    body = str(content or "")
    if not body.strip():
        return None
    for quote in candidate_quotes(point):
        at = body.find(quote)
        if at < 0:
            # 允许中间被标点打断：用前 12 字再试一次
            head = re.sub(r"\s+", "", quote)[:12]
            compact = re.sub(r"\s+", "", body)
            if head and head in compact:
                # 映射回原文位置：粗略取第一个出现的字符
                raw = quote[:12]
                at = body.find(raw)
            if at < 0:
                continue
        # 扩到句子边界
        start = max(0, body.rfind("。", 0, at) + 1)
        start = max(start, body.rfind("\n", 0, at) + 1, at - 200)
        end = body.find("。", at + len(quote))
        end = (end + 1) if end >= 0 else min(len(body), at + len(quote) + 200)
        fragment = body[start:end].strip()
        if len(fragment) < 6:
            continue
        return {"quote": quote, "before": fragment, "start": start, "end": end}

    # 第三策略：抽关键词，找"命中 2 个以上关键词"的句子
    terms = [term for term in re.findall(r"[\u4e00-\u9fff]{3,8}", str(point or ""))
             if not re.search(r"(缺少|缺少的|未|没有|不足|需要|建议|应|应当|评审|问题)", term)][:12]
    best: dict | None = None
    best_hits = 0
    for match in re.finditer(r"[^。；！？\n]{10,400}[。；！？]?", body):
        sentence = match.group(0)
        hits = sum(1 for term in terms if term in sentence)
        if hits > best_hits:
            best_hits = hits
            best = {"quote": "", "before": sentence.strip(), "start": match.start(), "end": match.end()}
    if best and best_hits >= 2:
        best["hits"] = best_hits
        best["matched_terms"] = [term for term in terms if term in best["before"]][:4]
        return best
    return None


def build_user_prompt(issue: dict, before: str, section: str = "") -> str:
    return (
        f"所在小节：{section or '（未知）'}\n"
        f"评审问题：{issue.get('point') or ''}\n"
        f"评审建议：{issue.get('suggestion') or '（无）'}\n"
        f"严重度：{issue.get('severity') or 'medium'}\n"
        f"---\n待改片段（只改这一段）：\n{before}\n---\n"
        "请输出修正后的片段文本。"
    )


def clean_output(text: str) -> str:
    body = str(text or "").strip()
    body = re.sub(r"^```[a-zA-Z]*\n?|```$", "", body).strip()
    body = re.sub(r"^(修正后的片段|片段|输出)[：:]\s*", "", body)
    return body.strip()


async def build_patch(block_pk: int, issue_index: int) -> dict:
    """为某条评审问题生成"定点重写"补丁（不落库）。"""
    from pydantic import BaseModel  # noqa: F401  （仅占位，避免循环导入）

    from services.chapter_review import load_review
    from services.config_store import config_store
    from infra.llm.clients import generate_oneshot_openai
    from services.block_store import get_block
    from db import get_db

    review = await load_review(block_pk)
    if not review:
        return {"found": False, "hint": "本章还没有评审结果，请先点「评审本章」"}
    issues = review.get("issues") or []
    if issue_index < 0 or issue_index >= len(issues):
        return {"found": False, "hint": "这条意见已失效，请重新评审"}
    issue = issues[issue_index]
    if issue.get("needs_material"):
        return {"found": False, "hint": "这条需要补充材料（重写修不好）→ 请到「素材」页签补料"}

    async with get_db() as db:
        block = await get_block(db, block_pk)
    content = str((block or {}).get("content") or "")
    hit = locate(content, str(issue.get("point") or ""))
    if not hit:
        return {"found": False, "hint": "这条意见指向「缺少内容」，正文里没有对应片段 → 建议用「重新编写本模块」整节重写",
                "issue": issue}

    try:
        configs, _ = config_store.get_configs_and_next_index()
    except Exception:
        configs = []
    config = (configs or [None])[0]
    if config is None:
        return {"found": False, "hint": "没有可用的模型配置"}
    section = ""
    before = hit["before"]
    head = content.rfind("\n## ", 0, hit["start"])
    if head >= 0:
        line_end = content.find("\n", head + 1)
        section = content[head + 3:line_end].strip()

    # 定点补丁要"快、只输出片段"：优先非 reasoning 模型（pro 类会把额度烧在 reasoning 上）
    ordered = sorted(configs or [], key=lambda item: 1 if "pro" in str(getattr(item, "model", "")).lower() else 0)
    prompt = build_user_prompt(issue, before, section)
    after = ""
    for candidate in ordered[:3]:
        try:
            raw = await generate_oneshot_openai(candidate, SYSTEM, prompt, max_tokens=1600, timeout=90.0,
                                                call_site="review_patch")
        except Exception:
            continue
        after = clean_output(raw)
        if after and len(after) >= 6:
            break
    if not after or len(after) < 6:
        return {"found": False, "hint": "模型没有生成可用内容，请重试或改用「重新编写本模块」"}
    return {"found": True, "mode": "replace", "section": section,
            "quote": hit.get("quote") or "", "before": before, "after": after,
            "issue": issue, "issue_index": issue_index}


async def apply_patch(block_pk: int, before: str, after: str, *, issue_index: int = -1) -> dict:
    """把补丁落到正文（精确匹配替换 + 写 revision）。"""
    from services.block_store import add_revision, get_block, update_block_content
    from db import get_db

    if not before or not after:
        raise ValueError("缺少原文或新文本")
    async with get_db() as db:
        block = await get_block(db, block_pk)
        if not block:
            raise ValueError("章节不存在")
        content = str(block.get("content") or "")
        at = content.find(before)
        if at < 0:
            return {"applied": False, "hint": "原文已变化，请重新生成补丁"}
        updated = content[:at] + after + content[at + len(before):]
        await add_revision(db, block_pk, content, summary="评审定点重写", source="ai")
        await update_block_content(db, block_pk, updated)
        # issue 状态机：这条问题标为"已修待验"（复评确认消失才 verified）
        try:
            from services.review_issues import mark_fixed
            if issue_index >= 0:
                from services.chapter_review import load_review
                review = await load_review(block_pk)
                issues = (review or {}).get("issues") or []
                if 0 <= issue_index < len(issues):
                    await mark_fixed(db, block_pk, [issues[issue_index]])
        except Exception as e:
            import logging as _logging
            _logging.getLogger(__name__).warning(f"标记已修失败：{e}")
    return {"applied": True, "content": updated}

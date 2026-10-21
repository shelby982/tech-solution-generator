"""待办 v3：可执行的交付清单（`待办-可操作交付清单-方案v3.md`）。

核心差别：不再输出"检查结果列表"，而是**任务卡**：
  哪里有问题（target）· 为什么重要（why）· 你要做什么（action）· 做完怎么算好（impact/verify）

分区（按"谁来消除"）：
  need_user  需要你处理（上传 / 决定 / 确认）
  system     系统处理中（不需要你操作）
  suggestion 建议优化（不影响交付）
"""
from __future__ import annotations

import hashlib
import re

IMPACT_TEXT = {
    "export_block": "不处理不能导出",
    "completion_block": "不处理不能确认完成",
    "quality": "影响评分/可读性",
    "suggestion": "可选优化",
}
_ACTION_LABEL = {"upload": "上传", "choose": "选一种说法", "view": "查看", "locate": "定位", "confirm": "确认"}
_SECTION_RE = re.compile(r"^#{2,6}\s+(.+?)\s*$")
_QUOTE_LIMIT = 60


def _task_id(category: str, key: str) -> str:
    digest = hashlib.sha1(f"{category}:{key}".encode("utf-8")).hexdigest()[:12]
    return f"{category}-{digest}"


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", "", str(text or ""))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _task(*, category: str, owner: str, title: str, **fields) -> dict:
    task = {
        "id": _task_id(category, fields.pop("dedupe_key", title)),
        "scope": "chapter",
        "category": category,
        "owner": owner,
        "title": title[:24],
        "target": fields.pop("target", {}) or {},
        "why": fields.pop("why", ""),
        "impact": fields.pop("impact", "suggestion"),
        "action": fields.pop("action", {"type": "locate", "label": "定位", "payload": {}}),
        "options": fields.pop("options", []),
        "evidence": fields.pop("evidence", []),
        "dedupe_key": fields.pop("dedupe_key", ""),
        "status": "open",
    }
    task["impact_text"] = IMPACT_TEXT.get(task["impact"], "")
    return task


def build_delivery_tasks(*, block_id: str, content: str, deliverables: list[dict],
                         material_gaps: list[dict], generation_gaps: list[dict],
                         word_plan: dict, decisions: list[dict], proofs: list[dict],
                         completion: dict | None = None) -> dict:
    """把检查结果重写成三区任务卡。返回 {need_user:[], system:[], suggestion:[], summary}。"""
    body = str(content or "")
    need_user: list[dict] = []
    system: list[dict] = []
    suggestion: list[dict] = []

    # ① 材料缺口（跨章会重复 → 给 dedupe_key，供项目级合并）
    for gap in material_gaps or []:
        kind = str(gap.get("kind") or "材料")
        lack = int(gap.get("lack") or 0)
        if not lack:
            continue
        if gap.get("availability") == "upload":
            need_user.append(_task(
                category="upload", owner="user",
                title=f"上传 {lack} 份{_short_kind(kind)}",
                dedupe_key=f"material:{kind}",
                target={"block_id": block_id, "section": gap.get("section", ""), "quote": ""},
                why=gap.get("purpose") or "评分点要求举证，公开渠道没有这类材料",
                impact="completion_block",
                action={"type": "upload_material", "label": "上传",
                        "payload": {"kind": kind, "lack": lack}},
                evidence=[{"label": "缺口", "text": gap.get("reason") or ""}],
            ))
        else:
            system.append(_task(
                category="system", owner="system",
                title=f"系统补 {lack} 份{_clip(kind, 8)}",
                dedupe_key=f"material-public:{kind}",
                why="属于公开信息，系统先查官方来源",
                impact="quality",
                action={"type": "system_search", "label": "查看", "payload": {"kind": kind}},
            ))

    # ② 依据缺口（11 条合成 1 张系统卡）
    pending_gaps = [g for g in (generation_gaps or []) if str(g.get("text") or "").strip()]
    if pending_gaps:
        system.append(_task(
            category="system", owner="system",
            title=f"系统补 {len(pending_gaps)} 条依据",
            dedupe_key="generation-gaps",
            why="生成时发现这些依据缺失，系统先去公开渠道查；查不到的会转成「需要你提供」",
            impact="quality",
            action={"type": "system_search", "label": "查看", "payload": {}},
            evidence=[{"label": "缺口", "text": _clip(g.get("text"), _QUOTE_LIMIT)} for g in pending_gaps[:5]],
        ))

    # ③ 决定项（口径/承诺/角色）：同类合并成一张卡，抽屉里逐条选
    decide_pool = [d for d in (decisions or []) if d.get("group") == "decide"]
    if decide_pool:
        section = _first_section(decide_pool, body)
        quote = _quote_of(decide_pool[0], body)
        if len(decide_pool) == 1:
            title = f"定 1 处口径"
        else:
            title = f"定 {len(decide_pool)} 处口径"
        need_user.append(_task(
            category="decide", owner="user", title=title,
            dedupe_key="decide-batch",
            target={"block_id": block_id, "section": section, "quote": quote},
            why=_clip(decide_pool[0].get("evidence"), 80) or "这些表述会影响承诺口径，交付前需要确定",
            impact="completion_block",
            action={"type": "choose", "label": "选一种说法", "payload": {"count": len(decide_pool)}},
            options=[{"label": s, "todo_id": _task_id("decide", str(d.get("target") or idx))}
                     for idx, d in enumerate(decide_pool) for s in (d.get("suggestions") or [])][:8],
            evidence=[{"label": "原句", "text": _clip(d.get("evidence"), _QUOTE_LIMIT)} for d in decide_pool[:3]],
        ))

    # ④ 图表缺口：系统生成 → 完成后请你确认
    for item in deliverables or []:
        if item.get("status") not in ("未写", "不足") or item.get("kind") == "text":
            continue
        label = "表格" if item.get("kind") == "table" else "图示"
        system.append(_task(
            category="system", owner="system",
            title=f"系统生成{label}「{_clip(item.get('title'), 10)}」",
            dedupe_key=f"gen:{item.get('title')}",
            why="计划要求但正文没有；系统生成后请你确认再插入",
            impact="quality",
            action={"type": "system_generate", "label": "查看", "payload": {"title": item.get("title")}},
        ))

    # ⑤ 文字不足：系统补写（不占用户待办）
    for item in deliverables or []:
        if item.get("kind") != "text" or item.get("status") not in ("未写", "不足"):
            continue
        system.append(_task(
            category="system", owner="system",
            title=f"系统补写「{_clip(item.get('title'), 10)}」",
            dedupe_key=f"write:{item.get('title')}",
            why=f"已写 {item.get('written_words') or 0} / 目标 {item.get('target_words')} 字",
            impact="quality",
            action={"type": "system_write", "label": "查看", "payload": {"title": item.get("title")}},
        ))

    # ⑥ 术语/数字/引用/格式：系统批量修
    proof_system = [p for p in (proofs or []) if p.get("group") == "proof" and p.get("fix")]
    if proof_system:
        system.append(_task(
            category="system", owner="system",
            title=f"系统统一 {len(proof_system)} 处表述",
            dedupe_key="proof-fix",
            why="术语/引用格式不一致，系统可批量统一，改完可撤销",
            impact="quality",
            action={"type": "system_fix", "label": "查看", "payload": {}},
        ))
    # 需要人工核对的（无自动修法）→ 同类合并成一张卡
    manual_proofs = [p for p in (proofs or []) if p.get("group") == "proof" and not p.get("fix")]
    groups: dict[str, list[dict]] = {}
    for proof in manual_proofs:
        title = str(proof.get("title") or "")
        if title.startswith("引用未标来源"):
            key = "引用来源"
        elif "术语" in title:
            key = "术语"
        elif "数字" in title or "数值" in title:
            key = "数字"
        elif "列数" in title or "表格" in title:
            key = "表格"
        else:
            key = "表述"
        groups.setdefault(key, []).append(proof)
    for key, items in groups.items():
        first = items[0]
        need_user.append(_task(
            category="confirm", owner="user",
            title=(f"核对 {key}" if len(items) == 1 else f"核对 {len(items)} 处{key}"),
            dedupe_key=f"check:{key}",
            target={"block_id": block_id, "section": _first_section(items, body),
                    "quote": _quote_of(first, body)},
            why=_clip(first.get("evidence"), 80),
            impact="quality",
            action={"type": "locate", "label": "定位", "payload": {}},
            evidence=[{"label": "示例", "text": _clip(i.get("evidence"), _QUOTE_LIMIT)} for i in items[:3]],
        ))

    # ⑦ 篇幅超建议 → 建议
    written = int(word_plan.get("written") or 0)
    target = int(word_plan.get("total") or 0)
    if target and written > target * 1.5:
        suggestion.append(_task(
            category="suggestion", owner="user",
            title=f"本节偏长：精简到约 {target} 字",
            dedupe_key="budget",
            why=f"当前 {written} 字，建议 {target} 字",
            impact="suggestion",
            action={"type": "condense_preview", "label": "预览精简", "payload": {"target": target}},
        ))

    # ⑧ 确认完成（未确认时给一张"确认"卡，而不是门槛列表）
    if completion is not None and not completion.get("ok", True) and (need_user or system):
        pass  # 完成条件不单独成卡；顶部状态条展示原因（v3 §5.2）

    summary = {
        "need_user": len(need_user),
        "system": len(system),
        "suggestion": len(suggestion),
        "impact_export_block": sum(1 for t in need_user + system if t["impact"] == "export_block"),
        "completion_reasons": (completion or {}).get("reasons") or [],
    }
    return {"need_user": need_user, "system": system, "suggestion": suggestion, "summary": summary}


def _first_section(items: list[dict], body: str) -> str:
    for item in items:
        evidence = str(item.get("evidence") or "")
        match = re.match(r"^(.+?)：", evidence)
        if match:
            return match.group(1).strip()
    return ""


def _quote_of(item: dict, body: str) -> str:
    evidence = str(item.get("evidence") or "")
    match = re.search(r"：(.+?)(…|$)", evidence)
    quote = (match.group(1) if match else evidence).strip()
    quote = re.sub(r"^.*?：", "", quote)
    return _clip(quote, _QUOTE_LIMIT)


_MATERIAL_SHORT = (
    ("业绩", "业绩材料"), ("案例", "案例材料"), ("资质", "资质文件"), ("合同", "合同/验收单"),
    ("客户", "客户证明"), ("数据", "内部数据"), ("内部", "内部材料"), ("政策", "政策原文"),
    ("标准", "标准条款"), ("制度", "制度原文"),
)


def _short_kind(kind: str) -> str:
    for word, short in _MATERIAL_SHORT:
        if word in str(kind):
            return short
    cleaned = re.sub(r"[（(].*?[)）]", "", str(kind)).strip()
    return _clip(cleaned or "材料", 10)

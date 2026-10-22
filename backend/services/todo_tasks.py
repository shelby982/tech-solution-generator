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
        "occurrences": fields.pop("occurrences", []),
        "materials_required": fields.pop("materials_required", []),
        "material_usage": fields.pop("material_usage", ""),
        "material_format": fields.pop("material_format", ""),
        "fallback": fields.pop("fallback", None),
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
            spec = _material_spec(kind)
            need_user.append(_task(
                category="upload", owner="user",
                title=f"上传 {lack} 份{_short_kind(kind)}",
                dedupe_key=f"material:{kind}",
                target={"block_id": block_id, "section": gap.get("section", ""), "quote": ""},
                why=gap.get("purpose") or "评分要求需要举证，公开渠道没有这类材料",
                impact="completion_block",
                action={"type": "upload_material", "label": "上传",
                        "payload": {"kind": kind, "lack": lack}},
                evidence=[{"label": "缺口", "text": gap.get("reason") or ""}],
                materials_required=spec["required"],
                material_usage=spec["usage"],
                material_format=spec["format"],
                fallback={"type": "soften_claim", "label": "没有这份材料，改成概括表述",
                          "payload": {"kind": kind}},
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
            title=f"补 {len(pending_gaps)} 条依据原文",
            dedupe_key="generation-gaps",
            why=("正文引用了这些文件但没取到原文，无法核验条款。" 
                 "点「现在去查」系统会逐条去公开来源检索，查到的自动挂到本章；"
                 "查不到的会告诉你只能由你提供"),
            impact="quality",
            action={"type": "search_now", "label": "现在去查", "payload": {"count": len(pending_gaps)}},
            evidence=[{"label": "缺口", "text": _clip(g.get("text"), _QUOTE_LIMIT)} for g in pending_gaps[:5]],
        ))

    # ③ 决定项（口径/承诺/角色）：同类合并成一张卡，抽屉里逐条选
    decide_pool = [d for d in (decisions or []) if d.get("group") == "decide"]
    if decide_pool:
        first = decide_pool[0]
        section = str(first.get("section") or _first_section(decide_pool, body))
        quote = _clean_quote(str(first.get("sentence") or ""), body) or _quote_of(first, body)
        need_user.append(_task(
            category="decide", owner="user",
            title=(f"定 1 处口径" if len(decide_pool) == 1 else f"定 {len(decide_pool)} 处口径"),
            dedupe_key="decide-batch",
            target={"block_id": block_id, "section": section, "quote": quote},
            why=("这句话把口径留成了「未定」，评委会认为方案没想清楚；改完才能确认本章完成"
                 if "未定" in str(first.get("title") or "") or "待明确" in quote
                 else "这句话是承诺口径，交付前需要确定说法"),
            impact="completion_block",
            action={"type": "choose", "label": "选一种说法", "payload": {"count": len(decide_pool)}},
            options=[{"label": label, "todo_id": str(item.get("target") or index)}
                     for index, item in enumerate(decide_pool)
                     for label in _derive_options(str(item.get("sentence") or ""), item.get("suggestions") or [])][:3],
            evidence=[{"label": "原句", "text": _clip(item.get("sentence") or item.get("evidence"), _QUOTE_LIMIT)}
                      for item in decide_pool[:3]],
        ))

    # ④ 图表缺口：系统生成 → 完成后请你确认
    for item in deliverables or []:
        if item.get("status") not in ("未写", "不足") or item.get("kind") == "text":
            continue
        label = "表格" if item.get("kind") == "table" else "图示"
        system.append(_task(
            category="system", owner="system",
            title=f"生成{label}「{_clip(item.get('title'), 10)}」",
            dedupe_key=f"gen:{item.get('title')}",
            why="计划要求这一节有它，但正文里还没有。现在就能生成，生成后你确认再插入正文",
            impact="quality",
            action={"type": "generate_chart", "label": "现在生成", "payload": {"title": item.get("title"), "kind": item.get("kind")}},
        ))

    # ⑤ 文字不足：系统补写（不占用户待办）
    for item in deliverables or []:
        if item.get("kind") != "text" or item.get("status") not in ("未写", "不足"):
            continue
        system.append(_task(
            category="system", owner="system",
            title=f"补写「{_clip(item.get('title'), 10)}」",
            dedupe_key=f"write:{item.get('title')}",
            why=f"这一节只有 {item.get('written_words') or 0} 字（目标 {item.get('target_words')} 字），点「现在补写」马上写",
            impact="quality",
            action={"type": "write_now", "label": "现在补写", "payload": {"title": item.get("title")}},
        ))

    # ⑥ 术语/数字/引用/格式：系统批量修
    proof_system = [p for p in (proofs or []) if p.get("group") == "proof" and p.get("fix")]
    if proof_system:
        system.append(_task(
            category="system", owner="system",
            title=f"统一 {len(proof_system)} 处表述",
            dedupe_key="proof-fix",
            why="术语/引用格式不一致。点「现在统一」会改正文并写版本，可撤销",
            impact="quality",
            action={"type": "fix_now", "label": "现在统一", "payload": {}},
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
        elif "指标" in title or "量化" in title:
            key = "指标"
        elif str(proof.get("source") or "") == "引用格式":
            key = "引用格式"
        elif str(proof.get("source") or "") == "表述强度":
            key = "表述"
        else:
            key = "表述"
        groups.setdefault(key, []).append(proof)
    for key, items in groups.items():
        first = items[0]
        if key == "引用格式":
            system.append(_task(
                category="system", owner="system",
                title="补引用文号与标准号",
                dedupe_key="cite:enrich",
                why=("政策的文号、等级保护的标准号都是确定信息，系统直接补齐并写版本；"
                     "补完给你确认，不满意可撤销"),
                impact="quality",
                action={"type": "enrich_citation", "label": "系统补齐", "payload": {}},
            ))
            continue
        if key in ("指标", "表述"):
            # 这两类系统能按小节契约重写，不需要用户手动改
            system.append(_task(
                category="system", owner="system",
                title=("补目标节的量化指标" if key == "指标" else f"改 {len(items)} 处模糊表述"),
                dedupe_key=f"rewrite:{key}",
                why=("目标节没有可验收数值，系统会按「指标名+目标值+验收口径」重写这一节，改完给你确认再保留"
                     if key == "指标" else
                     "「拟/将/待」这类表述会被认为没想清楚，系统按小节契约重写这几处，改完给你确认再保留"),
                impact="quality",
                action={"type": "rewrite_now", "label": "系统重写", "payload": {"key": key}},
                options=[],
            ))
            continue
        is_citation = key == "引用来源"
        if is_citation:
            system.append(_task(
                category="system", owner="system",
                title=(f"补 {len(items)} 处引用来源" if len(items) > 1 else "补 1 处引用来源"),
                dedupe_key="cite:auto",
                why=(f"这段引用了《{_policy_name(first)}》却没有【来源：…】。系统先去检索可信来源并插入；"
                     "查不到的才会转成「需要你提供」"),
                impact="quality",
                action={"type": "search_now", "label": "现在去查", "payload": {"count": len(items)}},
                occurrences=[{"section": str(x.get("section") or ""),
                              "quote": _clean_quote(str(x.get("sentence") or ""), body)}
                             for x in items],
            ))
            continue
        need_user.append(_task(
            category="confirm", owner="user",
            title=((f"补 {len(items)} 处引用来源" if len(items) > 1 else "补 1 处引用来源")
                   if is_citation else
                   (("补目标节的量化指标" if len(items) == 1 else f"补 {len(items)} 节的目标指标")
                    if key == "指标" else
                    ("补引用文号或标准号" if key == "引用格式" else
                     (f"改 {len(items)} 处模糊表述" if key == "表述" else
                      ("核对 1 处表述" if len(items) == 1 else f"核对 {len(items)} 处{key}"))))),
            dedupe_key=f"check:{key}",
            target={"block_id": block_id,
                    "section": str(first.get("section") or _first_section(items, body)),
                    "quote": _clean_quote(str(first.get("sentence") or ""), body)},
            occurrences=[{"section": str(x.get("section") or _first_section([x], body)),
                          "quote": _clean_quote(str(x.get("sentence") or ""), body)}
                         for x in items],
            why=(f"这段引用了《{_policy_name(first)}》却整段没有【来源：…】；评委会问出处，补上来源即可"
                 if is_citation else
                 ("目标节通篇没有可验收的数值（%，个，项，小时…）。每个目标给「指标名+目标值+验收口径」才拿得到分"
                  if key == "指标" else
                  ("政策缺文号、等级保护缺标准号、引用招标文件没到条款级 —— 这些都是评委核对依据时的扣分点"
                   if key == "引用格式" else
                   ("「拟/将/待」这类模糊表述会被认为没想清楚；建议挑关键的几处改成明确做法与指标"
                    if key == "表述" else f"这几处{key}不一致，建议统一后再交付")))),
            impact="quality",
            action=({"type": "add_citation", "label": "去补来源", "payload": {}}
                    if is_citation else {"type": "locate", "label": "定位", "payload": {}}),
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

    # ⑦b 交付门并入（内部语法/术语→系统可一键清理；占位符→需要你补）
    try:
        from services.delivery_gate import scan as gate_scan
        gate = gate_scan(body)
        kinds = (gate.get("summary") or {}).get("by_kind") or {}
        # 占位符已由系统在保存/生成时**自动净化**（正文不再出现"待补充"），不再生成待办卡
        # 内部语法/内部术语：系统会在保存与生成时**自动清理**，不出现在待办里（不需要用户参与）
    except Exception:
        pass

    # ⑧ 确认完成（未确认时给一张"确认"卡，而不是门槛列表）
    if completion is not None and not completion.get("ok", True) and (need_user or system):
        pass  # 完成条件不单独成卡；顶部状态条展示原因（v3 §5.2）

    # v3 §5.4 硬规则：带定位的条目，原句必须能在正文里逐字找到；找不到就不进待办
    need_user = [task for task in need_user if _locatable(task, body)]
    system = [task for task in system if _locatable(task, body)]
    # 交付就绪度：硬阻塞重罚，建议轻罚（0~100）
    hard = sum(1 for task in need_user if task["impact"] in ("export_block", "completion_block"))
    soft = sum(1 for task in need_user if task["impact"] not in ("export_block", "completion_block"))
    score = max(0, 100 - hard * 8 - soft * 5)
    # 产品原则：系统能做的**不进待办**（用户在计划里点一次「按计划编写」即可）。
    # 待办只留"只有人能给的东西"：上传材料 / 定口径 / 确认完成。
    system = [task for task in system
              if str(task.get("action", {}).get("type")) in ("confirm_complete",)]
    summary = {
        "readiness": score,
        "readiness_text": ("可以交付" if score >= 90 else "接近可交付" if score >= 70
                           else "还需处理" if score >= 40 else "不可交付"),
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
    ("业绩", "业绩材料"), ("案例", "案例材料"), ("资质", "资质证书"), ("合同", "合同/验收材料"),
    ("测评", "测评报告"), ("测评报告", "测评报告"), ("客户", "客户证明"),
    ("数据", "现状数据"), ("内部", "内部材料"), ("政策", "政策原文"),
    ("标准", "标准条款"), ("制度", "制度原文"),
)


def _short_kind(kind: str) -> str:
    for word, short in _MATERIAL_SHORT:
        if word in str(kind):
            return short
    cleaned = re.sub(r"[（(].*?[)）]", "", str(kind)).strip()
    return _clip(cleaned or "材料", 10)


def _clean_quote(sentence: str, body: str) -> str:
    """原句必须是正文里真实存在的一段（去表格噪音、截断到可定位长度）。"""
    text = re.sub(r"\s+", "", str(sentence or ""))
    if not text:
        return ""
    compact_body = re.sub(r"\s+", "", str(body or ""))
    for size in (40, 30, 20, 12):
        candidate = text[:size]
        if len(candidate) >= 10 and candidate in compact_body:
            return candidate
    return ""


def _policy_name(item: dict) -> str:
    text = str(item.get("title") or "")
    match = re.search(r"《(.+?)》", text)
    return match.group(1) if match else "相关政策文件"


def _derive_options(sentence: str, fallback: list[str]) -> list[str]:
    """从原句派生"能直接替换的说法"（比通用模板贴合得多）。"""
    sentence = str(sentence or "").strip()
    if not sentence:
        return list(fallback)[:3]
    # 「X 有待明确」「X 待明确」
    match = re.search(r"([^，。；：]{2,24}?)(有待明确|待明确|尚未明确)", sentence)
    if match:
        subject = match.group(1).strip("，。； ")
        return [
            f"改为「拟在实施阶段明确{subject}」",
            f"改为「{subject}将按实施方案明确」",
            "删掉这句话",
        ]
    # 「保证/确保/100%」承诺
    for word in ("保证", "确保", "绝不", "100%", "零故障"):
        if word in sentence:
            return [f"改为「按合同约定提供」", f"改为「通过措施保障」", "删掉该承诺"]
    if fallback:
        return list(fallback)[:3]
    return ["改成更确定的说法（自己写）", "删掉这句话"]


def _locatable(task: dict, body: str) -> bool:
    """带原句的任务：原句必须能在正文里找到（空白无关）；无原句的任务放行。"""
    quote = str((task.get("target") or {}).get("quote") or "").strip()
    if not quote:
        return True
    compact = re.sub(r"\s+", "", str(body or ""))
    return re.sub(r"\s+", "", quote) in compact


# ── 材料规格：说清"要什么 / 用在哪 / 什么格式 / 没有怎么办"────────────────
_MATERIAL_SPECS = {
    "业绩": {
        "required": [
            "中标通知书 或 合同关键页（首页+甲乙方+金额+签订日期+盖章页）",
            "验收报告 或 用户使用证明（可脱敏，能看出项目与时间）",
            "客户单位出具的项目证明（盖章）—— 可选，能加分",
        ],
        "usage": "用于「业绩/案例」类小节的举证，以及评分表中「类似项目经验」项",
        "format": "PDF 或清晰扫描件（JPG/PNG），单份 ≤20MB；关键页不要遮挡甲乙方、金额、日期",
    },
    "资质": {
        "required": ["营业执照", "相关资质证书（有效期内）", "体系认证证书（如有）"],
        "usage": "用于「投标人资格」「资质能力」类小节",
        "format": "PDF/扫描件，需含证书编号与有效期",
    },
    "数据": {
        "required": ["现有系统/业务的数据清单或统计表（脱敏即可）", "关键指标口径说明"],
        "usage": "用于「现状描述」「目标指标」类小节",
        "format": "Excel/PDF；无需逐条明细，给出维度与口径即可",
    },
    "测评": {
        "required": ["等级保护备案证明", "最近一次测评报告结论页"],
        "usage": "用于「安全现状」「合规差距」类小节",
        "format": "PDF；可只提供结论页",
    },
    "合同": {
        "required": ["合同关键页（甲乙方、金额、工期、签订日期）"],
        "usage": "用于「业绩」「履约能力」类小节",
        "format": "PDF/扫描件",
    },
}


def _material_spec(kind: str) -> dict:
    text = str(kind or "")
    for key, spec in _MATERIAL_SPECS.items():
        if key in text:
            return spec
    return {
        "required": [f"与「{_clip(text, 16)}」相关的证明材料（可脱敏）"],
        "usage": "用于本章对应小节的举证",
        "format": "PDF/扫描件，单份 ≤20MB",
    }

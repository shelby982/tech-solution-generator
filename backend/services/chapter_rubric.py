"""章节达标线（rubric）：按章类型给出"满分要素清单"，并核对是否达到。

为什么需要：评审说"缺量化事实"，用户不知道补到什么程度算够。
达标线把模糊建议变成**可勾选的清单**（现状类章必须有系统数/数据量/用户数/等保级别等）。

v1 用规则判（零成本、可解释），不调模型；识别不准时兜底 generic 清单。
"""
from __future__ import annotations

import re

# 章类型识别（按标题关键词，命中越多越优先；顺序即优先级）
TYPE_RULES = (
    ("status", ("现状", "背景", "概况", "问题", "差距", "需求分析", "理解")),
    ("plan", ("方案", "设计", "架构", "技术路线", "总体", "思路", "策略", "规划")),
    ("impl", ("实施", "进度", "计划", "里程碑", "组织", "保障", "安排")),
    ("team", ("团队", "人员", "组织", "职责", "配置", "投入")),
    ("policy", ("政策", "依据", "法规", "标准", "规范", "合规")),
    ("accept", ("验收", "质量", "测试", "交付", "服务", "运维", "培训")),
)
TYPE_LABEL = {"status": "现状/背景类", "plan": "方案/设计类", "impl": "实施/计划类",
              "team": "团队/组织类", "policy": "政策/依据类", "accept": "验收/服务类",
              "generic": "通用"}

# 每类 = 满分要素（check 用 L0 逻辑：数量/关键词/结构）
RUBRICS: dict[str, list[dict]] = {
    "status": [
        {"key": "numbers", "name": "量化事实", "need": "系统数量/数据量/用户数/并发/等保级别等具体数字（≥5 处）",
         "hint": "补 3~5 个可核实数字，并标【来源】", "weight": 3},
        {"key": "pain", "name": "现存问题", "need": "明确写出 2 条以上痛点/差距（而非只夸现状）",
         "hint": "写出「哪不行、影响什么」", "weight": 3},
        {"key": "source", "name": "事实来源", "need": "关键事实带【来源：…】或页码",
         "hint": "给数字和结论补来源", "weight": 2},
        {"key": "target", "name": "现状→目标衔接", "need": "现状结尾要落到「因此本项目要…」",
         "hint": "用一句话把现状问题接到项目目标", "weight": 2},
    ],
    "plan": [
        {"key": "structure", "name": "分层结构", "need": "总体/技术/数据/安全等分层清晰（或有架构图）",
         "hint": "按「接入-应用-数据-基础设施-安全」分层描述", "weight": 3},
        {"key": "means", "name": "手段具体", "need": "每个模块写到「用什么技术/组件/机制」",
         "hint": "避免只写「采用先进技术」，写具体组件与做法", "weight": 3},
        {"key": "indicator", "name": "指标与验收", "need": "关键能力给出可验证指标（时延/并发/可用性）",
         "hint": "每个能力配 1 个可验证指标", "weight": 3},
        {"key": "fit", "name": "回应招标要求", "need": "逐条对应招标技术要求（章节或条款号）",
         "hint": "写「对应技术规范书 X.X 条」", "weight": 2},
    ],
    "impl": [
        {"key": "phase", "name": "阶段划分", "need": "分阶段（设计/开发/测试/上线）且每阶段有交付物",
         "hint": "用表格列出阶段-时间-交付物", "weight": 3},
        {"key": "time", "name": "时间与里程碑", "need": "有明确起止时间/里程碑节点",
         "hint": "对齐招标要求工期", "weight": 3},
        {"key": "resource", "name": "资源保障", "need": "人力/环境/工具等资源安排",
         "hint": "写清角色与投入比例", "weight": 2},
        {"key": "risk", "name": "风险与应对", "need": "列出 2 条以上风险及应对措施",
         "hint": "进度风险/人员风险/技术风险各一条", "weight": 3},
    ],
    "team": [
        {"key": "roles", "name": "角色与职责", "need": "角色清单 + 每人职责",
         "hint": "用表格：角色-人数-职责-投入比例", "weight": 3},
        {"key": "qual", "name": "资历证明", "need": "关键人员有证书/项目经历",
         "hint": "附证书与业绩，标【来源】", "weight": 3},
        {"key": "org", "name": "组织与协同", "need": "与甲方/分包/第三方的协同机制",
         "hint": "写例会/汇报/升级路径", "weight": 2},
    ],
    "policy": [
        {"key": "cite", "name": "政策原文引用", "need": "引用具体文件名/文号/条款",
         "hint": "写全称与文号，不要只写「相关政策」", "weight": 3},
        {"key": "map", "name": "条款→响应映射", "need": "每条要求对应到本方案的哪个部分",
         "hint": "用表格：条款-要求-响应章节", "weight": 3},
        {"key": "source", "name": "来源可核", "need": "带【来源】或页码",
         "hint": "补来源标注", "weight": 2},
    ],
    "accept": [
        {"key": "criteria", "name": "验收标准", "need": "可量化验收指标（不是「满意为止」）",
         "hint": "写指标+阈值+验证方法", "weight": 3},
        {"key": "method", "name": "验证方法", "need": "自测/第三方/甲方验收的方式",
         "hint": "说明由谁用什么方法验", "weight": 2},
        {"key": "service", "name": "服务承诺", "need": "响应时间/到场时间/SLA",
         "hint": "给具体小时数", "weight": 3},
    ],
    "generic": [
        {"key": "numbers", "name": "量化事实", "need": "至少 5 处具体数字", "hint": "补数字与来源", "weight": 3},
        {"key": "source", "name": "事实来源", "need": "关键结论带【来源】", "hint": "补来源", "weight": 2},
        {"key": "structure", "name": "结构完整", "need": "有小节划分且每节有实体内容", "hint": "补小节与内容", "weight": 2},
        {"key": "action", "name": "可执行", "need": "写出具体做法而非空话", "hint": "把「加强/提升」换成具体动作", "weight": 3},
    ],
}

_SOURCE = re.compile(r"【来源|（第\s*\d+\s*页")
_ARCH_HINT = re.compile(r"(:::diagram|架构图|分层|接入层|应用层|数据层|基础设施|安全体系)")


def detect_type(title: str, content: str = "") -> str:
    """识别章类型；标题命中优先，其次正文特征。"""
    text = str(title or "")
    best, best_hits = "generic", 0
    for type_key, keywords in TYPE_RULES:
        hits = sum(1 for keyword in keywords if keyword in text)
        if hits > best_hits:
            best, best_hits = type_key, hits
    if best == "generic" and content:
        body = str(content)
        if len(re.findall(r"\d", body)) >= 5 and ("现状" in body or "目前" in body):
            best = "status"
    return best


def rubric_for(title: str, content: str = "") -> dict:
    """返回该章的达标线定义 + 类型。"""
    type_key = detect_type(title, content)
    return {"chapter_type": type_key, "chapter_type_label": TYPE_LABEL.get(type_key, "通用"),
            "items": RUBRICS.get(type_key) or RUBRICS["generic"]}


def _hit_fragment(content: str, pattern: str, window: int = 40) -> str:
    match = re.search(pattern, content or "")
    if not match:
        return ""
    start = max(0, match.start() - window // 2)
    return re.sub(r"\s+", "", content[start:match.start() + window])


def check_rubric(content: str, rubric: dict) -> dict:
    """按规则核对达标线：逐条 met + 命中片段。"""
    body = str(content or "")
    compact = re.sub(r"\s+", "", body)
    digits = len(re.findall(r"\d", body))
    has_source = bool(_SOURCE.search(body))
    has_arch = bool(_ARCH_HINT.search(body))
    paragraphs = [p for p in re.split(r"\n\s*\n", body) if len(re.sub(r"\s+", "", p)) > 40]

    checks: dict[str, tuple[bool, str]] = {
        "numbers": (digits >= 5, f"当前 {digits} 处数字"),
        "source": (has_source, "已标来源" if has_source else "无来源标注"),
        "pain": (any(word in body for word in ("问题", "不足", "差距", "痛点", "尚未", "尚未建立")),
                 _hit_fragment(body, r"(问题|不足|差距|痛点)")),
        "target": (("因此" in body or "为此" in body or "本项目" in body), _hit_fragment(body, r"(因此|为此|本项目将)")),
        "structure": (has_arch or len(paragraphs) >= 3, "有分层/结构" if has_arch else f"{len(paragraphs)} 段"),
        "means": (any(word in body for word in ("采用", "基于", "通过", "使用", "集成", "调用")),
                  _hit_fragment(body, r"(采用|基于|集成)")),
        "indicator": (bool(re.search(r"(\d+\s*(%|毫秒|秒|分钟|小时|万|TB|GB|并发|可用性))", body)),
                      _hit_fragment(body, r"\d+\s*(%|毫秒|秒|分钟|小时|万|TB|GB)")),
        "fit": (bool(re.search(r"(第\s*\d+(\.\d+)?\s*[条章节]|技术规范书|招标文件|★)", body)),
                _hit_fragment(body, r"(技术规范书|招标文件|第\s*\d+\s*[条章节])")),
        "phase": (any(word in body for word in ("阶段", "一期", "二期", "第一阶段")), _hit_fragment(body, r"(阶段|一期|二期)")),
        "time": (bool(re.search(r"(\d{4}\s*年|\d{1,2}\s*月|季度)", body)), _hit_fragment(body, r"\d{4}\s*年")),
        "resource": (any(word in body for word in ("人员", "人力", "投入", "资源", "工作组")), _hit_fragment(body, r"(人员|投入|资源)")),
        "risk": (any(word in body for word in ("风险", "应对", "预案", "措施")), _hit_fragment(body, r"(风险|应对)")),
        "roles": (any(word in body for word in ("项目经理", "角色", "职责", "岗位")), _hit_fragment(body, r"(项目经理|角色|职责)")),
        "qual": (any(word in body for word in ("证书", "资质", "业绩", "经历", "职称")), _hit_fragment(body, r"(证书|资质|业绩)")),
        "org": (any(word in body for word in ("例会", "汇报", "协同", "沟通", "对接")), _hit_fragment(body, r"(例会|协同|沟通)")),
        "cite": (bool(re.search(r"(《[^》]{4,40}》|[\u4e00-\u9fff]{2,12}〔\d{4}〕|\d+号)", body)), _hit_fragment(body, r"《[^》]{4,40}》")),
        "map": (bool(re.search(r"(对应|映射|逐条|条款)", body)), _hit_fragment(body, r"(对应|逐条)")),
        "criteria": (bool(re.search(r"(\d+\s*(%|分|项|天|次))", body)), _hit_fragment(body, r"\d+\s*(%|项|天)")),
        "method": (any(word in body for word in ("验收", "测试", "测评", "验证", "第三方")), _hit_fragment(body, r"(验收|测试|第三方)")),
        "service": (bool(re.search(r"(\d+\s*(小时|个月)|SLA|响应时间)", body)), _hit_fragment(body, r"\d+\s*小时")),
        "action": (any(word in body for word in ("通过", "采用", "建立", "配置", "实现", "落地")), _hit_fragment(body, r"(通过|建立|配置)")),
    }

    items, met_count, total_weight, got_weight = [], 0, 0, 0
    for item in rubric.get("items") or []:
        met, note = checks.get(item["key"], (False, ""))
        weight = int(item.get("weight") or 1)
        total_weight += weight
        if met:
            met_count += 1
            got_weight += weight
        items.append({**item, "met": bool(met), "evidence": note})
    return {"chapter_type": rubric.get("chapter_type"), "items": items,
            "met_count": met_count, "total": len(items),
            "ratio": round(got_weight / total_weight * 100) if total_weight else 0}

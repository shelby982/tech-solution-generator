"""技术要求矩阵抽取：把技术规范书拆成结构化的"技术要求条目"。

定位（用户 2026-10-12 的产品流程设计）：

    招标文件与技术规范书回答的是**两个不同问题**——
    招标文件（评分标准）规定「如何评标」，技术规范书规定「具体要做什么」。

        项目文件输入
          ├─ 招标文件   → 评分规则解析   → 评分要求矩阵（S01…）
          └─ 技术规范书 → 技术要求解析   → 技术要求矩阵（R01…）
                        ↓
                  统一应标要求矩阵
                        ↓
                  技术应答目录生成

    所以技术规范书必须在**解析阶段**就结构化，而不是等到目录生成之后再补。
    并且**不能在每个阶段重新解析整份规范书** —— 初始解析一次，后续引用条目 ID 与原文，
    既省 token，也避免不同 agent 对同一条款产生不一致理解。

本模块负责其中的「技术要求解析」：输出 ``TechRequirement`` 列表。

抽取范围与口径（实测该规范书 `4_高可靠技术专题研究与验证-技术招标文件.docx`）：
- 载体有三种：**编号列表项**（``1. xxx：yyy``）、**章节标题**（工作项 / 工具）、
  **交付物表行**（任务域 → 任务项 → 预期成果）。三类都要收，否则会漏。
- ``mandatory_level``：从原文的义务词判定（必须/须/不得 → mandatory；应/需 → required；
  其余 → informational）。规范书里没有独立章节号的要求，原文位置用「节标题 + 条目序号」表达。
- ``verification_method`` 与 ``related_scoring_ids``：规范书本身**不写**这两项，
  留空为 None。前者属"关联节点/人工确认"，后者由评分↔技术要求关联节点回填。
    宁可留空也不要让模型编 —— 编出来的关联会污染下游的覆盖度统计。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 要求类型
# ─────────────────────────────────────────────

TYPE_RESEARCH = "research_task"        # 研究任务（梳理现状 / 研究方案）
TYPE_VERIFY = "verification"           # 验证要求（验证技术方案）
TYPE_DELIVERABLE = "deliverable"       # 交付成果
TYPE_STANDARD = "standard"             # 应遵循的标准 / 规范
TYPE_PERSONNEL = "personnel"           # 人员与团队要求
TYPE_SERVICE = "service"               # 服务与管理要求（实施/质量/进度/结算…）
TYPE_SECURITY = "security"             # 信息安全与保密
TYPE_IPR = "ipr"                       # 知识产权
TYPE_INDICATOR = "indicator"           # 量化指标类要求
TYPE_OBJECTIVE = "objective"           # 项目目标 / 背景类（背景知识，非强制条目）

# 章节标题 → 要求类型的规则（**先命中先用**，所以特异性高的必须排在前面）。
# 实测踩到：把「工作项|研究|梳理|归集|方案」放前面时，「项目交付项」「人员要求」
# 都会被它吃掉 —— 该模式里的"项"会命中"交付项"。
_HEADING_TYPE_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"验证"), TYPE_VERIFY),
    (re.compile(r"交付项|交付物|预期成果|工具|研发"), TYPE_DELIVERABLE),
    (re.compile(r"遵循标准|标准|规范"), TYPE_STANDARD),
    (re.compile(r"人员|团队|组织保证"), TYPE_PERSONNEL),
    (re.compile(r"知识产权|专利|论文"), TYPE_IPR),
    (re.compile(r"信息安全|保密|安全要求"), TYPE_SECURITY),
    (re.compile(r"验收|质量|进度|项目管理|服务"), TYPE_SERVICE),
    (re.compile(r"工作项|研究|梳理|归集|方案|现状"), TYPE_RESEARCH),
    (re.compile(r"项目目标|背景|概述|定义"), TYPE_OBJECTIVE),
    (re.compile(r"招标范围"), TYPE_SERVICE),
]

# 义务级别
MANDATORY = "mandatory"      # 必须 / 须 / 不得（不满足即偏离）
REQUIRED = "required"        # 应 / 需要 / 需
INFORMATIONAL = "informational"

_MANDATORY_RE = re.compile(r"必须|须|不得|严禁|不允许|禁止")
_REQUIRED_RE = re.compile(r"应当|应|需要|需|要求")

# 量化指标：数字 + 单位/量词（35 名、12 小时、5 个工作日、12 个月、不少于 7 项…）
# 顺序有讲究：**带单位/量词的完整形态优先**（「至少35名」不该被拆成裸「至少35」），
# 裸阈值（「不少于7项」中数字与量词分离的写法）放最后兜底。
_INDICATOR_RE = re.compile(
    r"(?:不少于|至少|不低于|不超过|不高于|≥|<=|≤|>)\s*\d+(?:\.\d+)?\s*"
    r"(?:名|人|小时|个工作日|工作日|个月|天|周|年|项|篇|%|％|分)?"
    r"|\d+(?:\.\d+)?\s*(?:名|人|小时|个工作日|工作日|个月|天|周|年|项|篇|%|％|分)"
)
# 可验证动作：形成/提交/编制/提供 …报告/方案/清单/记录（用于推断"如何验证满足"）
_ARTIFACT_RE = re.compile(r"报告|方案|清单|说明书|规范|记录|台账|证明|证书|PDM|文档|工具")

# 「1. 标题：正文」「1、标题」「1）标题」等编号项
_NUMBERED_ITEM_RE = re.compile(r"^\s*(\d{1,2})\s*[.、)）]\s*(\S.*)$")
# 交付物表：「序号 | 任务域 | 任务项 | 预期成果」
_DELIVERABLE_HEADER = ("序号", "任务域", "任务项")


@dataclass
class TechRequirement:
    """技术规范书里的一条要求。"""
    requirement_id: str = ""            # R01、R02…（按抽取顺序编号，稳定）
    requirement_type: str = ""          # 见 TYPE_*
    requirement_text: str = ""          # 原文（含小标题时一并保留，便于回溯）
    package_id: str = ""                # 所属标包（本次为"标包2"）
    source_location: str = ""           # 「节标题 · 条目 n」，规范书无独立条款号
    mandatory_level: str = INFORMATIONAL
    verification_method: Optional[str] = None   # 规范书不写；由关联节点/人工补
    related_scoring_ids: list[str] = field(default_factory=list)  # 由关联节点回填
    indicators: list[str] = field(default_factory=list)          # 原文里的量化指标
    topic: str = ""                     # 所属技术专题（用于对齐评分点 S03-B 这类）

    def to_dict(self) -> dict:
        return {
            "requirement_id": self.requirement_id,
            "requirement_type": self.requirement_type,
            "requirement_text": self.requirement_text,
            "package_id": self.package_id,
            "source_location": self.source_location,
            "mandatory_level": self.mandatory_level,
            "verification_method": self.verification_method,
            "related_scoring_ids": list(self.related_scoring_ids),
            "indicators": list(self.indicators),
            "topic": self.topic,
        }


def classify_mandatory(text: str) -> str:
    """按义务词判定强制级别。must > should > 说明性。"""
    t = text or ""
    if _MANDATORY_RE.search(t):
        return MANDATORY
    if _REQUIRED_RE.search(t):
        return REQUIRED
    return INFORMATIONAL


def extract_indicators(text: str) -> list[str]:
    """抽原文里的量化指标（去重、保持出现顺序）。"""
    out: list[str] = []
    for m in _INDICATOR_RE.finditer(text or ""):
        v = re.sub(r"\s+", "", m.group(0))
        if v not in out:
            out.append(v)
    return out


def classify_heading(title: str) -> str:
    """章节标题 → 要求类型。"""
    t = title or ""
    for pattern, type_ in _HEADING_TYPE_RULES:
        if pattern.search(t):
            return type_
    return TYPE_RESEARCH


def _cells(line: str) -> list[str]:
    line = (line or "").strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(set(c) <= {"-", ":", " "} for c in cells if c)


def _topic_of(section_path: list[str]) -> str:
    """技术专题 = 章节路径里最贴近内容的那个专题名（如"信创适配研究及验证"）。"""
    for name in reversed(section_path or []):
        if re.search(r"研究及验证|专题|工具|模型梳理研究", name or ""):
            return name
    return (section_path[-1] if section_path else "")


# 叙述性/说明性文本的识别。为什么必须滤：这类段落会被当成"技术要求"抽出来，
# 覆盖率里的分母被污染 —— 实测 208 条里混进「本技术招标文件适用于…」
# 「可供投标人编写投标文件之用」等说明句，落点率因此虚低（131/208），
# 用户看到的是一个假缺口（覆盖率自检把它暴露了出来）。
_NARRATIVE_PREFIXES = (
    "本技术招标文件", "本招标文件", "本规范", "本文", "本文件", "本章",
    "投标人应按照本技术招标文件", "投标人提交的项目交付物除了",
)
# 可执行性信号：出现任一才认为是"要去响应/满足"的要求
_ACTIONABLE = ("应", "须", "需", "必须", "不得", "不少于", "不低于", "不超过",
               "提供", "提交", "工作项", "研究", "验证", "工具", "指标", "成果",
               "交付", "承诺", "满足", "符合", "得", "分")


# 术语定义：`招标方：广东电网有限责任公司。`（实测被抽成"技术要求"）
_DEFINITION_RE = re.compile(
    r"^(招标方|投标方|中标方|甲方|乙方|招标人|投标人|采购人|供应商)\s*[：:]"
    r"|指响应投标|指经过评标|被授予合同的投标人|指响应招标"
)


# 上下文节：只提供背景，不是"要去响应"的要求。它们（尤其"定义"）的正文常是编号列表，
# 会被载体②逐条抽成"技术要求" —— 实测混进「招标方：广东电网有限责任公司。」
# 「投标方：指响应投标、参加投标竞争的法人。」这类术语定义，污染覆盖率分母。
_CONTEXT_SECTION_TITLES = ("总则", "概述", "定义", "术语", "招标范围", "遵循标准",
                           "项目背景", "项目目标", "项目描述")


def _is_context_section(title: str) -> bool:
    t = (title or "").strip()
    return any(x in t for x in _CONTEXT_SECTION_TITLES)


def _is_narrative(text: str) -> bool:
    t = (text or "").strip()
    if any(t.startswith(p) for p in _NARRATIVE_PREFIXES):
        return True
    if _DEFINITION_RE.search(t):
        return True
    if "可供投标人编写投标文件之用" in t:
        return True
    # 长句且没有任何可执行性措辞 → 当叙述处理
    if len(t) >= 40 and not any(w in t for w in _ACTIONABLE):
        return True
    return False


def extract_tech_requirements(
    sections: list,
    *,
    package_id: str = "",
    source_file: str = "",
) -> list[TechRequirement]:
    """把技术规范书的 sections 拆成技术要求条目。

    三种载体都收（缺一类就会漏要求）：
    1. **章节标题**（**有**正文的节）→ 一条要求（工作项 / 工具 / 要求类小节）；
    2. **编号列表项**（节正文里的 ``1. xxx：yyy``）→ 各一条，逐条可追溯；
    3. **交付物表行** → 一条要求，原文含任务项与预期成果。

    纯容器节（无正文、只用来分组的，如"项目内容与要求""项目内容"）不产出条目 ——
    它们不是要求，产出会让矩阵充满空行。

    返回按文档顺序排列，``requirement_id`` 从 R01 起连续编号（稳定、可引用）。
    """
    out: list[TechRequirement] = []
    counter = 0

    # 本规范书自身涉及的任务域（用节标题做白名单）。
    # 交付物表会**跨标包**列出全部标的的任务域 —— 实测该表混进了
    # 「统一数据模型梳理研究」「数据迁移研究及验证」等属其他标包的行，
    # 不筛掉会让本标包的技术要求矩阵凭空多出别人的要求。
    known_domains = {
        (sec.get("title", "") if isinstance(sec, dict) else getattr(sec, "title", "")) or ""
        for sec in (sections or [])
    }
    known_domains = {d.strip() for d in known_domains if d.strip()}
    _seen: set[str] = set()

    def _domain_is_own(domain: str) -> bool:
        d = (domain or "").strip()
        if not d:
            return False
        if d in known_domains:
            return True
        # 表格里的任务域可能带尾注（如「…（含数据安全）」），做包含匹配兜底
        return any(d in k or k in d for k in known_domains)

    def _get(sec, name: str, default=""):
        return sec.get(name, default) if isinstance(sec, dict) else getattr(sec, name, default)

    def _add(rtype: str, text: str, location: str, topic: str) -> None:
        nonlocal counter
        text = (text or "").strip()
        if not text:
            return
        if _is_narrative(text):
            return
        # 去重：交付物表里存在重复行（实测信创适配「工作项4」出现两次），
        # 同一内容只保留第一次，避免矩阵里出现两条一模一样的可响应条目。
        key = re.sub(r"\s+", "", text)
        if key in _seen:
            return
        _seen.add(key)
        counter += 1
        out.append(TechRequirement(
            requirement_id=f"R{counter:02d}",
            requirement_type=rtype,
            requirement_text=text,
            package_id=package_id,
            source_location=location,
            mandatory_level=classify_mandatory(text),
            indicators=extract_indicators(text),
            topic=topic,
        ))

    path: list[str] = []
    for sec in sections or []:
        level = int(_get(sec, "level", 1) or 1)
        title = (_get(sec, "title", "") or "").strip()
        content = _get(sec, "raw_content", "") or ""
        # 维护章节路径（按 level 截断），用于给条目定位所属技术专题
        if level <= 0:
            level = 1
        path = path[: level - 1]
        while len(path) < level - 1:
            path.append("")
        path.append(title)
        topic = _topic_of(path)

        # 上下文节（总则/概述/定义/术语/招标范围/遵循标准/项目背景/项目目标）：
        # 只维护章节路径，不产出"技术要求" —— 它们不是要去响应的要求。
        if _is_context_section(title):
            continue

        if not content.strip():
            continue                       # 纯容器节：不是要求

        location_base = " > ".join(x for x in path if x)

        # ① 交付物表：按行产出，附上任务项与预期成果
        table_lines = [l for l in content.splitlines() if l.strip().startswith("|")]
        if table_lines:
            for line in table_lines:
                cells = _cells(line)
                if _is_separator(cells) or not cells:
                    continue
                joined = "".join(cells)
                if all(h in joined for h in _DELIVERABLE_HEADER):
                    continue               # 表头
                if len(cells) >= 4 and cells[0].isdigit():
                    domain, task, artifacts = cells[1], cells[2], cells[3]
                    if not _domain_is_own(domain):
                        continue       # 其他标包的任务域，不属于本包
                    _add(
                        TYPE_DELIVERABLE,
                        f"{domain} / {task} → 预期成果：{artifacts}",
                        f"{location_base} · 交付物表第 {cells[0]} 行",
                        domain or topic,
                    )
            continue                        # 表格已消化，不再当列表项拆

        # ② 编号列表项：逐条产出（投标人须逐条响应，颗粒度必须到条）
        items = [l.strip() for l in content.splitlines() if l.strip()]
        numbered = [(i, _NUMBERED_ITEM_RE.match(l)) for i, l in enumerate(items)]
        numbered = [(i, m) for i, m in numbered if m]
        if numbered:
            for n, (_, m) in enumerate(numbered, start=1):
                body = m.group(2).strip()
                _add(
                    classify_heading(title) if title else TYPE_RESEARCH,
                    body,
                    f"{location_base} · 第 {n} 条",
                    topic,
                )
            continue

        # ③ 整节正文当作一条要求（无编号列表的节，如"人员要求""知识产权要求"）
        _add(classify_heading(title), content.strip(), location_base, topic)

    return out


__all__ = [
    "INFORMATIONAL",
    "MANDATORY",
    "REQUIRED",
    "TYPE_DELIVERABLE",
    "TYPE_INDICATOR",
    "TYPE_IPR",
    "TYPE_OBJECTIVE",
    "TYPE_PERSONNEL",
    "TYPE_RESEARCH",
    "TYPE_SECURITY",
    "TYPE_SERVICE",
    "TYPE_STANDARD",
    "TYPE_VERIFY",
    "TechRequirement",
    "classify_heading",
    "classify_mandatory",
    "extract_indicators",
    "extract_tech_requirements",
]

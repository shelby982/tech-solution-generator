"""应答文件目录的**确定性**骨架。

## 为什么要有这个模块（2026-10-13 用户反馈）
用户原话：
  「目前拆分还是只按照得分项，得分项里面的具体评分点没有在目录里面拆分出来。
    技术规范书里面的内容没有结合到整体的章节目录里面去。
    这一步是非常关键的，需要稳定地按照评分点和技术规范书的内容结合，输出专业目录。
    请核实如何保障它能够稳定地输出目录结构，不要一会儿变这样，一会儿变那样。」

实测确认的两个缺陷：
1. 模型只拆到「评分因素 → 详细评审项」两层（而且这两层常常同名，于是出现
   `L1 技术方案 / L2 技术方案` 这种重复节点），**分项要素那一层从未拆出来** ——
   而"方案可行性 / 方案先进性""项目背景/现状/目标/重难点"这些细分点，
   原文就写在分项要素里。
2. 技术规范书 54 节正文此前**一个字都没进材料**（只作为未选中标题列了一行），
   所以目录里看不到规范书的结合点。

## 稳定性从哪来
**结构不再由模型生成**。评分表与规范书都已经在解析层结构化：
- 评分表：``ReviewPoint``（factor 评分因素 / item 详细评审项 / criterion 分项要素 / points 分值 / bands 档位）；
- 规范书：章节树（研究专题 → 工作项）。
本模块只用**确定性规则**把它们拼成目录：
  L1 评分因素 → L2 详细评审项 → L3 分项要素细分点 → L4 规范书对应的工作项
同样的输入必然得到同样的目录（有单测钉死），因此不再"一会儿这样一会儿那样"。
模型只在需要润色标题时才参与，且失败即回落到本模块的结果。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

# 分项要素里的枚举前缀：`1、` `2.` `（1）` `(1)` `一、` …
_ENUM_RE = re.compile(
    r"(?:^|(?<=[。；;]))\s*(?:([0-9０-９]{1,2})\s*[、.．]|（([0-9０-９一二三四五六七八九十]{1,3})）|\(([0-9]{1,2})\)|([一二三四五六七八九十]{1,2})\s*[、.．])"
)

# 档位/分值描述（"得8-12分""优良（7-10分）"）：不作章节，只作为该点的评分口径
_SCORE_RE = re.compile(r"(得\s*[0-9０-９]+\s*[-–~至]?\s*[0-9０-９]*\s*分|[0-9０-９]+\s*[-–~]\s*[0-9０-９]+\s*分|不得分|满分)")

# 分项要素里"标题：正文"的标题部分（如「1、方案可行性：…」→ 方案可行性）
_LABEL_RE = re.compile(r"^\s*([^：:。；;]{2,20})\s*[：:]")

# 规范书里表达"研究与验证"的层级词：这些下面才是工作项
_TOPIC_LEVEL_HINTS = ("研究", "验证", "工具", "要求", "保障")

# 规范书章节标题里的噪音词（不参与主题匹配）
_SPEC_TITLE_NOISE = ("工作项", "梳理分析", "明确工作目标", "确定工作目标", "研究技术方案",
                     "验证技术方案", "运行保障管理方案", "含数据安全")


@dataclass
class OutlineNode:
    """目录节点。``source`` 标出它来自哪一侧，便于用户核对与调试。"""
    level: int
    title: str
    source: str = "scoring"          # scoring | spec | merged
    factor: str = ""                 # 所属评分因素
    item: str = ""                   # 所属详细评审项
    points: Optional[float] = None   # 该评分点分值（放在最近的有分值节点上）
    criterion: str = ""              # 对应的分项要素原文（写作时的评分口径）
    spec_refs: list[str] = field(default_factory=list)   # 并入的规范书章节标题

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "title": self.title,
            "source": self.source,
            "factor": self.factor,
            "item": self.item,
            "points": self.points,
            "criterion": self.criterion,
            "spec_refs": list(self.spec_refs),
        }


def split_criterion(text: str) -> list[tuple[str, str]]:
    """把分项要素拆成 ``[(小标题, 正文), ...]``。

    两件事：
    1. 按枚举前缀切段（``1、方案可行性：（1）…（2）…``）；
    2. 段内若以「标题：正文」开头，标题单独取出来当小标题。

    实测（标包2 技术方案的分项要素）：
      ``1、方案可行性：（1）遵循…（2）…2、方案先进性：（1）…信创适配、云原生、容灾…``
    → ``[("方案可行性", "（1）遵循…"), ("方案先进性", "（1）…信创适配…")]``
    """
    raw = (text or "").strip()
    if not raw:
        return []
    # 先按「数字、」这种**顶层**枚举切（`1、`/`2、`），它比分项要素里的（1）（2）层级更高
    top = re.split(r"(?=(?:^|(?<=[。；;]))\s*[0-9０-９]{1,2}\s*[、.．])", raw)
    chunks = [c.strip() for c in top if c.strip()]
    if len(chunks) <= 1:
        # 没有顶层枚举：整段作为一块，标题从「标题：」里取
        chunks = [raw]

    out: list[tuple[str, str]] = []
    for chunk in chunks:
        chunk = re.sub(r"^\s*[0-9０-９]{1,2}\s*[、.．]\s*", "", chunk).strip()
        if not chunk:
            continue
        label, body = _split_label(chunk)
        if label:
            out.append((label, body or chunk))
            continue

        # 顿号清单：`深刻理解项目背景、现状、目标、建设范围、建设内容、项目重难点等`
        # → 逐项成章。这是「具体评分点」最常见的写法，必须拆出来，
        # 否则只能得到一个被截断的长句标题（实测就是这样）。
        topics = _list_topics(chunk)
        if len(topics) >= 3:
            for t in topics:
                out.append((t, chunk))
            continue

        # 档位式条款（优良/中等/一般/未响应）：没有细分标签，但它其实在讲
        # 「响应度 / 优于要求 / 负偏差」三件事 —— 用标准落点，保证有可写的章节。
        banded = _band_topics(chunk)
        if banded:
            for t in banded:
                out.append((t, chunk))
            continue

        # 兜底：取第一个句子的**首个短语**（在逗号/顿号/冒号处断开），
        # 而不是硬截 24 字 —— 硬截会产出「1）具备科学、合理的项目管理方案，项目管理方法完」
        # 这种半句垃圾（实测）。
        sentence = re.split(r"[。；;]", chunk)[0]
        sentence = re.sub(r"^\s*[（(]?[0-9０-９]{1,2}[）)、.]?\s*", "", sentence)
        sentence = _SCORE_RE.sub("", sentence).strip("（( ）)、,，")
        head = re.split(r"[，,、：:]", sentence)[0].strip()
        title = (head or sentence or chunk)[:22]
        out.append((title, chunk))
    return out


# 档位式条款里反复出现的两件事 —— 招标文件用词高度固定，所以用确定性映射，
# 不猜、不调模型（要稳定就不能让模型自由发挥）。
_BAND_TOPIC_RULES = (
    (("优于",), "优于招标技术要求的条款"),
    (("负偏差", "未响应", "未作响应"), "非实质性负偏差及未响应说明"),
    (("完全满足", "满足招标技术要求", "响应度"), "技术响应度说明"),
)

# 「、」清单里不成词的噪音（避免把整句当成清单项）
_LIST_STOP = ("等", "等等", "相关", "有关")


def _list_topics(chunk: str) -> list[str]:
    """从句子里抽出顿号清单项（≥3 项才算清单）。"""
    best: list[str] = []
    for sentence in re.split(r"[。；;]", chunk):
        # 顿号清单 + 「和/及/与/或」并列：后者是短清单的常见写法
        # （`实施计划…、保障措施` 这类只有两三项，按"≥3 项才算清单"会漏掉，
        #   结果 L3 标题退化成被截断的长句 —— 实测就是这样）。
        parts = [p.strip("（()）【】 　") for p in re.split(r"[、]", sentence)]
        if len(parts) < 3:
            parts = [p.strip("（()）【】 　") for p in re.split(r"[、和及与或]", sentence)]
        parts = [re.sub(r"^[（(]?[0-9０-９一二三四五六七八九十]{1,3}[）)、.]?", "", p).strip()
                 for p in parts]
        cand = [p for p in parts if 2 <= len(p) <= 16 and p not in _LIST_STOP]
        if len(cand) >= 2 and len(cand) > len(best):
            best = cand
    return [c for c in best if not _SCORE_RE.fullmatch(c)]


# 真正的档位词：出现两个以上才认定这是"分档描述"而非普通条款。
# 收紧原因：`交付成果满足要求` 的原文里有"满足项目要求"，被旧规则误判成档位条款，
# 于是给它编出"优于招标技术要求的条款/技术响应度说明"两个假落点（实测）。
_BAND_WORDS = ("优良", "中等", "一般", "良好", "不完全", "没有内容", "较先进", "较可行")


def _band_topics(chunk: str) -> list[str]:
    """档位式条款 → 标准落点（顺序固定，去重）。"""
    if sum(1 for w in _BAND_WORDS if w in chunk) < 2:
        return []
    out: list[str] = []
    for keys, title in _BAND_TOPIC_RULES:
        if any(k in chunk for k in keys) and title not in out:
            out.append(title)
    return out if len(out) >= 2 else []


def _split_label(chunk: str) -> tuple[str, str]:
    """从「方案可行性：（1）…」里取出（标题, 正文）；取不到返回 ("", chunk)。"""
    m = _LABEL_RE.match(chunk)
    if not m:
        return "", chunk
    label = m.group(1).strip()
    # 标题里不该有档位/分值描述；带分值的「优良（7-10分）」这类不当标题
    if _SCORE_RE.search(label) or len(label) < 2:
        return "", chunk
    if any(w in label for w in ("得", "分）：", "不得分")):
        return "", chunk
    return label, chunk[m.end():].strip()


def _body_for_subpoint(title: str, rule_bodies: list[tuple[str, str]]) -> str:
    """把外部细项标题映射回它所属的规则块正文（映射不到返回空串）。

    映射靠"标题字符落在哪个块里"：模型给的标题多半就是原文里的说法，
    所以字符命中率能可靠地把正文认回来。
    """
    t = _normalize(title)
    if not t:
        return ""
    best, best_hit = "", 0
    for label, body in rule_bodies:
        blob = _normalize(f"{label}{body}")
        hit = sum(1 for ch in t if ch in blob)
        if hit > best_hit:
            best, best_hit = body, hit
    # 命中率太低 → 认为映射不可靠，让调用方回落整段
    return best if best_hit >= max(2, len(t) * 0.6) else ""


def build_scoring_skeleton(review_points: Iterable, *, category: str = "tech",
                           package_no: str = "",
                           subpoints_by_criterion: Optional[dict] = None) -> list[OutlineNode]:
    """评分侧骨架：L1 评分因素 → L2 详细评审项 → L3 分项要素细分点。

    - 过滤：只取点名类别（tech/commerce/price）与标包号的评分点；
    - **L2 与 L1 同名时跳过 L2**（实测模型就是照抄这两层，产出
      `L1 技术方案 / L2 技术方案` 的重复节点）；
    - 每个细分点带上该评分点的分值，写作时才知道"这一段值多少分"。
    """
    nodes: list[OutlineNode] = []
    seen_l1: dict[str, OutlineNode] = {}

    for p in review_points or []:
        if category and getattr(p, "category", "") != category:
            continue
        if package_no and str(getattr(p, "package_no", "") or "") != str(package_no):
            continue

        factor = (getattr(p, "factor", "") or "").strip() or "技术响应"
        item = (getattr(p, "item", "") or "").strip()
        criterion = getattr(p, "criterion", "") or ""
        pts = getattr(p, "points", None)

        l1 = seen_l1.get(factor)
        if l1 is None:
            l1 = OutlineNode(level=1, title=factor, source="scoring", factor=factor)
            seen_l1[factor] = l1
            nodes.append(l1)

        parent_level = 1
        parent_title = factor
        if item and _normalize(item) != _normalize(factor):
            nodes.append(OutlineNode(level=2, title=item, source="scoring",
                                     factor=factor, item=item, points=pts,
                                     criterion=criterion))
            parent_level = 2
            parent_title = item

        # 细项来源：优先用外部传入的（模型抽取 + 接地校验 + 内容哈希缓存，
        # 见 agents.subpoints），否则用规则拆分。**结构仍由本函数决定**，
        # 外部只提供 L3 的标题文本。
        override = None
        if subpoints_by_criterion:
            override = subpoints_by_criterion.get(_normalize(criterion))
        if override:
            # 外部只给"标题"。正文要**逐个映射回规则块**，不能每个都塞整段：
            # 整段塞进去会让主题匹配分不清谁是谁 —— 实测「方案先进性」的
            # 信创/云原生/容灾/埋点被挂到了同级的「方案可行性」下（第一个命中的赢）。
            rule_bodies = split_criterion(criterion)
            subs = [(t, _body_for_subpoint(t, rule_bodies) or criterion) for t in override]
        else:
            subs = split_criterion(criterion)
        # 去掉与上层同名的细项（模型偶尔会把因素名当要点给回来）
        subs = [(t, b) for t, b in subs
                if _normalize(t) not in {_normalize(factor), _normalize(item)}]
        if not subs:
            # 分项要素为空（如纯形式条款）：给一个可写作的落点，不要留空壳
            subs = [(f"{item or factor}应答", "")]

        for label, body in subs:
            nodes.append(OutlineNode(
                level=parent_level + 1, title=label, source="scoring",
                factor=factor, item=item or parent_title,
                points=pts if parent_level == 1 else None,
                criterion=body or criterion,
            ))
    return nodes


def _normalize(s: str) -> str:
    return re.sub(r"[\s、，,。；;：:（）()【】\[\]]+", "", s or "")


def criterion_lookup_key(criterion: str) -> str:
    """分项要素的查表键。**调用方必须用它**（与内部 ``_normalize`` 同一口径），
    否则 ``subpoints_by_criterion`` 永远查不中，表现为"接了外部细项却没生效"。
    """
    return _normalize(criterion)


def spec_topic_sections(spec_sections: Iterable) -> list[dict]:
    """规范书里"能作为章节落点"的节：研究专题 / 工作项 / 各类要求。"""
    out: list[dict] = []
    for s in spec_sections or []:
        title = (s.get("title") or "").strip()
        if not title:
            continue
        out.append({
            "id": s.get("id") or "",
            "level": int(s.get("level") or 1),
            "title": title,
            "content": s.get("raw_content") or s.get("content") or "",
        })
    return out


def match_spec_nodes(target_text: str, spec_nodes: list[dict], *,
                     min_hits: int = 1) -> list[dict]:
    """按主题词把规范书节点匹配到某个评分点。

    匹配是**确定性**的：用规范书节点标题里的实词（去掉"工作项N""研究技术方案"这类
    噪音）去目标文本（评分点的因素/评审项/分项要素）里找。实测「方案先进性」的
    分项要素原文就写着"信创适配、云原生、容灾、埋点、低延迟" —— 与规范书的五个
    研究专题**逐词对得上**，这正是用户要的"结合点"。
    """
    text = _normalize(target_text)
    if not text:
        return []
    hits: list[dict] = []
    for node in spec_nodes:
        keys = _topic_keys(node["title"])
        if not keys:
            continue
        if sum(1 for k in keys if k in text) >= min_hits:
            hits.append(node)
    return hits


def _topic_keys(title: str) -> list[str]:
    """标题里的主题词：去掉工作项编号与通用词，只留能与评分点对齐的实词。

    实测必须剥干净：「信创适配研究及验证」要能对齐评分点原文里的「信创适配」，
    「埋点与低延迟系统技术研究及验证」要能对齐「埋点」「低延迟」——
    不剥掉 研究/及/验证/系统/技术 这些通用词，主题匹配一个都命中不了，
    规范书就全被丢进独立板块（用户要的"结合进章节目录"落空）。
    """
    t = title
    for noise in _SPEC_TITLE_NOISE:
        t = t.replace(noise, " ")
    for generic in ("研究及验证", "研究与验证", "研究", "验证", "系统技术", "系统",
                    "技术研究", "架构", "及", "与", "和", "的", "专题",
                    # 通用修饰词：不剥掉的话「高可靠性容灾研究及验证」的 key 会是
                    # 「高可靠性容灾」，而评分点原文只写「容灾」—— 匹配不上，
                    # 规范书就进不了评分章节（实测就是这个原因）。
                    "高可靠性", "高可靠", "全链路", "面向", "含数据安全", "数据安全"):
        t = t.replace(generic, " ")
    parts = re.split(r"[、，,（）()【】\s]+", t)
    keys = []
    for p in parts:
        p = p.strip()
        if len(p) >= 2 and p not in keys:
            keys.append(p)
    return keys


# 「放到哪里」类结构指令：用户会直接指派位置，例如
# 「技术规范书里面的五个专题内容需要融合在技术方案里面，在技术方案可行性里面需要体现技术方案内容」
# 这类要求必须被**执行**，否则用户改了提炼要求、目录却一模一样 —— 实测反馈的正是这个。
# ⚠️ 尾部**不能全可选**：写成 `([^，。；、]{2,16}?)\s*(?:里面|...)?\s*[，。；、]?` 时，
# 懒惰组只需两个字就能满足整个模式，实测把「融合在技术方案里面」解析成了「技术」。
# 必须要求"方位词"出现，才能把目标限定住。
_PLACEMENT_PATTERNS = (
    # 「在 X 里面/中/下 …动词…」——允许方位词与动词之间夹少量词（"里面需要体现"）
    re.compile(r"(?:在|到|于)\s*([^，。；、]{2,16}?)\s*(?:里面|里|中|内|下)\s*[^，。；、]{0,8}?"
               r"(?:体现|融合|包含|结合|写|展开|呈现|说明)"),
    # 「融合/并入/放到 X 里面」——方位词必需
    re.compile(r"(?:融合|并入|放到|放进|归入|纳入|安排)\s*(?:到|在|进)?\s*([^，。；、]{2,16}?)"
               r"\s*(?:里面|里|中|内|下)"),
)


def parse_placement_targets(instruction: str) -> list[str]:
    """从提炼要求里解析"把内容放到 X 下"的目标表述（按出现顺序、去重）。"""
    text = instruction or ""
    out: list[str] = []
    for pat in _PLACEMENT_PATTERNS:
        for m in pat.finditer(text):
            t = (m.group(1) or "").strip(" 　的")
            if 2 <= len(t) <= 16 and t not in out:
                out.append(t)
    return out


def _resolve_target(target: str, nodes: list[OutlineNode]) -> Optional[OutlineNode]:
    """把指令里的目标表述解析到具体的目录节点。

    匹配用双向包含（"技术方案可行性" ↔ 节点同名；"技术方案" ↔ 更浅的节点），
    并列时取**层级最深**的那个（指令越具体，越该命中具体章节）。
    """
    key = _normalize(target)
    if not key:
        return None
    best: Optional[OutlineNode] = None
    best_score: tuple[int, int, int] = (-1, -1, -1)
    for n in nodes:
        nk = _normalize(n.title)
        if not nk:
            continue
        if not (nk == key or nk in key or key in nk):
            continue
        # 排序依据（**不是**简单的"层级最深"）：
        #   1) 精确匹配优先；
        #   2) 其次比标题长度 —— 更具体的赢；
        #   3) 层级只作最后 tiebreak。
        # 为什么：指令写「技术方案可行性」时，短标题「方案可行性」（它同时是
        # 实施方案下的一个 L3）是目标串的子串且层级更深，按"最深"会选中它 ——
        # 实测专题就被挂到了「实施方案 → 方案可行性」下。
        score = (1 if nk == key else 0, len(nk), n.level)
        if score > best_score:
            best, best_score = n, score
    return best


def build_outline(review_points: Iterable, *, spec_sections: Iterable = (),
                  category: str = "tech", package_no: str = "",
                  max_spec_level: int = 99,
                  subpoints_by_criterion: Optional[dict] = None,
                  placement_targets: Iterable[str] = ()) -> list[OutlineNode]:
    """**主入口**：评分侧骨架 + 规范书结合点，输出一份稳定可复现的目录。

    ``placement_targets``：从提炼要求里解析出的"把规范书内容放到 X 下"的目标表述
    （见 ``parse_placement_targets``）。用户明确指派的位置**优先于**关键词猜测。

    规范书的归属（全部确定性规则）：
    1. 先按主题词匹配到某个评分节点（从最深的 L3 往回找，就近挂载）；
    2. **匹配到的专题连它的整棵子树一起搬进去** —— 专题下的「工作项N」是研究/
       验证任务，必须逐条能应答；此前只把标题记进 ``spec_refs``、子树留在原地，
       结果既看不见结合点、又留下悬空的重复工作项（实测）。
    3. 没被匹配的整棵子树，收进 L1「技术规范书要求应答」，保证规范书内容全有落点。
    """
    nodes = build_scoring_skeleton(review_points, category=category, package_no=package_no,
                                   subpoints_by_criterion=subpoints_by_criterion)
    # max_spec_level 默认不限层深：规范书的工作项常在第 6~7 层，而它们恰恰是
    # "研究与验证任务"，必须逐条能应答。按固定层数截断会悄悄丢掉它们
    # （实测测试里第 7 层的工作项就被 6 这个魔法数滤掉了）。
    spec_nodes = [s for s in spec_topic_sections(spec_sections) if s["level"] <= max_spec_level]
    forest = _dedupe_forest(_build_forest(spec_nodes))   # 顶层节点（去重后的树）

    used: set[str] = set()

    def _mark_used(node: dict) -> None:
        used.add(node["id"])
        for c in node.get("children", []):
            _mark_used(c)

    def _attach(node: dict, parent: OutlineNode, level: int) -> list[OutlineNode]:
        """把一个规范书节点（及其子树）转成目录节点，**返回按树序排好的节点序列**。

        为什么要返回序列而不是直接 append 到 nodes：目录是"扁平列表 + level 缩进"，
        追加到末尾会让子节点在视觉上挂到**别的**父节点下面（实测：方案先进性的
        信创/云原生/容灾被渲染成挂在「服务团队」下）。必须插在父节点之后。
        """
        out = [OutlineNode(level=min(6, level), title=node["title"], source="spec")]
        parent.spec_refs.append(node["title"])
        for child in node.get("children", []):
            out.extend(_attach(child, parent, level + 1))
        return out

    scoring_nodes = [n for n in nodes if n.source == "scoring"]

    # ① 先执行用户的**结构指令**：指令点名了"放到 X 里"时，规范书的具体主题一律挂到 X 下。
    #    为什么放在主题匹配之前：用户的明确指派优先于系统的关键词猜测 ——
    #    否则"我写了要求却没有任何变化"（实测反馈）。
    placed_by_instruction = False
    resolved: list[OutlineNode] = []
    for target in placement_targets or ():
        node = _resolve_target(target, scoring_nodes)
        if node is not None and node not in resolved:
            resolved.append(node)
    if resolved:
        host = max(resolved, key=lambda n: n.level)
        for topic in _walk(forest):
            if topic["id"] in used or not _is_research_topic(topic):
                continue
            _mark_used(topic)
            idx = nodes.index(host) + 1 + _subtree_len(nodes, host)
            for offset, child in enumerate(_attach(topic, host, host.level + 1)):
                nodes.insert(idx + offset, child)
        placed_by_instruction = True

    # ② 没有指令时（或指令没解析出目标）：按主题词就近挂载
    for topic in _walk(forest):
        if placed_by_instruction or topic["id"] in used or not _is_specific_topic(topic):
            continue
        keys = _topic_keys(topic["title"])
        if not keys:
            continue
        best, best_score = None, 0
        for node in scoring_nodes:
            target = _normalize(" ".join([node.title, node.factor, node.item, node.criterion]))
            score = sum(1 for k in keys if k in target)
            if score > best_score or (score == best_score and score and best is not None
                                      and node.level > best.level):
                best, best_score = node, score
        if best is not None and best_score:
            _mark_used(topic)
            # 插到该评分节点之后（含它已有的子节点之后），保持树序
            idx = nodes.index(best) + 1 + _subtree_len(nodes, best)
            for offset, child in enumerate(_attach(topic, best, best.level + 1)):
                nodes.insert(idx + offset, child)

    def _prune(node: dict) -> Optional[dict]:
        """剔除已并入的节点；父节点只剩空壳时一并丢弃。

        旧写法只看 forest 顶层：被并入的都是**深层**节点（专题在工作项之上），
        它的顶层祖先没被标 used，于是整棵子树又出现在残留板块里 ——
        目录看起来就是"同一批专题出现两遍"（实测）。
        """
        if node["id"] in used:
            return None
        kids = [k for k in (_prune(c) for c in node.get("children", [])) if k]
        pruned = dict(node)
        pruned["children"] = kids
        return pruned

    leftovers = [p for p in (_prune(t) for t in forest) if p]
    if leftovers:
        nodes.append(OutlineNode(level=1, title="技术规范书要求应答", source="spec"))

        def _emit(node: dict, level: int) -> None:
            nodes.append(OutlineNode(level=min(6, level), title=node["title"], source="spec",
                                     spec_refs=[node["title"]]))
            for c in node.get("children", []):
                _emit(c, level + 1)

        for t in leftovers:
            _emit(t, 2)

    return _repair_levels(nodes)


# 容器节点：标题只起分组作用，不是可应答的主题。把它们当主题去匹配，
# 会把整棵子树重复搬进多个评分章节（实测：交付成果/服务团队各吞掉半个规范书）。
_SPEC_CONTAINER_TITLES = (
    "项目内容与要求", "项目内容", "高可靠技术专题研究与验证", "总则", "项目描述",
    "其他要求", "信息安全要求", "项目验收", "项目交付项", "招标范围", "概述", "定义",
)


def _is_specific_topic(node: dict) -> bool:
    """这个规范书节点是不是"能挂到评分点下"的具体主题。"""
    title = (node.get("title") or "").strip()
    if title in _SPEC_CONTAINER_TITLES:
        return False
    if len(title) < 3:
        return False
    if any(w in title for w in ("研究", "验证", "工具")):
        return True
    if title.endswith("要求"):                  # 人员要求 / 项目管理要求 / 保密要求…
        return True
    if title.startswith("工作项"):               # 工作项随专题一起搬，不单独匹配
        return False
    return False


def _subtree_len(nodes: list[OutlineNode], parent: OutlineNode) -> int:
    """parent 之后紧跟着的、level 比它深的节点个数（即已挂上的子树长度）。"""
    i = nodes.index(parent) + 1
    n = 0
    while i < len(nodes) and nodes[i].level > parent.level:
        n += 1
        i += 1
    return n


def _dedupe_forest(forest: list[dict]) -> list[dict]:
    """按标题去重（保留首次出现），并在去重时丢弃"其祖先已被丢弃"的子树。

    规范书里同一批研究专题会出现两遍（目录一次、正文一次）；不去重的话，
    匹配走一份、另一份留在"技术规范书要求应答"里，目录看起来就是重复的。
    """
    seen: set[str] = set()

    def _walk_dedupe(nodes: list[dict], dropped_ancestor: bool) -> list[dict]:
        out = []
        for node in nodes:
            title = (node.get("title") or "").strip()
            dup = dropped_ancestor or title in seen
            if not dup:
                seen.add(title)
            kids = _walk_dedupe(node.get("children", []), dup)
            if dup:
                continue
            node = dict(node)
            node["children"] = kids
            out.append(node)
        return out

    return _walk_dedupe(forest, False)


def _is_research_topic(node: dict) -> bool:
    """是不是"研究 / 验证 / 工具"类专题。

    结构指令("把五个专题放进技术方案")只该搬这些；`人员要求 / 保密要求` 这类
    通用要求不属于"专题"，实测会被一起拖进目标章节里（污染目录）。
    """
    title = (node.get("title") or "")
    return any(w in title for w in ("研究", "验证", "工具")) and not title.startswith("工作项")


def _walk(forest: list[dict]) -> Iterable[dict]:
    """深度优先遍历（先父后子）。"""
    for node in forest:
        yield node
        yield from _walk(node.get("children", []))


def _build_forest(spec_nodes: list[dict]) -> list[dict]:
    """按 level 把扁平的规范书章节还原成树（父子关系靠层级）。"""
    forest: list[dict] = []
    stack: list[dict] = []
    for sp in spec_nodes:
        node = dict(sp)
        node["children"] = []
        while stack and stack[-1]["level"] >= node["level"]:
            stack.pop()
        if stack:
            stack[-1]["children"].append(node)
        else:
            forest.append(node)
        stack.append(node)
    return forest


def render_outline_text(nodes: Iterable[OutlineNode]) -> str:
    """把目录渲染成缩进文本（给用户看 / 给模型做润色输入）。"""
    out = []
    for n in nodes:
        indent = "  " * max(0, n.level - 1)
        pts = f"（{n.points:g}分）" if n.points else ""
        src = {"scoring": "", "spec": " [规范书]", "merged": " [合并]"}.get(n.source, "")
        refs = f"  ← 并入规范书：{'、'.join(n.spec_refs)}" if n.source == "scoring" and n.spec_refs else ""
        out.append(f"{indent}- {n.title}{pts}{src}{refs}")
    return "\n".join(out)


def _repair_levels(nodes: list[OutlineNode]) -> list[OutlineNode]:
    """把层级修成"每个节点 = 它的父节点 + 1"。

    为什么要修：去重 / 剔除会让中间层消失（容器被去掉、专题被并走），
    留下 `L2 → L4` 这样的跨度。下游 ``normalize_nodes`` 要求层级自洽，
    不自洽就整批"重挂" —— 实测 78 节里有 **70 个被重挂**，最终结构已经不是
    本模块定的那个了（"稳定"也就无从谈起）。
    """
    stack: list[int] = []
    for n in nodes:
        while stack and stack[-1] >= n.level:
            stack.pop()
        n.level = (stack[-1] + 1) if stack else 1
        stack.append(n.level)
    return nodes

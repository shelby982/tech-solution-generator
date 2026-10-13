"""评审要素表结构化抽取：把「评分标准」从文本里抽成可校验的结构。

为什么需要（问题记录）：
    第一步解析的产物此前是"节 → 原文"，第三步派生目录时靠 ``build_spec_digest``
    按字符预算把它压成 8000 字文本。压缩是无差别的 —— 实测同一份招标文件：
    - 提炼要求里写了「标包 2」→ 区段定位命中，评分表活下来（digest 7661 字）；
    - 提炼要求里没写范围 → 退回全量，digest 末尾出现
      「……以下 380 个章节因长度限制未列出」，**标包2 / 技术条款差异性 / 对项目的理解 /
      服务团队 / 分值 全部不在 prompt 里**。
    于是"目录拆得对不对"取决于提示词措辞，而不是取决于招标文件的评分点。

    本模块把评分标准**确定性地**抽成结构化行（评分因素 / 详细评审项 / 详细评审分项要素 /
    分值 / 档位 / 适用标包 / 客观主观），使下游可以直接从评分点投影出章节，
    并有可断言的判据（如"标包2 技术评分 8 项、分值和 100"）。

设计取舍：
    输入是**已解析的 sections**（表格已是 Markdown 行），不重跑 PDF。这样纯函数、可单测，
    且与解析层解耦；将来解析层若产出更细的表格结构，本模块只换输入适配。

    表格在解析产物里因跨页被切成多个 Markdown 表碎片，且**碎片之间列数会变**
    （实测 7 列 → 5 列），同一「分值」列的位置从下标 6 漂到 4。所以这里不按列下标取数，
    而是**按表头列名语义映射**每次重算，跨页碎片因此可以安全拼接。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 表头列名 → 规范化字段
# ─────────────────────────────────────────────

# 表格里有两种列布局（实测）：
#   7 列：条款号 | 分类 | 序号 | 评分因素 | 详细评审项 | 详细评审分项要素 | 分值
#   5 列：序号 | 评分因素 | 详细评审项 | 详细评审分项要素 | 分值
# 因此按列名映射，不按位置。
_COLUMN_ALIASES: dict[str, str] = {
    "条款号": "clause",
    "序号": "seq",
    "评分因素": "factor",
    "评审因素": "factor",
    "详细评审项": "item",
    "详细评审分项要素": "criterion",
    "评分标准": "criterion",
    "详细评审标准": "criterion",
    "分值": "points",
    "分值/权重": "points",
}
# 未在表头出现的列（如 7 列布局里的"商务评分标准 / 技术评分标准"分类列）按位置兜底
_CATEGORY_HINTS = ("商务评分标准", "技术评分标准", "投标报价评分标准", "其他因素评分标准")

# 评分类别
CATEGORY_TECH = "tech"
CATEGORY_COMMERCE = "commerce"
CATEGORY_PRICE = "price"
CATEGORY_OTHER = "other"
_CATEGORY_BY_HINT = {
    "技术评分标准": CATEGORY_TECH,
    "商务评分标准": CATEGORY_COMMERCE,
    "投标报价评分标准": CATEGORY_PRICE,
    "其他因素评分标准": CATEGORY_OTHER,
}

# 「下列评审标准适用的标的/标包：标包2：高可靠技术专题研究与验证标包」
_SCOPE_LINE_RE = re.compile(
    r"下列评审标准适用的[^：:]*[：:]\s*标包\s*([0-9一二三四五六七八九十]+)\s*[：:]\s*([^|｜\n]*)"
)
# 「1. 项目负责人具有 PMP 证书」这类客观项
_OBJECTIVE_HINT_RE = re.compile(
    r"证书|资质|认证|业绩|社保|简历|工作年限|注册|执业|盖章|扫描件|ISO|CMMI|ITSS|PMP"
)
# 客观分值占比高的行必然是可核查材料项；用于压低误判（纯承诺类不给客观）
_OBJECTIVE_MIN_HINT = 2


def _norm(text: str) -> str:
    """去换行占位与空白，便于在单元格文本里做子串匹配。"""
    return re.sub(r"\s+", "", (text or "").replace("<br>", ""))


def _cells(line: str) -> list[str]:
    """Markdown 表行 → 单元格列表。"""
    line = (line or "").strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(set(c) <= {"-", ":", " "} for c in cells if c != "")


def _to_number(text: str) -> Optional[float]:
    m = re.search(r"\d+(?:\.\d+)?", (text or "").replace("<br>", ""))
    return float(m.group(0)) if m else None


# ─────────────────────────────────────────────
# 档位
# ─────────────────────────────────────────────

# 「得8-12分」「（7-10分）」「得 5 分」「12-18分」
_BAND_RE = re.compile(r"(?:得|对应)?\s*(\d+(?:\.\d+)?)\s*[-~—至]\s*(\d+(?:\.\d+)?)\s*分")
_SINGLE_BAND_RE = re.compile(r"得\s*(\d+(?:\.\d+)?)\s*分")
_BAND_LABEL_RE = re.compile(r"(优良|优|中等|良|一般|合格|差)\s*[（(]?")


@dataclass
class ScoreBand:
    """档位区间，如「优良（7-10分）」。"""
    label: str = ""
    lo: float = 0.0
    hi: float = 0.0
    text: str = ""

    def to_dict(self) -> dict:
        return {"label": self.label, "lo": self.lo, "hi": self.hi, "text": self.text}


def extract_bands(text: str) -> list[ScoreBand]:
    """从分项要素文本里抽档位。区间优先；抽不到区间再抽单值「得5分」。"""
    t = (text or "").replace("<br>", "")
    bands: list[ScoreBand] = []
    for m in _BAND_RE.finditer(t):
        lo, hi = float(m.group(1)), float(m.group(2))
        label = ""
        head = t[max(0, m.start() - 12):m.start()]
        lm = _BAND_LABEL_RE.search(head)
        if lm:
            label = lm.group(1)
        bands.append(ScoreBand(label=label, lo=lo, hi=hi, text=m.group(0).strip()))
    if bands:
        return bands
    for m in _SINGLE_BAND_RE.finditer(t):
        v = float(m.group(1))
        head = t[max(0, m.start() - 12):m.start()]
        lm = _BAND_LABEL_RE.search(head)
        bands.append(ScoreBand(label=lm.group(1) if lm else "", lo=v, hi=v, text=m.group(0).strip()))
    return bands


def classify_objective(factor: str, item: str, criterion: str) -> bool:
    """这一项是"客观可核查材料项"还是"主观评审项"。

    为什么要分清：客观项（证书 / 社保 / 业绩 / 资质）的得分动作是**准备材料**，
    主观项（技术方案 / 对项目的理解）的得分动作是**写内容**。二者混在同一张
    要求清单里，会让写手去"写一段话回应人员社保"，而真正该做的是交扫描件。
    """
    haystack = f"{factor}{item}{criterion}"
    hits = len(_OBJECTIVE_HINT_RE.findall(haystack))
    return hits >= _OBJECTIVE_MIN_HINT


# ─────────────────────────────────────────────
# 评审要素行
# ─────────────────────────────────────────────

@dataclass
class ReviewPoint:
    """评分标准里的一行（一个"评分点"）。"""
    package_label: str = ""          # 适用标包的原始描述，如「标包2：高可靠技术专题研究与验证标包」
    package_no: str = ""             # 标包号，如 "2"
    clause: str = ""                 # 条款号，如 "2.2.4（2）"
    category: str = ""               # tech / commerce / price / other
    seq: str = ""                    # 序号
    factor: str = ""                 # 评分因素
    item: str = ""                   # 详细评审项
    criterion: str = ""              # 详细评审分项要素
    points: Optional[float] = None   # 分值
    bands: list[ScoreBand] = field(default_factory=list)
    objective: bool = False          # 客观可核查材料项
    source_section_id: str = ""
    source_file: str = ""
    # 内容派生的稳定 id（`services/point_store.stable_review_point_id` 回写）。
    # 章节靠它反查"这章为哪个评分点得分"；不用顺序编号是因为增删一条会让后面全移位。
    point_id: str = ""

    @property
    def key(self) -> str:
        """稳定标识：标包 + 类别 + 序号（用于对账与去重）。"""
        return f"{self.package_no or '-'}|{self.category or '-'}|{self.seq or '-'}"

    def to_dict(self) -> dict:
        return {
            "package_label": self.package_label,
            "package_no": self.package_no,
            "clause": self.clause,
            "category": self.category,
            "seq": self.seq,
            "factor": self.factor,
            "item": self.item,
            "criterion": self.criterion,
            "points": self.points,
            "bands": [b.to_dict() for b in self.bands],
            "objective": self.objective,
            "source_section_id": self.source_section_id,
            "source_file": self.source_file,
        }


@dataclass
class ReviewPointTable:
    """一份要求文件里抽出的全部评审要素。"""
    points: list[ReviewPoint] = field(default_factory=list)
    packages: dict[str, str] = field(default_factory=dict)   # 标包号 → 标包名称

    def of_category(self, category: str = CATEGORY_TECH,
                    package_no: str = "") -> list[ReviewPoint]:
        return [
            p for p in self.points
            if (not category or p.category == category)
            and (not package_no or p.package_no == package_no)
        ]

    def total_points(self, category: str = "", package_no: str = "") -> float:
        return sum(
            (p.points or 0.0)
            for p in self.of_category(category, package_no)
        )

    def to_dict(self) -> dict:
        return {
            "packages": dict(self.packages),
            "points": [p.to_dict() for p in self.points],
        }


# ─────────────────────────────────────────────
# 抽取
# ─────────────────────────────────────────────

# 每个角色在"宽行（带分类列）"下的列序，用于从表头推出窄行布局。
# ─────────────────────────────────────────────
# 列布局识别（实测驱动）
# ─────────────────────────────────────────────

_SEQ_CELL_RE = re.compile(r"^\s*(\d{1,2})\s*$")
_SEQ_LEAD_RE = re.compile(r"^\s*(\d{1,2})\s+(?=\S)")


def _looks_like_seq(text: str) -> bool:
    """序号单元格：纯数字，或「1 商务投标文件质量」这种把下一列并进来的形态。"""
    t = (text or "").strip()
    return bool(_SEQ_CELL_RE.match(t) or _SEQ_LEAD_RE.match(t))


def _probe_offset(rows: list[list[str]], offset: int) -> int:
    """按给定偏移把规范列切出来并打分：分越高说明该偏移越贴合真实列结构。

    判据全部来自实测的结构事实，不做"猜表头"：
    - ``seq`` 槽位必须是序号形态（否则该偏移整体错位，重罚）；
    - 纯数字落在 ``factor`` 槽位 → 也说明错位（评分因素不会是纯数字）；
    - ``criterion`` 槽位通常最长；
    - ``points`` 是纯数字分值或空。
    """
    score = 0
    for cells in rows:
        def slot(i: int) -> str:
            ci = i + offset
            return cells[ci].strip() if ci < len(cells) else ""

        seq, factor, item, criterion, points = (slot(i) for i in range(5))
        if _looks_like_seq(seq):
            score += 3
        elif seq:
            score -= 4
        else:
            score -= 2
        if factor:
            score += 2 if not _SEQ_CELL_RE.match(factor) else -4
        if item:
            score += 1
        if criterion:
            score += min(len(criterion), 80) // 20
        if points:
            score += 2 if _to_number(points) is not None else -2
    return score


# 实测指纹（逐行追踪 114 条评审要素数据行归纳）：
#   7 个单元格 → ``[条款号, 分类, 序号, 评分因素, 详细评审项, 要素, 分值]``，序号在切片下标 2
#   5 个单元格 → ``[序号, 评分因素, 详细评审项, 要素, 分值]``，序号在下标 0（丢了行首空列）
# 偏移因此由**行宽**决定。整节统一取一个偏移是错的 —— 实测同一节里两种行并存，
# 取错会让整批行的"序号"被读成"评分因素"，前面几项直接丢掉（实测丢了 1–3 项）。
_OFFSET_BY_WIDTH = {7: 2, 5: 0}


def _detect_offset(rows: list[list[str]]) -> int:
    """节级兜底偏移：按行宽多数票决定；异常宽度再打分择优。"""
    if not rows:
        return 2          # 无行可判时按宽行处理（该 PDF 的主表形态）
    votes = [_OFFSET_BY_WIDTH[w] for w in (len(r) for r in rows) if w in _OFFSET_BY_WIDTH]
    if votes:
        return max(set(votes), key=votes.count)
    return max((0, 1), key=lambda off: _probe_offset(rows, off))


def _offset_for_row(cells: list[str], fallback: int) -> int:
    """单行的偏移：行宽命中实测指纹就定向，否则用节级兜底。"""
    return _OFFSET_BY_WIDTH.get(len(cells), fallback)


def _split_row(cells: list[str], offset: int) -> dict[str, str]:
    """按偏移切出一条评审要素行的各字段。

    序号列会把下一列并进来（pdfplumber 合并了相邻单元格）→ 拆开，
    例如 ``"1 商务投标文件质量"`` = 序号 1 + 评分因素「商务投标文件质量」。
    """
    def slot(i: int) -> str:
        ci = i + offset
        return cells[ci].strip() if ci < len(cells) else ""

    seq_raw = slot(0)
    factor, seq = slot(1), ""
    m = _SEQ_LEAD_RE.match(seq_raw)
    if m:
        seq = m.group(1)
        factor = seq_raw[m.end():].strip() or factor
    elif _SEQ_CELL_RE.match(seq_raw):
        seq = seq_raw.strip()

    return {
        "seq": seq,
        "factor": factor,
        "item": slot(2),
        "criterion": slot(3),
        "points": slot(4),
    }


def _iter_table_rows(content: str):
    """逐行产出 (原始单元格, 是否表头行)。表头行由列名判定，不用宽度。"""
    for raw in (content or "").splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = _cells(line)
        if not cells or _is_separator(cells):
            continue
        joined = _norm("".join(cells))
        is_header = all(
            any(alias == _norm(c) for alias in _COLUMN_ALIASES) for c in cells if c.strip()
        ) and ("评分因素" in joined or "评审因素" in joined)
        yield cells, is_header


# 子串识别：不同招标文件的表头写法差别很大。实测南网中台那份写的是
# 「（一）、商务评审（100）分」「（二）、技术评审（100）分」——
# 精确匹配 _CATEGORY_BY_HINT 一个都命中不了，13 条评分点**类别全空**，
# 于是按"技术类"过滤时一条都取不到，确定性骨架直接退回模型路径（用户实测反馈）。
_CATEGORY_SUBSTR = (
    ("技术评审", CATEGORY_TECH), ("技术评分", CATEGORY_TECH), ("技术部分", CATEGORY_TECH),
    ("商务评审", CATEGORY_COMMERCE), ("商务评分", CATEGORY_COMMERCE), ("商务部分", CATEGORY_COMMERCE),
    ("报价评分", CATEGORY_PRICE), ("价格分", CATEGORY_PRICE), ("投标报价", CATEGORY_PRICE),
    ("其他因素", CATEGORY_OTHER), ("其他评分", CATEGORY_OTHER),
)


def _category_from_cells(cells: list[str]) -> str:
    """从一行里读评分类别（合并单元格 / 表内小标题，只在部分行出现）。"""
    for c in cells:
        n = _norm(c)
        if n in _CATEGORY_BY_HINT:
            return _CATEGORY_BY_HINT[n]
        for word, cat in _CATEGORY_SUBSTR:
            if word in n:
                return cat
    return ""


_SCOPE_IN_LINE_RE = re.compile(
    r"下列评审标准适用的[^：:]*[：:]\s*标包\s*([0-9]+)\s*[：:]\s*([^|｜\s]*)"
)


# 「适用标包：<名称>」——只有名称没有编号。实测南网中台那份就是这种写法，
# 旧实现只认「标包N：」，于是 13 条评分点的 package_no 全空。
_SCOPE_NAME_RE = re.compile(r"适用标包\s*[：:]\s*([^|｜\n]{2,80})")


def _split_off_scope(cells: list[str]) -> Optional[tuple[str, str]]:
    """若这一行是作用域声明 → (标包号, 标包名)。

    两种写法都要认：
    1. 「下列评审标准适用的标的/标包：标包2：高可靠…」→ 有编号；
    2. 「适用标包：2026年南网…采购项目[应用中台客户档案管理认证系统开发实施服务]」→ 只有名称。
       只有名称时编号留空，靠**名称匹配**（见 agents.zhang_heng 的范围判定）挂靠。
    """
    text = _norm("".join(cells))
    m = _SCOPE_IN_LINE_RE.search(text)
    if m:
        return m.group(1), m.group(2)
    m2 = _SCOPE_NAME_RE.search("".join(cells))
    if m2:
        return "", m2.group(1).strip()
    return None


def extract_review_points(
    sections: list,
    *,
    source_file: str = "",
) -> ReviewPointTable:
    """从已解析的 sections 里抽出结构化评审要素表。

    三件事，都是此前缺的：
    1. **标包作用域**：「下列评审标准适用的标的/标包：标包N：…」开启一个作用域，
       直到下一个同类锚点。此前它只是一行普通表格文本，标包维度在解析层不存在 ——
       "针对标包 N"因此只能靠字符串碰运气。
    2. **列布局探测**：同一张表跨页后列的切法会变（带/不带分类列、pdfplumber 丢行首空列），
       对候选偏移打分择优，避免按固定下标读到错列（实测会把序号读成评分因素）。
    3. **续行合并**：跨页断行的正文并回上一条，续行只续正文，不污染评分因素/评审项。

    ``sections`` 接受 dict（``{"id","title","raw_content","source_file"}``）或带同名
    属性的对象，以便同时服务 parser 的 Section 与 domain 的 Section。
    """
    table = ReviewPointTable()

    def _get(sec, name: str, default=""):
        return sec.get(name, default) if isinstance(sec, dict) else getattr(sec, name, default)

    cur_pkg_label = ""
    cur_pkg_no = ""
    cur_clause = ""
    cur_category = ""
    pending: Optional[dict] = None
    seen: set[str] = set()

    def _flush() -> None:
        nonlocal pending
        if not pending:
            return
        if (pending.get("seq") or "").strip():
            criterion = pending.get("criterion", "")
            factor = pending.get("factor", "")
            item = pending.get("item", "")
            rp = ReviewPoint(
                package_label=cur_pkg_label,
                package_no=cur_pkg_no,
                clause=pending.get("clause", "") or cur_clause,
                category=pending.get("category", ""),
                seq=_norm(pending.get("seq", "")),
                factor=_norm(factor),
                item=_norm(item),
                criterion=criterion.replace("<br>", "").strip(),
                points=_to_number(pending.get("points", "")),
                bands=extract_bands(criterion),
                objective=classify_objective(factor, item, criterion),
                source_section_id=pending.get("_sid", ""),
                source_file=pending.get("_sfile", "") or source_file,
            )
            if rp.key not in seen:
                seen.add(rp.key)
                table.points.append(rp)
        pending = None

    for sec in sections or []:
        content = _get(sec, "raw_content", "") or ""
        if "评分因素" not in content and "评审因素" not in content:
            continue
        sid = _get(sec, "id", "")
        sfile = _get(sec, "source_file", "") or source_file

        # 先收齐本节的评审要素行（跳过表头，**保留标包作用域行** —— 后面要按它切标包），
        # 再一次性探测列偏移：单行探测不稳，整节一起打分才可靠。
        data_rows: list[list[str]] = []
        for cells, is_header in _iter_table_rows(content):
            if is_header:
                continue
            data_rows.append(cells)
        offset = _detect_offset(data_rows)

        for cells in data_rows:
            scope = _split_off_scope(cells)
            if scope:
                _flush()
                cur_pkg_no, pkg_name = scope
                cur_pkg_label = f"标包{cur_pkg_no}：{pkg_name}"
                if cur_pkg_no:
                    table.packages[cur_pkg_no] = pkg_name
                continue

            cur_category = _category_from_cells(cells) or cur_category
            row = _split_row(cells, _offset_for_row(cells, offset))
            row["category"] = cur_category
            row["_sid"] = sid
            row["_sfile"] = sfile
            if row.get("clause"):
                cur_clause = row["clause"]
            row.setdefault("clause", cur_clause)

            # 条款号列：形如 2.2.4（1）
            for c in cells:
                if re.match(r"^\d+\.\d+", _norm(c)):
                    row["clause"] = _norm(c)
                    break

            if row.get("seq"):
                _flush()
                pending = row
            elif pending is not None:
                # 续行（跨页断行）：只续正文列
                if row.get("criterion"):
                    pending["criterion"] = pending.get("criterion", "") + row["criterion"]
                if row.get("points") and not pending.get("points"):
                    pending["points"] = row["points"]

        _flush()

    return table


def review_points_of(table: ReviewPointTable, *,
                     category: str = "", package_no: str = "") -> list[ReviewPoint]:
    """便捷过滤（薄封装，便于下游与测试共用同一口径）。"""
    return table.of_category(category, package_no)


__all__ = [
    "CATEGORY_COMMERCE",
    "CATEGORY_OTHER",
    "CATEGORY_PRICE",
    "CATEGORY_TECH",
    "ReviewPoint",
    "ReviewPointTable",
    "ScoreBand",
    "classify_objective",
    "extract_bands",
    "extract_review_points",
    "review_points_of",
]

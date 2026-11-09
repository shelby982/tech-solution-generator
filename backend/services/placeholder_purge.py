"""正文净化：**正文里永不留"待补充/待补事实/待确认"这类字眼**。

产品原则（用户拍板）：
- 内容可以浅，但必须**完整可交**；
- 缺材料 → 要么在待办里让用户上传，要么**把这句删掉**；
- 不允许让用户到最后在文档里批量搜"待补充"再改。

做法：
  ① 句中占位（"……以【待补充：xx】为准"）→ 删占位、修补标点，保留句子骨架
  ② 整句只有占位、或占位后无实质信息 → 删整句
  ③ 删掉的内容**记录下来** → 进待办（系统补依据 / 需要你上传），信息不丢
"""
from __future__ import annotations

import re

_PLACEHOLDER = re.compile(
    r"【\s*(待补充|待填写|待补全|待补齐|待补事实|待提供|待确认|待核实|待核对|待明确|待定|缺失)\s*[:：]?\s*([^】]{0,120})】")
# 【来源：xx（具体条款号待补充）】这类：把括号里的"待补充"整段去掉，保留来源名
_SOURCE_INLINE = re.compile(r"（[^）]{0,40}待(?:补充|明确|确认)[^）]{0,40}）")
_SENTENCE_SPLIT = re.compile(r"(?<=[。；！？])")
# 裸词（不带宽方括号）：正文里同样不能出现"待补充/待明确"这类字眼 → 整句删掉
_BARE_PHRASES = ("尚待补充", "待补充", "待补全", "待明确", "待确认", "有待确定", "尚未明确")
# 表格/单元格里不能删整行，改成事实性表述
_BARE_REPLACEMENTS = (
    ("尚待补充", "尚不完善"), ("待补充", "尚不完善"), ("待补全", "尚不完善"),
    ("待明确", "尚不规范"), ("有待确定", "尚待规范"), ("尚未明确", "尚不规范"),
    ("待确认", "尚不规范"), ("待建立", "尚未建立"),
)
_HEADING = re.compile(r"(?m)^#{2,6}\s+(.+?)\s*$")

# 这些占位内容说明"需要用户提供材料"
_NEED_UPLOAD = ("业绩", "客户", "合同", "验收", "资质", "证书", "内部", "台账", "清单",
                "业务量", "岗位", "运行统计", "测评", "备案", "组织", "人员", "预算", "报价")
# 这些说明"系统可以去查"
_CAN_SEARCH = ("条款", "原文", "文号", "发文机关", "政策", "标准", "规范", "依据", "指标")


def _section_of(text: str, offset: int) -> str:
    section = ""
    for match in _HEADING.finditer(text):
        if match.start() > offset:
            break
        section = match.group(1).strip()
    return section


def _kind(content: str) -> str:
    if any(word in content for word in _NEED_UPLOAD):
        return "upload"
    if any(word in content for word in _CAN_SEARCH):
        return "search"
    return "drop"


def purge(content: str) -> tuple[str, list[dict]]:
    """清理正文中的占位符。返回 (新正文, 被清理项清单)。"""
    text = str(content or "")
    removed: list[dict] = []
    lines = text.splitlines()
    out: list[str] = []

    for line in lines:
        stripped_line = line.strip()
        # 表格行：一律做"占位清理 + 裸词改事实性表述"（单元格不能整行删）
        if stripped_line.startswith("|"):
            new_line = _PLACEHOLDER.sub("", line)
            for match in _PLACEHOLDER.finditer(line):
                removed.append({"section": _section_of(text, max(0, text.find(line))),
                                "placeholder": match.group(0), "reason": match.group(2),
                                "kind": _kind(match.group(2) or line), "quote": stripped_line[:80]})
            for bare, replacement in _BARE_REPLACEMENTS:
                new_line = new_line.replace(bare, replacement)
            out.append(new_line)
            continue
        if not _PLACEHOLDER.search(line):
            # 来源标注里的"（具体条款号待补充）"这类：去掉括号内容，保留来源名
            if _SOURCE_INLINE.search(line):
                cleaned_line = _SOURCE_INLINE.sub("", line)
                if not any(word in cleaned_line for word in _BARE_PHRASES):
                    out.append(cleaned_line)
                    continue
            if any(word in line for word in _BARE_PHRASES):
                # 裸词句（"列为待确认事项"）→ 整句删
                kept = []
                for sentence in _SENTENCE_SPLIT.split(line):
                    if any(word in sentence for word in _BARE_PHRASES):
                        continue
                    kept.append(sentence)
                rebuilt_line = "".join(kept).strip()
                if rebuilt_line:
                    out.append(rebuilt_line)
                continue
            out.append(line)
            continue
        stripped = line.strip()
        is_heading = stripped.startswith("#")
        is_table = stripped.startswith("|")
        # 表格单元格：去掉占位，保留空单元格
        if is_table:
            new_line = _PLACEHOLDER.sub("", line)
            for bare, replacement in _BARE_REPLACEMENTS:
                new_line = new_line.replace(bare, replacement)
            offset = text.find(line)
            for match in _PLACEHOLDER.finditer(line):
                removed.append({"section": _section_of(text, offset if offset >= 0 else 0),
                                "placeholder": match.group(0),
                                "reason": match.group(2), "kind": _kind(match.group(2) or line),
                                "quote": stripped[:80]})
            out.append(new_line)
            continue
        if is_heading:
            out.append(_PLACEHOLDER.sub("", line))
            continue

        # 正文段落：逐句处理（含"裸词"整改：有"待补充/待明确"的整句删掉）
        kept_sentences: list[str] = []
        for sentence in _SENTENCE_SPLIT.split(line):
            if any(word in sentence for word in _BARE_PHRASES) and not _PLACEHOLDER.search(sentence):
                offset = text.find(sentence)
                removed.append({"section": _section_of(text, offset if offset >= 0 else 0),
                                "placeholder": next(word for word in _BARE_PHRASES if word in sentence),
                                "reason": sentence.strip()[:60], "kind": "drop",
                                "quote": sentence.strip()[:80]})
                continue
            if not sentence.strip():
                continue
            if not _PLACEHOLDER.search(sentence):
                kept_sentences.append(sentence)
                continue
            offset = text.find(sentence)
            for match in _PLACEHOLDER.finditer(sentence):
                removed.append({"section": _section_of(text, offset if offset >= 0 else 0),
                                "placeholder": match.group(0),
                                "reason": match.group(2), "kind": _kind(match.group(2) or sentence),
                                "quote": sentence.strip()[:80]})
            cleaned = _PLACEHOLDER.sub("", sentence)
            if any(word in cleaned for word in _BARE_PHRASES):
                removed.append({"section": _section_of(text, offset if offset >= 0 else 0),
                                "placeholder": next(word for word in _BARE_PHRASES if word in cleaned),
                                "reason": cleaned.strip()[:60], "kind": "drop",
                                "quote": sentence.strip()[:80]})
                continue
            # 去掉占位后剩下的"骨架"是否还有实质内容（去掉标点后 ≥8 字）
            skeleton = re.sub(r"[\s，。；：、（）()]", "", cleaned)
            # 去掉占位后如果句子以连接词结尾（"标注为。""包括。"），说明骨架不完整 → 整句删掉
            tail = skeleton.rstrip("。；，、")
            dangling = ("为", "是", "如下", "包括", "如", "见", "以", "由", "需", "应", "的", "和", "及", "等", "于", "按")
            if any(tail.endswith(word) for word in dangling):
                continue
            # 占位删掉后是否出现"断裂"（如"以为依据"）→ 只删断裂的**分句**，保留其余实质内容
            broken = ("以为", "以为准", "以为依据", "以为基础", "按照为", "以、", "、以",
                      "如下", "详见", "参见", "标注为", "为。", "是。")
            if any(token in cleaned for token in broken):
                clauses = re.split(r"([，,])", cleaned)
                kept_clauses, drop_count = [], 0
                for clause in clauses:
                    if not clause.strip():
                        continue
                    if clause in ("，", ","):
                        kept_clauses.append(clause)
                    elif any(token in clause for token in broken):
                        drop_count += 1
                        continue
                    else:
                        kept_clauses.append(clause)
                repaired = "".join(kept_clauses).strip("，, ")
                if repaired:
                    repaired = repaired.rstrip("，, ")
                    if not repaired.endswith(("。", "；", "！", "？")):
                        repaired += "。"
                    kept_sentences.append(repaired)
                continue            # 断裂句已处理（要么修复后保留，要么丢弃）
            if len(skeleton) >= 8:
                cleaned = re.sub(r"[，、；]{2,}", "，", cleaned)
                cleaned = re.sub(r"（\s*）|\(\s*\)", "", cleaned)
                kept_sentences.append(cleaned)
        rebuilt = "".join(kept_sentences).strip()
        if rebuilt:
            out.append(rebuilt)

    result = "\n".join(out)
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = re.sub(r"[ \t]+\n", "\n", result)
    had_leading_blank = text.startswith("\n")
    result = re.sub(r"[，、；]{2,}", "，", result)
    result = re.sub(r"[，。]{2,}", "。", result)
    result = re.sub(r"[。]{2,}", "。", result)
    result = result.rstrip()
    if had_leading_blank and result and not result.startswith("\n"):
        result = "\n" + result
    return result, removed


def classify(removed: list[dict]) -> dict:
    """按"系统去查 / 需要你上传 / 直接删掉"分类汇总。"""
    groups: dict[str, list[dict]] = {"search": [], "upload": [], "drop": []}
    for item in removed:
        groups.setdefault(item["kind"], []).append(item)
    return {
        "search": groups["search"],
        "upload": groups["upload"],
        "dropped": groups["drop"],
        "summary": {key: len(value) for key, value in groups.items()},
    }

"""L0 规则预检：零成本、秒级，先把"规则能判的硬伤"挑出来。

为什么需要：每章双角色 LLM 评审约 1.2 万 token / 30~60 秒，
但占位符残留、空表、无数字、无来源这类问题**规则就能判**。
L0 先跑（<50ms），有 critical 就先修硬伤，不必烧精评。

规则集（v1 八条）见 specs/review-v3/spec.md §A2。
"""
from __future__ import annotations

import re

_HEADING = re.compile(r"^#{2,3}[ \t]+(.+)$", re.M)
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.M)

PLACEHOLDER_PATTERNS = (
    re.compile(r"【待[^】]{0,12}】"),
    re.compile(r"(待补充|待明确|待确认|待落实|待提供|待完善)"),
    re.compile(r"\b(TBD|TODO|FIXME)\b", re.I),
    re.compile(r"(XX|ＸＸ)(?:年|月|日|公司|项目)"),
)
SOURCE_PATTERNS = (re.compile(r"【来源"), re.compile(r"（第\s*\d+\s*页"), re.compile(r"\(第\s*\d+\s*页"))
SENTENCE_END = "。；！？!?.;"


def _sections(content: str) -> list[tuple[str, str]]:
    """按 `## ` 切小节 → [(标题, 正文)]；无标题时整篇算一节。"""
    text = str(content or "")
    matches = list(_HEADING.finditer(text))
    if not matches:
        return [("", text)]
    out: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        out.append((match.group(1).strip(), text[start:end]))
    return out


def _empty_table_ratio(text: str) -> float:
    rows = _TABLE_ROW.findall(text)
    if not rows:
        return 0.0
    empty = 0
    for row in rows:
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        if all(not cell or set(cell) <= set("-: ") for cell in cells):
            empty += 1
    return empty / len(rows)


def precheck(content: str) -> dict:
    """规则预检：返回 {level, counts, items}。"""
    text = str(content or "")
    body_len = len(re.sub(r"\s+", "", text))
    items: list[dict] = []

    def add(code: str, severity: str, message: str, section: str = "", hint: str = "", quote: str = "") -> None:
        items.append({"code": code, "severity": severity, "message": message,
                      "section": section, "hint": hint, "quote": quote[:80]})

    # ① 占位符
    for pattern in PLACEHOLDER_PATTERNS:
        match = pattern.search(text)
        if match:
            add("placeholder", "critical", "正文仍有占位符/未完成表述",
                hint="改成完整表述（哪怕概括），不要留「待补充」", quote=match.group(0))
            break

    # ② 空表
    for title, section_body in _sections(text):
        if _empty_table_ratio(section_body) >= 0.5:
            add("empty_table", "high", "表格过半单元格为空", section=title,
                hint="补数据或改成文字表述")
            break

    # ③ 无量化事实
    if body_len >= 200 and len(re.findall(r"\d", text)) < 5:
        add("no_numbers", "high", "全章缺少量化事实（数字少于 5 处）",
            hint="补充系统数量/数据量/用户数/等保级别等具体数字")

    # ④ 无来源标注
    if body_len >= 200 and not any(pattern.search(text) for pattern in SOURCE_PATTERNS):
        add("no_source", "high", "没有【来源】或页码标注", hint="给关键事实补【来源：…】")

    # ⑤ 过短小节
    for title, section_body in _sections(text):
        stripped = len(re.sub(r"\s+", "", section_body))
        if title and 0 < stripped < 120:
            add("short_section", "medium", f"小节「{title[:12]}」正文过短（{stripped} 字）", section=title,
                hint="至少写清 目标-做法-验证 三句")

    # ⑥ 相邻重复段
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if len(re.sub(r"\s+", "", p)) > 40]
    for index in range(len(paragraphs) - 1):
        if paragraphs[index] == paragraphs[index + 1]:
            add("dup_paragraph", "high", "相邻两段内容完全重复", hint="删掉重复段或改写",
                quote=paragraphs[index][:60])
            break

    # ⑦ 结尾截断
    stripped_text = text.rstrip()
    if body_len >= 200 and stripped_text and stripped_text[-1] not in SENTENCE_END and stripped_text[-1] not in "|】)`":
        add("broken_tail", "critical", "正文结尾不完整（不是句末标点）",
            hint="补完最后一句", quote=stripped_text[-40:])

    # ⑧ 整体过短
    if body_len < 400:
        add("too_short", "high", f"全章仅 {body_len} 字，明显不足", hint="按计划交付物补齐")

    counts = {"critical": 0, "high": 0, "medium": 0}
    for item in items:
        if item["severity"] in counts:
            counts[item["severity"]] += 1
    level = "fail" if counts["critical"] else ("warn" if items else "pass")
    return {"level": level, "counts": counts, "items": items[:12],
            "body_len": body_len, "sections": len(_sections(text))}


def resolve_tier(precheck_result: dict, requested: str = "auto") -> str:
    """分层调度：auto → L0 有 critical 走 fast（先修硬伤），否则 full。"""
    requested = str(requested or "auto").lower()
    if requested in ("fast", "full"):
        return requested
    has_critical = int((precheck_result or {}).get("counts", {}).get("critical") or 0) > 0
    return "fast" if has_critical else "full"

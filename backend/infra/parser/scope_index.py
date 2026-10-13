"""范围索引：从整份招标文件里建出「标的/标包」索引，并把评分表按**表边界**切开。

用户提出的真实困难（2026-10-13）：
> 拆分需要从整个招标文件中找到对应的技术论述和技术评审的打分点。这里会有很多干扰
> 信息，可能会有多个标包、多个标的，也有可能会有商务、技术不同的评分点。

实测（项目 19，南网中台）就是最坏情况：
- 「标的/标包清单」表里 **标的1→标包1**、**标的2→标包1/标包2**；
- 「（2）详细评审标准」一节里其实有 **3 张评分表**，每张各有自己的「适用标包：」声明，
  每张表内部再分「（一）、商务评审（100）分 /（二）、技术评审（100）分 / 报价」；
- 旧抽取器把 3 张表**混成一套 25 条**，于是标的1 的目录里混进了 95598/智能客服的评分点。

本模块只做两件事，都是**确定性**的：
1. `parse_package_index()`：从清单表建 `标的N → [(标包号, 标包名称)]`；
2. `split_scoring_tables()`：用「适用标包：」声明把评分表切成独立段，每段一个范围。

有了它，下游才能"先看地图再定向筛选"，而不是靠关键词在全文里撒网。
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

# 「适用标包：xxx」/「下列评审标准适用的标的/标包：标包2：xxx」
_SCOPE_DECL_RE = re.compile(
    r"(?:适用标包|下列评审标准适用的[^：:]*)\s*[：:]\s*([^|｜\n]{2,200})"
)
# 清单表表头关键词
_INDEX_HEADER = ("标的", "标包号", "标包名称")
# 章节/条号前缀：`3.2.1. `、`（一）、`、`二、`
_NUM_PREFIX_RE = re.compile(r"^(?:[0-9]+(?:\.[0-9]+)*[.、]?|[（(][一二三四五六七八九十]+[）)]|[一二三四五六七八九十]+、)\s*")


def normalize_scope_name(name: str) -> str:
    """范围名称归一化：去空白、去换行、全角转半角、去尾部后缀噪声。

    招标文件里同一个标包在不同地方写法不同（全名 / 括号内简称 / 带"采购项目"后缀），
    所以匹配必须归一化，而不是精确比较。
    """
    s = (name or "").strip()
    s = s.replace("<br>", "").replace("<br/>", "")
    s = re.sub(r"\s+", "", s)
    s = s.translate(str.maketrans("（）［］【】", "()[][]"))
    for suffix in ("采购项目", "服务采购项目", "项目"):
        if s.endswith(suffix) and len(s) > len(suffix) + 2:
            s = s[: -len(suffix)]
    return s


def _cells(line: str) -> list[str]:
    raw = line.strip()
    if raw.startswith("|"):
        raw = raw[1:]
    if raw.endswith("|"):
        raw = raw[:-1]
    return [c.strip() for c in raw.split("|")]


def _clean_cell(c: str) -> str:
    return re.sub(r"\s+", "", (c or "").replace("<br>", "").replace("<br/>", ""))


def parse_package_index(sections: Iterable) -> list[dict]:
    """从「标的清单及分包情况」表建索引。

    返回 ``[{"target": "标的1", "target_name": …, "packages": [{"no": "1", "name": …}]}]``。

    表格是合并单元格（标的一列只在第一行出现），所以要**向下继承**标的值。
    """
    def _get(sec, name, default=""):
        return sec.get(name, default) if isinstance(sec, dict) else getattr(sec, name, default)

    targets: list[dict] = []
    for sec in sections or []:
        content = _get(sec, "raw_content", "") or ""
        if not content or "标包" not in content:
            continue
        lines = [l for l in content.splitlines() if l.strip().startswith("|")]
        if not lines:
            continue
        header_idx = None
        for i, line in enumerate(lines):
            joined = "".join(_clean_cell(c) for c in _cells(line))
            if all(h in joined for h in _INDEX_HEADER):
                header_idx = i
                break
        if header_idx is None:
            continue

        cur_target = ""
        for line in lines[header_idx + 1:]:
            cells = [_clean_cell(c) for c in _cells(line)]
            if not any(cells) or set("".join(cells)) <= {"-", " "}:
                continue
            # 列顺序：序号 | 标的 | 概算金额 | 标包号 | 标包名称 | 最高限价 | …
            if len(cells) < 5:
                continue
            target_name = cells[1]
            pkg_no = cells[3] if cells[3].isdigit() else ""
            pkg_name = cells[4]
            if target_name:
                cur_target = target_name
                targets.append({"target": f"标的{len(targets) + 1}", "target_name": target_name,
                                "packages": []})
            if not targets or not pkg_no or not pkg_name:
                continue
            targets[-1]["packages"].append({"no": pkg_no, "name": pkg_name})
    return [t for t in targets if t["packages"]]


def split_scoring_tables(sections: Iterable) -> list[dict]:
    """把评分表按「适用标包：」声明切成独立段。

    返回 ``[{"scope_label": …, "section_id": …, "title": …, "content": …}]``；
    没有范围声明的表归为 ``scope_label=""``（下游会要求用户确认）。
    """
    def _get(sec, name, default=""):
        return sec.get(name, default) if isinstance(sec, dict) else getattr(sec, name, default)

    tables: list[dict] = []
    for sec in sections or []:
        content = _get(sec, "raw_content", "") or ""
        if not content:
            continue
        # 只在"看起来是评分表"的章节里切：要有评分因素/详细评审项/分值 这类列名
        if not any(k in content for k in ("评分因素", "详细评审项", "评审分项要素", "分值")):
            continue

        lines = content.splitlines()
        segments: list[dict] = []
        cur = {"scope_label": "", "lines": []}
        for line in lines:
            m = _SCOPE_DECL_RE.search(line)
            if m:
                if cur["lines"]:
                    segments.append(cur)
                cur = {"scope_label": m.group(1).strip(), "lines": [line]}
                continue
            cur["lines"].append(line)
        if cur["lines"]:
            segments.append(cur)

        for seg in segments:
            text = "\n".join(seg["lines"]).strip()
            if not text:
                continue
            source_file = _get(sec, "source_file", "") or ""
            scope_label = seg["scope_label"]
            scope_from_file = False
            if not scope_label:
                # 跨文件场景：技术评分表可能整张放在技术规范书里、没有「适用标包」声明，
                # 但文件名常写着「标的1」「标包2」—— 用它兜底归属，否则这张表会被
                # 当成"无范围"，与该标的其他表各自为政（实测多文件项目）。
                toks = parse_target_tokens(source_file)
                if toks:
                    scope_label = toks[0]
                    scope_from_file = True
            tables.append({
                "scope_label": scope_label,
                "scope_from_file": scope_from_file,
                "section_id": _get(sec, "id", "") or "",
                "title": _get(sec, "title", "") or "",
                "source_file": source_file,
                "content": text,
            })
    return tables


def resolve_scope(scope_label: str, index: list[dict]) -> dict:
    """把「适用标包」的写法归一到 ``(标的, 标包号, 标包名称)``。

    匹配顺序：
    1. 与索引里**标包名称**归一化后互相包含 → 命中（最可靠）；
    2. 与**标的名称**互相包含 → 命中该标的（标包号留空，多标包时需确认）；
    3. 都命中不了 → ``resolved=False``，让用户确认（**不猜**）。
    """
    label = normalize_scope_name(scope_label)
    if not label:
        return {"resolved": False, "target": "", "target_name": "", "package_no": "",
                "package_name": "", "reason": "评分表没有「适用标包」声明"}

    best = None
    for t in index:
        for pkg in t["packages"]:
            name = normalize_scope_name(pkg["name"])
            if name and (name in label or label in name):
                # 括号里的简称也常出现在声明中
                best = {"resolved": True, "target": t["target"], "target_name": t["target_name"],
                        "package_no": pkg["no"], "package_name": pkg["name"],
                        "reason": f"适用标包名称与清单表「{pkg['name']}」匹配"}
                break
        if best:
            break

    if best is None:
        for t in index:
            tname = normalize_scope_name(t["target_name"])
            if tname and (tname in label or label in tname):
                best = {"resolved": True, "target": t["target"], "target_name": t["target_name"],
                        "package_no": "", "package_name": "",
                        "reason": f"适用标包名称与清单表「{t['target_name']}」所属标的匹配"}
                break

    if best is None:
        return {"resolved": False, "target": "", "target_name": "", "package_no": "",
                "package_name": "", "reason": "适用标包名称在清单表里找不到对应标包"}
    return best


def parse_target_tokens(instruction: str) -> list[str]:
    """从提炼要求里读范围词：``标的1`` / ``标包2`` / ``标段3``。

    返回形如 ``["标的1", "标包2"]``（按出现顺序）。
    """
    text = instruction or ""
    out: list[str] = []
    for m in re.finditer(r"(标的|标包|标段)\s*([0-9一二三四五六七八九十]+)", text):
        token = f"{m.group(1)}{m.group(2)}"
        if token not in out:
            out.append(token)
    return out

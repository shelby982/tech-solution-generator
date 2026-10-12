"""张衡 — 规范书解析专家 agent。

职责（spec §3）：
1. parse: 把规范书文件解析为 toc + 项目概述（doc_summary）
2. extract: 对每章节做 8 字段响应矩阵提炼（含 ★/▲ 标记的特殊处理）

不持有 LLM 配置；通过依赖注入接受 services.config_store.config_store 或自己的轻量 wrapper。
本期直接 import config_store 单例（route 层会注入相同实例）。
"""

import inspect
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, BinaryIO, Callable, Optional, Union

from domain.spec import OutlineMatrixRow
from domain.spec.field_normalize import normalize_row_fields
from domain.spec import Section as DomainSection
from domain.spec.outline_draft import (
    MIN_TOP_LEVEL_NODES,
    build_sections,
    count_top_level,
    normalize_nodes,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# parse 阶段产出
# ─────────────────────────────────────────────

@dataclass
class SpecParseResult:
    """parse 阶段的产出。

    与 spec §5 SpecState 字段对齐，但 outline_matrix 由 extract 阶段填充。
    """
    doc_id: str
    doc_title: str
    doc_summary: str
    toc: list[DomainSection]
    # 项目概述生成失败的原因（空串 = 成功）。摘要生成当前已停用（见 parse），
    # 所以恒为空串；字段与它的消费方（state、闸门 1）保留不动 —— 恢复摘要时
    # 不该还要回头改这条链路。
    doc_summary_error: str = ""
    # 解析失败或被跳过（解析成功但 0 章节）的文件名。非空时由 parse 节点写进
    # spec.parse_failed_files，闸门 1 展示 —— 静默缺席会让用户以为它参与了派生。
    failed_files: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────
# parse：多文件进度
# ─────────────────────────────────────────────

def _label_progress(
    callback: Optional[Callable[[str, int, int], None]],
    filename: str,
    idx: int,
    total: int,
) -> Optional[Callable[[str, int, int], None]]:
    """给单份文件的解析进度加上「第几份 / 共几份 + 文件名」前缀。

    单份时不加前缀，保持改造前的进度文案不变。
    """
    if callback is None or total <= 1:
        return callback

    def _wrapped(step: str, current: int, total_steps: int) -> None:
        callback(f"[{idx}/{total}] {filename} · {step}", current, total_steps)

    return _wrapped


# ─────────────────────────────────────────────
# 目录派生：grounding 辅助
# ─────────────────────────────────────────────

# 每个派生章节从规范书原文摘录进 raw_content 的字符上限
GROUNDING_SNIPPET_CHARS = 3000


def _render_toc_text(sections: list[DomainSection]) -> str:
    """把目录渲染成缩进文本，供「上一版目录」进 prompt。"""
    lines = []
    for sec in sections:
        indent = "  " * max(0, int(sec.level or 1) - 1)
        lines.append(f"{indent}- {sec.title}")
    return "\n".join(lines)


def _ground_sections(
    sections: list[DomainSection],
    source_sections: list[DomainSection],
) -> None:
    """用 BM25 把派生章节挂回规范书原文（就地修改）。

    模型只产出标题，而 ``extract`` 依赖每节的 ``raw_content`` 与 ``special_marks``
    （★/▲ 是 8 字段提炼 prompt 的关键输入）。这里用确定性检索补，而不是让模型写
    「依据」——后者会幻觉，还白费 token。

    命中则 `raw_content = 原文章节标题 + 正文[:3000]` 并继承其 special_marks；
    未命中就留空，extract 会走「内容较短」分支，不报错。

    BM25 在小语料上会退化：``rank_bm25`` 对「出现在半数文档里的词」算出的 idf 为 0，
    规范书只有两三个章节时整条检索都会落空。所以 BM25 无命中时再退一级到
    「词重叠最多」的确定性兜底，仍然零重叠才留空。
    """
    if not sections or not source_sections:
        return

    from infra.retrieval.keyword import (
        build_bm25_index,
        keyword_search,
        tokenize,
    )

    corpus = [
        {"content": f"{s.title}\n{s.raw_content}", "_src_idx": i}
        for i, s in enumerate(source_sections)
    ]
    index, _ = build_bm25_index(corpus)

    for sec in sections:
        if not sec.title:
            continue
        hits = keyword_search(corpus, sec.title, top_k=1, bm25_index=index)
        if hits:
            src = source_sections[hits[0]["_src_idx"]]
        else:
            src = _best_overlap_section(sec.title, source_sections, tokenize)
            if src is None:
                continue
        sec.raw_content = f"{src.title}\n{src.raw_content}"[:GROUNDING_SNIPPET_CHARS]
        sec.special_marks = list(src.special_marks)


def _best_overlap_section(query: str, source_sections, tokenize_fn):
    """BM25 兜底：返回与 query 词重叠最多的原文章节，零重叠返回 None。"""
    query_tokens = set(tokenize_fn(query))
    if not query_tokens:
        return None
    best, best_score = None, 0
    for src in source_sections:
        overlap = len(query_tokens & set(tokenize_fn(f"{src.title}\n{src.raw_content}")))
        if overlap > best_score:
            best, best_score = src, overlap
    return best


# ─────────────────────────────────────────────
# 目录派生：产出
# ─────────────────────────────────────────────

@dataclass
class OutlineDraftResult:
    """draft_outline 的产出。

    的行为），``error`` 写明原因供前端展示。
    """
    sections: list[DomainSection]
    error: str = ""


# ─────────────────────────────────────────────
# Agent
# ─────────────────────────────────────────────

def named_files_in(instruction: str, file_names: list[str]) -> list[str]:
    """要求在文字里**点名**了哪几份文件（整名或去扩展名的主干都能认）。

    「按照评分表拆分章节」这类说法里没有标包/标段词族，区段定位与模型兜底的门控
    都不会触发 —— 而它恰恰是最常见的诉求（评分表本身就是拆章依据）。这里做**确定性**
    的文件名匹配：命中就把那份文件的章节整体当材料，连一次模型调用都不用花。
    """
    text = re.sub(r"\s+", "", instruction or "").lower()
    if not text:
        return []
    picked = []
    for name in file_names or []:
        clean = re.sub(r"\s+", "", str(name or "")).lower()
        if not clean:
            continue
        stem = clean.rsplit(".", 1)[0] if "." in clean else clean
        if clean in text or (len(stem) >= 2 and stem in text):
            picked.append(name)
    return picked


class RequirementParseError(RuntimeError):
    """要求文件**全部**解析失败（单份失败只跳过并记录，不抛）。"""


class OutlineDraftError(RuntimeError):
    """目录派生失败。

    **不降级**：派生失败时不再偷偷沿用规范书原始目录 —— 那份目录不是模型按提炼要求
    拆的，用户会误以为派生成功（实测踩过：`outline_error=Connection error.`，页面
    照常显示原始目录，用户还按它往下走）。现在直接抛，由 runner 把运行标成 failed，
    前端明确报错并提供「重新拆分目录」重试。
    """


class ZhangHengAgent:
    """张衡：规范书解析与 8 字段响应矩阵提炼。"""

    def __init__(
        self,
        configs_provider: Optional[Callable[[], tuple[list, int]]] = None,
    ):
        """
        configs_provider: 返回 (configs, rr_start_index) 的函数。
        默认从 services.config_store 读，便于单测注入 fixture configs。
        """
        if configs_provider is None:
            from services.config_store import config_store
            configs_provider = lambda: config_store.get_configs_and_next_index()
        self._configs_provider = configs_provider

    # ── parse ──────────────────────────────────

    async def parse(
        self,
        sources: list[tuple[Union[str, BinaryIO], str, str]],
        *,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> SpecParseResult:
        """解析「应标要求」面板下的全部文件（规范书 / 主招标文件 / 评分表 / 评审要素）。

        sources: ``[(file_source, suffix, filename), ...]``，按上传顺序。

        多份文件的目录**按顺序拼接**成一份 source_toc，供后续 outline_draft 做
        grounding 与降级兜底。

        - 调 infra.parser.parse_document 拿到 ParsedDocument（逐份）
        - 把 infra.parser.Section → domain.spec.Section
        - doc_summary 当前不生成（恒为空串），原因见下方注释
        """
        import asyncio

        from infra.parser import parse_document

        if not sources:
            raise ValueError("没有可解析的要求文件")

        # (ParsedDocument, 文件名) —— 配对在**解析成功时**就定下来：
        # 跳过的文件不在列表里，用 zip(docs, sources) 会整体错位（坏文件的名字
        # 会安到后面那份好文件的章节上，进而是按区段切段时切错文件）。
        parsed_docs: list[tuple[object, str]] = []
        failed_files: list[str] = []
        for idx, (file_source, suffix, filename) in enumerate(sources, start=1):
            # parse_document 是同步且耗时（解析 docx/pdf）。直接 await 会阻塞事件循环，
            # 导致 SSE emitter 的 stream() 在解析期间一帧都吐不出。扔到后台线程跑。
            try:
                doc = await asyncio.to_thread(
                    parse_document,
                    file_source,
                    suffix=suffix,
                    filename=filename,
                    progress_callback=_label_progress(
                        progress_callback, filename, idx, len(sources),
                    ),
                )
            except Exception as e:
                # **单份隔离**：磁盘缺文件那条路早就只告警跳过了（main.py），
                # 但「文件在、内容坏」（损坏 PDF / 旧版 .doc 无转换器 / 不支持的格式）
                # 之前会让整次解析失败 —— 规范书+评分表+评审要素里坏一份，全部白解析。
                logger.warning(f"张衡：要求文件「{filename}」解析失败，跳过：{e}")
                failed_files.append(filename or f"第 {idx} 份文件")
                continue
            parsed_docs.append((doc, filename))
            if not doc.sections:
                # 解析成功但一个章节都没有（无文字层且 OCR 无输出 / 空文档）：
                # 静默缺席会让用户以为它参与了，这里记下来回传前端
                logger.warning(f"张衡：要求文件「{filename}」没有解析出任何章节")
                failed_files.append(f"{filename}（无章节）")

        if not parsed_docs:
            raise RequirementParseError(
                "要求文件全部解析失败："
                + "、".join(failed_files or ["未知原因"])
                + "。请检查文件格式（支持 PDF / DOCX / DOC）与内容是否可读"
            )

        # infra Section → domain Section。
        # 每份文档的 section id 都是 s1..sN，直接拼接会撞车，所以按拼接后的
        # 顺序重新编号 —— source_toc 的 id 只用于 grounding 的位置索引与占位行，
        # 重新编号不影响任何下游语义。
        toc: list[DomainSection] = []
        doc_titles: list[str] = []
        for doc, filename in parsed_docs:
            if doc.title:
                doc_titles.append(doc.title)
            for s in doc.sections:
                toc.append(DomainSection(
                    id=f"s{len(toc) + 1}",
                    level=s.level,
                    title=s.title,
                    raw_content=s.raw_content,
                    special_marks=list(s.special_marks),
                    # 多份文件拼接后文件边界只剩这一处线索（见 Section.source_file）
                    source_file=filename or "",
                ))

        # 摘要生成暂时停用（2026-09-17）。
        #
        # 用 deepseek-v4-pro 这类推理模型时这一步**必然**返回空：摘要走
        # dispatcher._generate_doc_summary，实参是 max_tokens=2000，而推理模型的
        # max_tokens 是「思考 + 正文」之和 —— 2000 连思考都写不完，正文一个字不吐。
        # 跑一次只换来一条「模型返回空内容」的降级提示和一次无谓的等待。
        #
        # 跳过它不改变任何下游输入：doc_summary 本来就会是空串，目录派生
        # （见本类 draft_outline 的 payload）与单章节正文生成
        # （dispatcher.dispatch_stream_generate 的 doc_summary 形参）拿到的都是空值，
        # 两条路径对空串都已有分支。
        #
        # 恢复：把 infra.llm.dispatch_doc_summary 的调用接回来，**并同时**给它足够的
        # max_tokens（或改用非推理模型），否则接回来的只是那条降级提示。
        doc_summary = ""
        doc_summary_error = ""

        # 多份文件时 doc_title 用**文件名**拼接：各文档的 doc.title 是解析器猜的
        # （pdf 路径常常只是正文第一个一级标题），几份拼起来既不是标题也不含来源信息。
        if len(parsed_docs) > 1:
            doc_title = "、".join(name for _doc, name in parsed_docs if name) or "、".join(doc_titles)
        else:
            doc_title = doc_titles[0] if doc_titles else ""
        return SpecParseResult(
            doc_id=parsed_docs[0][0].doc_id,
            doc_title=doc_title,
            doc_summary=doc_summary,
            toc=toc,
            doc_summary_error=doc_summary_error,
            failed_files=failed_files,
        )

    # ── draft_outline ──────────────────────────

    async def draft_outline(
        self,
        source_toc: list[DomainSection],
        *,
        instruction: str = "",
        doc_summary: str = "",
        previous_toc: Optional[list[DomainSection]] = None,
    ) -> OutlineDraftResult:
        """依据规范书目录 + 项目概述 + 用户提炼要求，派生「应答文件目录」。

        与 ``extract`` 的分工：本方法只产出**目录结构**（id/level/title +
        grounding 回的 raw_content/special_marks），8 字段章节要求仍由 ``extract``
        逐节点填。职责边界与改造前一致，只是 toc 的来源从正则变成了模型。

        **失败即抛** :class:`OutlineDraftError`（不再降级沿用原始目录）：原始目录不是
        按用户提炼要求拆的，静默展示会让用户以为派生成功。调用方（节点 / runner）负责
        把运行标成 failed 并把原因显示给用户，用户可点「重新拆分目录」重试。
        """
        from agents.prompts import build_spec_digest

        if not source_toc:
            raise OutlineDraftError("目录派生失败：要求文件里没有解析出任何章节")

        configs, rr_start = self._configs_provider()
        if not configs:
            raise OutlineDraftError(
                "目录派生失败：未配置可用模型，请先在「设置」中添加模型配置"
            )

        # 先按提炼要求定位「用文件的哪一部分」，再拿这部分去派生。
        # 整份文件灌进去时，招标公告/资格要求/合同范本会把真正要的章节挤出预算。
        flat = [
            {"title": s.title, "content": s.raw_content, "level": s.level,
             "source_file": getattr(s, "source_file", "") or ""}
            for s in source_toc
        ]
        digest_sections, follow_structure, locate_note = await self._locate_for_digest(
            flat, instruction, configs, rr_start,
        )

        payload = {
            "instruction": instruction,
            "doc_summary": doc_summary,
            "spec_digest": build_spec_digest(digest_sections),
            # 定位到了用户点名的那一部分 → 目录按那部分自身的结构拆，不重新组织
            "follow_structure": follow_structure,
            "previous_outline": (
                _render_toc_text(previous_toc) if previous_toc else ""
            ),
            # 本轮不读素材；保留该键作为下一轮接入素材的扩展点。
            "material_digest": "",
        }

        import infra.llm

        try:
            raw = await infra.llm.dispatch_outline_draft_json(configs, rr_start, payload)
        except Exception as e:
            logger.warning(f"张衡：目录派生失败，模型调用异常：{e}")
            raise OutlineDraftError(f"目录派生失败：模型调用失败：{e}") from e

        # 输出被截断时抢救出的目录只是部分成功 —— 让用户在闸门看到，而不是
        # 只看目录少了几节。占位符由 dispatcher 在抢救路径上放置。
        truncated = raw.get("_truncated_nodes") if isinstance(raw, dict) else None
        notes: list[str] = []
        if truncated:
            notes.append(
                f"模型输出被截断，仅保留前 {int(truncated)} 个章节，"
                f"建议删掉过长的提炼要求后点「重新拆分目录」重试"
            )

        nodes, warnings = normalize_nodes(raw.get("nodes"))
        sections = build_sections(nodes)

        if not sections:
            raise OutlineDraftError("目录派生失败：模型输出无法解析出目录节点")
        if count_top_level(sections) < MIN_TOP_LEVEL_NODES:
            raise OutlineDraftError(
                f"目录派生失败：目录结构不完整（仅 {count_top_level(sections)} 个一级章节，"
                f"至少需要 {MIN_TOP_LEVEL_NODES} 个）"
            )

        _ground_sections(sections, source_toc)

        # note（范围没定位到 / 输出被截断）排在最前：它们比 normalize 的告警更需要用户先看到
        messages = ([locate_note] if locate_note else []) + notes + list(warnings)
        return OutlineDraftResult(
            sections=sections,
            error="；".join(messages),
        )

    async def _locate_for_digest(
        self,
        flat: list[dict],
        instruction: str,
        configs: list,
        rr_start: int,
    ) -> tuple[list[dict], bool]:
        """挑出进 digest 的材料：命中区段带正文，其余只留标题。

        返回 ``(digest 条目, 是否定位到了用户点名的那一部分)``。第二个值为真时，
        调用方要求模型**按那部分自身的结构**拆章节 —— 用户说的「按照评分要求中的
        每一点进行大纲拆分章节」，指的就是复刻这一部分的结构。

        三条路径，逐级退化，任何一步失败都不会让内容凭空消失：

        1. **区段定位**（确定性）：要求里点名了标包/标的/标段时，按文档里的同类
           锚点整段切出。实测一份 164 页采购文件里标包2 的评分标准只占 7.3%，
           章级检索命中不了（那些章节的标题里一个「标包2」都没有）。
        2. **模型兜底**：有范围标识却找不到锚点时，让模型把口语化说法翻成字面
           关键词，拿回来重跑一次路径 1。
        3. **章级定位**：上面都不成，退回 ``locate_sections`` —— 与改造前一致。
           这条路只是「挑出相关章节」，没定位到用户点名的部分，结构仍由模型组织。
        """
        from infra.retrieval.locate import locate_sections
        from infra.retrieval.segment import (
            diagnose_scope_miss, extract_scope_tokens, locate_segments,
        )

        known_files = sorted({s["source_file"] for s in flat if s.get("source_file")})
        segments = locate_segments(flat, instruction)
        note = ""

        # 先看要求里是否**点名了文件**（「按评分表拆」）——这是确定性的，不用调模型。
        named = named_files_in(instruction, known_files)
        if segments is None and named:
            scoped = [s for s in flat if s.get("source_file") in named]
            if scoped:
                return (
                    [{"title": s["title"], "content": s["content"]} for s in scoped],
                    True,
                    f"已按文件名定位到：{'、'.join(named)}",
                )

        # 只有「用户确实点了范围、却哪儿都认不出」才值得多花一次模型调用；
        # 要求里压根没有范围标识时（「按评分项逐条拆章」），模型也无从下手。
        if segments is None and extract_scope_tokens(instruction):
            keyword, picked_files = await self._model_scope_keyword(
                instruction, [s["title"] for s in flat], configs, rr_start,
                file_names=known_files,
            )
            # 「按评分表拆章」这类**点名文件**的说法：模型认出是哪一份，就把那份文件的
            # 章节整体当作材料（结构锁死 = 照它的章节组织，不再由模型重新组织）。
            if picked_files:
                scoped = [s for s in flat if s.get("source_file") in picked_files]
                if scoped:
                    return (
                        [{"title": s["title"], "content": s["content"]} for s in scoped],
                        True,
                        f"已按文件名定位到 {len(picked_files)} 份材料"
                        f"（{'、'.join(picked_files)}）",
                    )
            if keyword:
                segments = locate_segments(flat, keyword)

        if segments is not None:
            return (
                [
                    {"title": s.title, "content": s.content, "selected": s.selected}
                    for s in segments
                ],
                True,
                note,
            )

        # 没定位到：如果用户点名了范围，必须让他在闸门看得到（并给出下一步），
        # 否则目录是按全文拆的，用户却以为「按标包2 拆」生效了。
        reason = diagnose_scope_miss(flat, instruction)
        if reason:
            note = (f"{reason}；已按全量材料拆分目录 —— "
                    f"可改写在提炼要求里的说法后点「重新拆分目录」重试")

        located = locate_sections(flat, instruction)
        return (
            [
                {"title": flat[i]["title"], "content": flat[i]["content"]}
                for i in located
            ],
            False,
            note,
        )

    async def _model_scope_keyword(
        self,
        instruction: str,
        titles: list[str],
        configs: list,
        rr_start: int,
        file_names: Optional[list[str]] = None,
    ) -> tuple[str, list[str]]:
        """兜底：让模型把要求里的说法换成文档里的字面标识。

        返回 ``(检索关键词, 命中的文件名)``。prompt 一直要求模型同时给出 ``files``，
        但旧实现只取 ``keywords``、还恒传空 ``file_names`` —— 于是「按评分表拆」这种
        **点名文件**的说法完全没救。失败返回 ``("", [])``。
        """
        import infra.llm

        known = [n for n in (file_names or []) if n]
        try:
            obj = await infra.llm.dispatch_scope_select_json(configs, rr_start, {
                "instruction": instruction,
                "file_names": known,
                "section_titles": titles,
            })
        except Exception as e:
            logger.warning(f"张衡：检索范围兜底失败，退回章级定位：{e}")
            return "", []

        keywords = [
            str(k).strip()
            for k in (obj.get("keywords") or [])
            if isinstance(k, (str, int, float)) and str(k).strip()
        ]
        # 只采信**确实存在**的文件名（模型可能编一个）
        picked_files = [
            name for name in (str(f).strip() for f in (obj.get("files") or []))
            if name and name in known
        ]
        if picked_files:
            logger.info(f"张衡：模型圈定文件 {picked_files}")
        if keywords:
            logger.info(f"张衡：模型圈定的检索关键词 {keywords}")
        return " ".join(keywords), picked_files

    # ── extract ────────────────────────────────

    async def extract(
        self,
        toc: list[DomainSection],
        *,
        scoring_contexts: Optional[dict[str, str]] = None,
        evaluation_contexts: Optional[dict[str, str]] = None,
        on_section: Optional[
            Callable[[DomainSection, "OutlineMatrixRow"], Optional[Awaitable[None]]]
        ] = None,
        on_section_start: Optional[
            Callable[[DomainSection], Optional[Awaitable[None]]]
        ] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> dict[str, OutlineMatrixRow]:
        """对 toc 中每个 section 做 8 字段提炼。

        scoring_contexts / evaluation_contexts 是按 block_id 索引的可选上下文
        （来自评分表 / 评审要素，由 routes 注入）。

        on_section: 每完成一章节立即回调一次，签名 (section, row) → None | awaitable[None]。
        on_section_start: 章节真正开始处理时（拿到信号量、调 LLM 前）回调一次，
                         前端用它把卡片切到"提炼中"状态，避免并发等待期看不到反馈。

        should_cancel: 逐节取消探测（runner 的 _Run.cancel_flag）。为真时**停止后续章节**，
        已完成的照常返回 —— 用于用户点「取消」时不再把剩余章节的模型调用烧掉。

        失败的章节：行的 8 字段保持默认空，error 字段写错误描述，不抛错（per spec §3）。

        返回：dict 的 key 是 block_id（与 Section.id 同），按 toc 顺序构建。
        """
        # 注意：函数内 import — 测试可通过 monkeypatch infra.llm.dispatch_outline_json 覆盖。
        import infra.llm

        async def _fire(section: DomainSection, row: OutlineMatrixRow) -> None:
            if on_section is None:
                return
            try:
                rv = on_section(section, row)
                if inspect.isawaitable(rv):
                    await rv
            except Exception as e:
                logger.warning(f"张衡：on_section 回调失败（block={section.id}）：{e}")

        async def _fire_start(section: DomainSection) -> None:
            if on_section_start is None:
                return
            try:
                rv = on_section_start(section)
                if inspect.isawaitable(rv):
                    await rv
            except Exception as e:
                logger.warning(f"张衡：on_section_start 回调失败（block={section.id}）：{e}")

        configs, rr_start = self._configs_provider()
        if not configs:
            logger.warning("张衡：未配置 LLM，extract 返回空字段 + error")
            matrix: dict[str, OutlineMatrixRow] = {}
            for s in toc:
                row = OutlineMatrixRow(
                    block_id=s.id,
                    title=s.title,
                    error="未配置 LLM",
                )
                matrix[s.id] = row
                await _fire(s, row)
            return matrix

        normalized_sections = 0
        # 构造 sections 列表交给 dispatch_outline_json
        sections_for_extract: list[dict[str, Any]] = []
        for s in toc:
            entry: dict[str, Any] = {
                "title": s.title,
                "content": s.raw_content,
                "special_marks": "".join(s.special_marks),
            }
            if scoring_contexts and s.id in scoring_contexts:
                entry["scoring_context"] = scoring_contexts[s.id]
            if evaluation_contexts and s.id in evaluation_contexts:
                entry["evaluation_context"] = evaluation_contexts[s.id]
            sections_for_extract.append(entry)

        # dispatch_outline_json 是 async generator，按完成顺序 yield；
        # 每条到手立即按 idx 回填到 toc + on_section，前端实时看到一条 outline_extract。
        async def _dispatch_on_start(idx: int) -> None:
            if 0 <= idx < len(toc):
                await _fire_start(toc[idx])

        # 测试用 monkeypatch 注入的 fake_dispatch 可能不接受 on_start —— 用签名探测降级。
        try:
            sig = inspect.signature(infra.llm.dispatch_outline_json)
            supports_on_start = "on_start" in sig.parameters
        except (TypeError, ValueError):
            supports_on_start = False

        dispatch_kwargs: dict[str, Any] = {}
        if supports_on_start:
            dispatch_kwargs["on_start"] = _dispatch_on_start
        # 逐节取消：dispatch 内部每节前检查一次（旧 stub 不接受该参数 → 签名探测）
        try:
            if "should_cancel" in inspect.signature(infra.llm.dispatch_outline_json).parameters:
                dispatch_kwargs["should_cancel"] = should_cancel
        except (TypeError, ValueError):
            pass
        gen = infra.llm.dispatch_outline_json(
            configs, rr_start, sections_for_extract, **dispatch_kwargs,
        )

        matrix: dict[str, OutlineMatrixRow] = {}
        seq_fallback_idx = 0
        async for item in gen:
            # 优先从 item 拿 idx（新 dispatch 按完成顺序 yield 时携带），
            # 兼容旧合约（无 idx 字段时按 toc 顺序匹配）。
            raw_idx = item.get("idx") if isinstance(item, dict) else None
            if raw_idx is None:
                idx = seq_fallback_idx
                seq_fallback_idx += 1
            else:
                try:
                    idx = int(raw_idx)
                except (TypeError, ValueError):
                    idx = seq_fallback_idx
                    seq_fallback_idx += 1
            if idx < 0 or idx >= len(toc):
                continue
            section = toc[idx]
            row = OutlineMatrixRow(
                block_id=section.id,
                title=section.title,
                requirement=str(item.get("requirement", "") or ""),
                key_points=str(item.get("key_points", "") or ""),
                veto_items=str(item.get("veto_items", "") or ""),
                bonus_items=str(item.get("bonus_items", "") or ""),
                score_items=str(item.get("score_items", "") or ""),
                evidence_required=str(item.get("evidence_required", "") or ""),
                constraint_level=str(
                    item.get("constraint_level", "recommended") or "recommended",
                ),
                indicators=str(item.get("indicators", "") or ""),
                error=str(item.get("error", "") or ""),
                error_kind=str(item.get("error_kind", "") or ""),
            )
            # 字段口径兜底：把"评分档位/得分规则"从 requirement 挪到 score_items。
            # 模型不保证听话，历史数据也不会因改 prompt 而变好；这一步是确定性的
            # （见 domain/spec/field_normalize.py 的说明）。
            moved = normalize_row_fields(row).get("moved") or []
            if moved:
                normalized_sections += 1
                logger.info(f"张衡：{section.id} 从 requirement 挪走 {len(moved)} 条评分口径 → score_items")
            matrix[section.id] = row
            await _fire(section, row)

        # toc 中存在但 results 缺少的章节兜底（防 dispatch bug）。
        # **取消时不补**：补出来的空行会以 status='outline_done' 落库（8 字段全空、
        # error=「提炼缺失」），前端按 status 判定就成了"已提炼" —— 取消后页面显示
        # 「提炼完成」而 8 字段是空的，用户根本判断不出到底跑没跑，也没法"继续提炼"
        # （skip_ids 要求 key_points/requirement 非空，空行本来不会被跳过，但会被算成已完成）。
        # 取消时让这些章节保持"未提炼"，继续提炼时重新排队。
        if not (should_cancel and should_cancel()):
            for section in toc:
                if section.id not in matrix:
                    row = OutlineMatrixRow(
                        block_id=section.id,
                        title=section.title,
                        error="提炼缺失",
                    )
                    matrix[section.id] = row
                    await _fire(section, row)

        if normalized_sections:
            logger.info(f"张衡：{normalized_sections} 个章节的评分口径已从 requirement 归位到 score_items")
        return matrix


__all__ = [
    "ZhangHengAgent",
    "SpecParseResult",
]

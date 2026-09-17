"""张衡 — 规范书解析专家 agent。

职责（spec §3）：
1. parse: 把规范书文件解析为 toc + 项目概述（doc_summary）
2. extract: 对每章节做 8 字段响应矩阵提炼（含 ★/▲ 标记的特殊处理）

不持有 LLM 配置；通过依赖注入接受 services.config_store.config_store 或自己的轻量 wrapper。
本期直接 import config_store 单例（route 层会注入相同实例）。
"""

import inspect
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, BinaryIO, Callable, Optional, Union

from domain.spec import OutlineMatrixRow
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

    degraded=True 表示未采用模型输出，``sections`` 是规范书原始目录（等价改造前
    的行为），``error`` 写明原因供前端展示。
    """
    sections: list[DomainSection]
    degraded: bool = False
    error: str = ""


# ─────────────────────────────────────────────
# Agent
# ─────────────────────────────────────────────

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

        docs = []
        for idx, (file_source, suffix, filename) in enumerate(sources, start=1):
            # parse_document 是同步且耗时（解析 docx/pdf）。直接 await 会阻塞事件循环，
            # 导致 SSE emitter 的 stream() 在解析期间一帧都吐不出。扔到后台线程跑。
            docs.append(await asyncio.to_thread(
                parse_document,
                file_source,
                suffix=suffix,
                filename=filename,
                progress_callback=_label_progress(
                    progress_callback, filename, idx, len(sources),
                ),
            ))

        # infra Section → domain Section。
        # 每份文档的 section id 都是 s1..sN，直接拼接会撞车，所以按拼接后的
        # 顺序重新编号 —— source_toc 的 id 只用于 grounding 的位置索引与占位行，
        # 重新编号不影响任何下游语义。
        toc: list[DomainSection] = []
        doc_titles: list[str] = []
        for doc in docs:
            if doc.title:
                doc_titles.append(doc.title)
            for s in doc.sections:
                toc.append(DomainSection(
                    id=f"s{len(toc) + 1}",
                    level=s.level,
                    title=s.title,
                    raw_content=s.raw_content,
                    special_marks=list(s.special_marks),
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

        return SpecParseResult(
            doc_id=docs[0].doc_id,
            doc_title="、".join(doc_titles),
            doc_summary=doc_summary,
            toc=toc,
            doc_summary_error=doc_summary_error,
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

        任何失败都不抛：降级为「沿用规范书原始目录」并把原因写进 ``error``，
        让用户能在闸门 1 看到并决定是否重出。
        """
        from agents.prompts import build_spec_digest

        if not source_toc:
            return OutlineDraftResult(
                sections=[], degraded=True, error="要求文件目录为空，无法派生应答目录",
            )

        def _fallback(reason: str) -> OutlineDraftResult:
            logger.warning(f"张衡：目录派生降级（{reason}），沿用要求文件原始目录")
            return OutlineDraftResult(
                sections=[DomainSection(**s.to_dict()) for s in source_toc],
                degraded=True,
                error=reason,
            )

        configs, rr_start = self._configs_provider()
        if not configs:
            return _fallback("未配置模型，沿用要求文件原目录")

        # 先按提炼要求定位「用文件的哪一部分」，再拿这部分去派生。
        # 整份文件灌进去时，招标公告/资格要求/合同范本会把真正要的章节挤出预算。
        flat = [
            {"title": s.title, "content": s.raw_content, "level": s.level}
            for s in source_toc
        ]
        digest_sections, follow_structure = await self._locate_for_digest(
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
            return _fallback(f"模型调用失败：{e}")

        nodes, warnings = normalize_nodes(raw.get("nodes"))
        sections = build_sections(nodes)

        if not sections:
            return _fallback("模型输出无法解析出目录节点")
        if count_top_level(sections) < MIN_TOP_LEVEL_NODES:
            return _fallback(
                f"目录结构不完整（仅 {count_top_level(sections)} 个一级章节）"
            )

        _ground_sections(sections, source_toc)

        return OutlineDraftResult(
            sections=sections,
            degraded=False,
            error="；".join(warnings),
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
        from infra.retrieval.segment import extract_scope_tokens, locate_segments

        segments = locate_segments(flat, instruction)

        # 只有「用户确实点了范围、却哪儿都认不出」才值得多花一次模型调用；
        # 要求里压根没有范围标识时（「按评分项逐条拆章」），模型也无从下手。
        if segments is None and extract_scope_tokens(instruction):
            keyword = await self._model_scope_keyword(
                instruction, [s["title"] for s in flat], configs, rr_start,
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
            )

        located = locate_sections(flat, instruction)
        return (
            [
                {"title": flat[i]["title"], "content": flat[i]["content"]}
                for i in located
            ],
            False,
        )

    async def _model_scope_keyword(
        self,
        instruction: str,
        titles: list[str],
        configs: list,
        rr_start: int,
    ) -> str:
        """兜底：让模型把要求里的说法换成文档里的字面标识。失败返回空串。"""
        import infra.llm

        try:
            obj = await infra.llm.dispatch_scope_select_json(configs, rr_start, {
                "instruction": instruction,
                # 文件名没传到这一层（SpecLoader 元组里才有），只给章节标题
                "file_names": [],
                "section_titles": titles,
            })
        except Exception as e:
            logger.warning(f"张衡：检索范围兜底失败，退回章级定位：{e}")
            return ""

        keywords = [
            str(k).strip()
            for k in (obj.get("keywords") or [])
            if isinstance(k, (str, int, float)) and str(k).strip()
        ]
        if not keywords:
            return ""
        logger.info(f"张衡：模型圈定的检索关键词 {keywords}")
        return " ".join(keywords)

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
    ) -> dict[str, OutlineMatrixRow]:
        """对 toc 中每个 section 做 8 字段提炼。

        scoring_contexts / evaluation_contexts 是按 block_id 索引的可选上下文
        （来自评分表 / 评审要素，由 routes 注入）。

        on_section: 每完成一章节立即回调一次，签名 (section, row) → None | awaitable[None]。
        on_section_start: 章节真正开始处理时（拿到信号量、调 LLM 前）回调一次，
                         前端用它把卡片切到"提炼中"状态，避免并发等待期看不到反馈。

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

        if supports_on_start:
            gen = infra.llm.dispatch_outline_json(
                configs, rr_start, sections_for_extract, on_start=_dispatch_on_start,
            )
        else:
            gen = infra.llm.dispatch_outline_json(
                configs, rr_start, sections_for_extract,
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
            )
            matrix[section.id] = row
            await _fire(section, row)

        # toc 中存在但 results 缺少的章节兜底（防 dispatch bug）
        for section in toc:
            if section.id not in matrix:
                row = OutlineMatrixRow(
                    block_id=section.id,
                    title=section.title,
                    error="提炼缺失",
                )
                matrix[section.id] = row
                await _fire(section, row)

        return matrix


__all__ = [
    "ZhangHengAgent",
    "SpecParseResult",
]

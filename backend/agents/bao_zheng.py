"""包拯 — 合规评审专家 agent。

职责（spec §3）：
对诸葛亮产出的每个 block 做合规视角评审：响应合规性、否决项是否覆盖、
加分项是否遗漏、公文证明材料是否齐备、量化指标逐条响应。逐 block 给
0-100 分 + issues + strengths。

失败处理：
- 单 block 全部 LLM config 失败 → Finding.error 写入失败原因，score=0，issues/strengths=[]
- 不抛错，让其它 block 继续评审
"""

import json
import logging
from typing import Any, Awaitable, Callable, Optional, Union

from agents.prompts import COMPLIANCE_REVIEW_SYSTEM, build_compliance_review_user
from domain.proposal import BlockOutput
from domain.review import Finding, Issue
from domain.spec import OutlineMatrixRow
from infra.llm import LLMConfig, OPENAI_COMPATIBLE_PROVIDERS
from infra.llm import errors as llm_errors
from infra.llm.clients import generate_oneshot_claude, generate_oneshot_openai
from infra.llm.json_utils import extract_json_object as _extract_json_object

logger = logging.getLogger(__name__)


# 事件回调签名：(event_type, payload) -> None | awaitable[None]
EventCallback = Callable[[str, dict], Union[None, Awaitable[None]]]

AGENT_NAME = "bao_zheng"


async def _emit(emitter: Optional[EventCallback], event_type: str, payload: dict) -> None:
    """统一 dispatch event callback；支持 sync 与 async 两种回调。"""
    if emitter is None:
        return
    result = emitter(event_type, payload)
    if hasattr(result, "__await__"):
        await result


# ─────────────────────────────────────────────
# Agent 类
# ─────────────────────────────────────────────

class BaoZhengAgent:
    """包拯：合规视角评审。"""

    SYSTEM_PROMPT = COMPLIANCE_REVIEW_SYSTEM
    AGENT_NAME = AGENT_NAME

    def __init__(
        self,
        configs_provider: Optional[Callable[[], tuple[list, int]]] = None,
    ):
        if configs_provider is None:
            from services.config_store import config_store
            self._configs_provider = config_store.get_configs_and_next_index
        else:
            self._configs_provider = configs_provider

    async def review(
        self,
        blocks: dict[str, BlockOutput],
        outline_matrix: dict[str, OutlineMatrixRow],
        emitter: Optional[EventCallback] = None,
        chain_by_block: Optional[dict[str, dict]] = None,
    ) -> dict[str, Finding]:
        """
        对 blocks 中每个 block 做合规评审，返回 {block_id: Finding}。

        ``chain_by_block``：链条数据 ``{block_id: {"criterion_text": …, "spec_requirements": [...]}}``。
        **必须收**：`orchestrator.nodes._run_review` 对技术/合规两个 agent 传同一套参数，
        少了这个形参就是 TypeError → 整个评审节点失败 → 评审页永远没有结论
        （实测：界面显示"本次运行失败：BaoZhengAgent.review() got an unexpected keyword
        argument 'chain_by_block'"，reviews 表 0 行）。

        遍历顺序：blocks 的字典插入顺序。
        单 block 全 API 失败：Finding(score=0, issues=[], strengths=[], error="...")。
        """
        configs, rr_start = self._configs_provider()
        if not configs:
            logger.warning("包拯：未配置 LLM，review 返回空")
            return {
                bid: Finding(
                    block_id=bid,
                    agent=self.AGENT_NAME,
                    score=0,
                    error="未配置 LLM",
                )
                for bid in blocks.keys()
            }

        results: dict[str, Finding] = {}

        for idx, (block_id, block_output) in enumerate(blocks.items()):
            row = outline_matrix.get(block_id)
            block_dict = self._block_to_dict(block_id, block_output)
            matrix_dict = self._row_to_dict(row)
            # build_compliance_review_user 用 block["title"]，title 由 outline_matrix 提供
            block_dict["title"] = matrix_dict.get("title", "")
            # 评分口径与规范要求：与技术评审同源，让合规评审也按"得分条件"判，而不是泛泛而谈
            chain = (chain_by_block or {}).get(block_id) or {}
            criterion = str(chain.get("criterion_text")
                            or matrix_dict.get("criterion_text") or "")
            spec_reqs = list(chain.get("spec_requirements") or [])
            user_prompt = self._build_user_prompt(
                block_dict, matrix_dict, criterion=criterion, spec_requirements=spec_reqs)

            await _emit(emitter, "review_block_start", {
                "block_id": block_id,
                "agent": self.AGENT_NAME,
            })

            finding = await self._review_one(
                block_id, user_prompt,
                configs, (rr_start + idx) % len(configs),
            )
            results[block_id] = finding

            await _emit(emitter, "review_block_done", {
                "block_id": block_id,
                "agent": self.AGENT_NAME,
                "score": finding.score,
                "issues": [i.to_dict() for i in finding.issues],
                "issues_count": len(finding.issues),
                "error": finding.error,
            })

        return results

    # ── 单 block 评审：轮询 + Fallback ───────────

    async def _review_one(
        self,
        block_id: str,
        user_prompt: str,
        configs: list[LLMConfig],
        rr_start: int,
    ) -> Finding:
        """对单个 block 调 LLM；按 rr_start 轮询可用 config，全失败时返 Finding(error=...)."""
        usable = llm_errors.available_configs(configs, rr_start)
        last_error: Optional[Exception] = None

        for i, config in enumerate(usable):
            try:
                if config.provider in OPENAI_COMPATIBLE_PROVIDERS:
                    raw = await generate_oneshot_openai(
                        config, self.SYSTEM_PROMPT, user_prompt, max_tokens=8000,
                        call_site="review_compliance",
                    )
                else:
                    raw = await generate_oneshot_claude(
                        config, self.SYSTEM_PROMPT, user_prompt, max_tokens=8000,
                        call_site="review_compliance",
                    )
                return self._parse_finding(block_id, raw)
            except Exception as e:
                last_error = e
                kind = llm_errors.note_failure(config, e)
                logger.warning(
                    f"{self.AGENT_NAME}: block {block_id} API "
                    f"[{config.provider}/{config.model}] 失败 "
                    f"({i + 1}/{len(usable)}，{llm_errors.describe_error(e)})：{e}"
                )
                if llm_errors.blocks_chain(kind):
                    break

        # 全失败兜底
        return Finding(
            block_id=block_id,
            agent=self.AGENT_NAME,
            score=0,
            error=f"全部 API 失败：{llm_errors.exhausted_summary(configs, last_error)}",
        )

    # ── 结果解析 ───────────────────────────────

    def _parse_finding(self, block_id: str, raw: str) -> Finding:
        """解析 LLM 返回的 JSON → Finding。解析失败也返回 Finding(error=...)。"""
        try:
            obj = _extract_json_object(raw)
        except Exception as e:
            return Finding(
                block_id=block_id,
                agent=self.AGENT_NAME,
                score=0,
                error=f"JSON 解析失败：{e}",
            )

        try:
            score = int(obj.get("score", 0))
        except (TypeError, ValueError):
            score = 0
        score = max(0, min(100, score))  # clamp 0-100

        issues_raw = obj.get("issues") or []
        issues: list[Issue] = []
        if isinstance(issues_raw, list):
            for item in issues_raw:
                if not isinstance(item, dict):
                    continue
                issues.append(Issue(
                    severity=str(item.get("severity", "medium")),
                    point=str(item.get("point", "")),
                    suggestion=str(item.get("suggestion", "")),
                    needs_material=bool(item.get("needs_material", False)),
                    material_query=str(item.get("material_query", "") or ""),
                ))

        strengths_raw = obj.get("strengths") or []
        strengths: list[str] = []
        if isinstance(strengths_raw, list):
            strengths = [str(s) for s in strengths_raw]

        return Finding(
            block_id=block_id,
            agent=self.AGENT_NAME,
            score=score,
            issues=issues,
            strengths=strengths,
            error="",
        )

    # ── 辅助：dataclass → dict ─────────────────

    @staticmethod
    def _build_user_prompt(block_dict: dict, matrix_dict: dict, *,
                           criterion: str = "", spec_requirements: list | None = None) -> str:
        return build_compliance_review_user(
            block_dict, matrix_dict, criterion=criterion, spec_requirements=spec_requirements)

    @staticmethod
    def _block_to_dict(block_id: str, output: BlockOutput) -> dict[str, Any]:
        return {
            "block_id": block_id,
            "title": "",  # title 由 outline_matrix 提供，外层会覆盖
            "content": output.content or "",
            "kind": output.kind or "tech",
        }

    @staticmethod
    def _row_to_dict(row: Optional[OutlineMatrixRow]) -> dict[str, Any]:
        if row is None:
            return {}
        return {
            "title": row.title or "",
            "requirement": row.requirement or "",
            "key_points": row.key_points or "",
            "veto_items": row.veto_items or "",
            "bonus_items": row.bonus_items or "",
            "score_items": row.score_items or "",
            "evidence_required": row.evidence_required or "",
            "indicators": row.indicators or "",
        }


__all__ = ["BaoZhengAgent", "AGENT_NAME"]

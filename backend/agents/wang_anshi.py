"""王安石 — 技术评审专家 agent。

职责（spec §3）：
对诸葛亮产出的每个 block 做技术视角评审：技术架构、可行性、实施风险、
专业术语准确度、技术指标响应。逐 block 给 0-100 分 + issues + strengths。

失败处理：
- 单 block 全部 LLM config 失败 → Finding.error 写入失败原因，score=0，issues/strengths=[]
- 不抛错，让其它 block 继续评审
"""

import json
import logging
from typing import Any, Awaitable, Callable, Optional, Union

from agents.prompts import TECH_REVIEW_SYSTEM, build_tech_review_user
from domain.proposal import BlockOutput
from domain.review import Finding, Issue
from domain.spec import OutlineMatrixRow
from infra.llm import LLMConfig, OPENAI_COMPATIBLE_PROVIDERS
from infra.llm.clients import generate_oneshot_claude, generate_oneshot_openai

logger = logging.getLogger(__name__)


# 事件回调签名：(event_type, payload) -> None | awaitable[None]
EventCallback = Callable[[str, dict], Union[None, Awaitable[None]]]

AGENT_NAME = "wang_anshi"


async def _emit(emitter: Optional[EventCallback], event_type: str, payload: dict) -> None:
    """统一 dispatch event callback；支持 sync 与 async 两种回调。"""
    if emitter is None:
        return
    result = emitter(event_type, payload)
    if hasattr(result, "__await__"):
        await result


# ─────────────────────────────────────────────
# JSON 抽取（与 dispatcher / rerank 同思路，本期就近内联）
# ─────────────────────────────────────────────

def _extract_json_object(text: str) -> dict:
    """从模型返回里抽出第一个完整 JSON 对象，剥代码围栏。失败抛 ValueError。"""
    s = text.strip()
    if s.startswith("```"):
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1:]
        if s.endswith("```"):
            s = s[:-3]
        s = s.strip()

    start = s.find("{")
    if start == -1:
        raise ValueError(f"未找到 JSON 起始 {{：{text[:120]}")

    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(s[start:i + 1])
    raise ValueError(f"JSON 对象未闭合：{text[:120]}")


# ─────────────────────────────────────────────
# Agent 类
# ─────────────────────────────────────────────

class WangAnshiAgent:
    """王安石：技术视角评审。"""

    SYSTEM_PROMPT = TECH_REVIEW_SYSTEM
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
    ) -> dict[str, Finding]:
        """
        对 blocks 中每个 block 做技术评审，返回 {block_id: Finding}。

        遍历顺序：blocks 的字典插入顺序。
        单 block 全 API 失败：Finding(score=0, issues=[], strengths=[], error="...")。
        """
        configs, rr_start = self._configs_provider()
        if not configs:
            logger.warning("王安石：未配置 LLM，review 返回空")
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
            # build_tech_review_user 用 block["title"]，title 由 outline_matrix 提供
            block_dict["title"] = matrix_dict.get("title", "")
            user_prompt = self._build_user_prompt(block_dict, matrix_dict)

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
        """对单个 block 调 LLM；按 rr_start 轮询全部 config，全失败时返 Finding(error=...)."""
        n = len(configs)
        last_error: Optional[Exception] = None

        for i in range(n):
            config = configs[(rr_start + i) % n]
            try:
                if config.provider in OPENAI_COMPATIBLE_PROVIDERS:
                    raw = await generate_oneshot_openai(
                        config, self.SYSTEM_PROMPT, user_prompt, max_tokens=2000,
                    )
                else:
                    raw = await generate_oneshot_claude(
                        config, self.SYSTEM_PROMPT, user_prompt, max_tokens=2000,
                    )
                return self._parse_finding(block_id, raw)
            except Exception as e:
                last_error = e
                logger.warning(
                    f"{self.AGENT_NAME}: block {block_id} API "
                    f"[{config.provider}/{config.model}] 失败 ({i + 1}/{n})：{e}"
                )

        # 全失败兜底
        return Finding(
            block_id=block_id,
            agent=self.AGENT_NAME,
            score=0,
            error=f"全部 API 失败：{last_error}" if last_error else "评审失败",
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
    def _build_user_prompt(block_dict: dict, matrix_dict: dict) -> str:
        return build_tech_review_user(block_dict, matrix_dict)

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


__all__ = ["WangAnshiAgent", "AGENT_NAME"]

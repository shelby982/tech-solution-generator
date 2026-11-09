"""llm_usage 表的落库 sink。

装配方式（见 main.py 启动钩子）：

    from services import usage_store
    usage_store.install()

之后 ``infra.llm.clients`` 每次真实调用都会经 ``infra.llm.usage.emit`` 把用量写进
llm_usage 表。未装配时（测试、脚本）不落库。

token 列写 NULL 而不是 0：provider 没返回该字段和「真的是 0」是两件事，
NULL 才能在汇总时区分（见 model_id 定价缺失那次教训 —— 记成 0 会被误读为免费）。

project_id / thread_id 用于把费用归集到「哪次运行」；未在 run_context 内发生的
调用（脚本、测试直调）写 NULL，而不是 0/空串 —— 同 NULL/0 的区分原则。
"""

import logging

import db as _db

from infra.llm import usage

logger = logging.getLogger(__name__)

_INSERT = """
INSERT INTO llm_usage (
    call_site, provider, model, streaming,
    input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
    reasoning_tokens, total_tokens,
    project_id, thread_id,
    ok, error, latency_ms,
    block_id, section, role
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


async def record(rec: dict) -> None:
    """把一条用量记录写进 llm_usage（自动带上当前章节/节/角色）。"""
    try:
        from infra.llm.usage import current_call
        ctx = current_call() or {}
        for key in ("block_id", "section", "role"):
            if ctx.get(key) not in (None, "", 0) and not rec.get(key):
                rec[key] = ctx[key]
    except Exception:
        pass
    async with _db.get_db() as conn:
        await conn.execute(
            _INSERT,
            (
                rec.get("call_site") or "",
                rec.get("provider") or "",
                rec.get("model") or "",
                1 if rec.get("streaming") else 0,
                rec.get("input_tokens"),
                rec.get("output_tokens"),
                rec.get("cache_read_tokens"),
                rec.get("cache_creation_tokens"),
                rec.get("reasoning_tokens"),
                rec.get("total_tokens"),
                rec.get("project_id"),
                rec.get("thread_id"),
                1 if rec.get("ok", True) else 0,
                rec.get("error") or "",
                rec.get("latency_ms"),
                rec.get("block_id"),
                rec.get("section") or "",
                rec.get("role") or "",
            ),
        )
        await conn.commit()


def install() -> None:
    """装配落库 sink。幂等。"""
    usage.set_sink(record)
    logger.info("LLM 用量记账已启用（llm_usage 表）")

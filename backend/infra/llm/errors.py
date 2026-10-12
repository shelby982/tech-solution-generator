"""Provider 调用错误分类与「坏配置」冷却。

实测背景（2026-09-20 费用分析）：一次运行里 3 条配置全部返回
``402 Insufficient Balance``，而各处重试循环对**任何**异常都无差别换下一条配置 ——
一个已经不可用的配置会被后面每一个小节反复重试（208 小节 × 3 条 = 624 次无谓尝试），
既拖慢又掩盖真实原因（日志里只看到「提炼失败」，看不到「余额不足」）。

这里做两件事：

1. **分类**：把异常归入三类之一 —— 「该配置不可用」「请求本身有问题」「可重试」；
2. **冷却**：不可用的配置进入冷却期（默认 300 秒，``LLM_UNHEALTHY_TTL_SECONDS``
   可调），后续调用直接跳过；全部处于冷却时**快速失败**，并给出可执行的聚合原因
   （「2 条余额不足、1 条 Key 无效」）。

刻意**不**做「遇到 402 就整链立即抛」：多 key 场景下换下一条配置正是应该做的事，
真正的问题是**坏配置被反复重试**。另外 400/422（例如长文档超出某模型上下文窗）
换一条配置仍可能成功，因此保留跨配置重试。两者都不改变「失败后降级」的既有语义。
"""

import logging
import os
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 分类
# ─────────────────────────────────────────────

# 该配置不可用：换别的配置是对的，但这条要拉黑，别再反复试
KIND_CONFIG_UNUSABLE = "config_unusable"
# 请求本身有问题（400/422）：换配置多半无用，但长文档超窗时换模型能救回来 → 仍重试
KIND_REQUEST_ERROR = "request_error"
# 瞬态故障：超时 / 限流 / 5xx / 连接失败 / 未知 → 换下一条配置重试
KIND_RETRYABLE = "retryable"

_KIND_BY_STATUS = {
    400: KIND_REQUEST_ERROR,
    401: KIND_CONFIG_UNUSABLE,
    402: KIND_CONFIG_UNUSABLE,
    403: KIND_CONFIG_UNUSABLE,
    404: KIND_CONFIG_UNUSABLE,   # 模型不存在/路径不对 → 这条配置修不好
    408: KIND_RETRYABLE,
    422: KIND_REQUEST_ERROR,
    429: KIND_RETRYABLE,
}

# 没有 status_code 时按文本兜底（SDK 不统一、有时被包了一层）
_TEXT_HINTS: tuple[tuple[str, str], ...] = (
    ("insufficient balance", KIND_CONFIG_UNUSABLE),
    ("insufficient_quota", KIND_CONFIG_UNUSABLE),
    ("invalid api key", KIND_CONFIG_UNUSABLE),
    ("incorrect api key", KIND_CONFIG_UNUSABLE),
    ("invalid_api_key", KIND_CONFIG_UNUSABLE),
    ("authentication", KIND_CONFIG_UNUSABLE),
    ("permission denied", KIND_CONFIG_UNUSABLE),
    ("model not found", KIND_CONFIG_UNUSABLE),
    ("model_not_found", KIND_CONFIG_UNUSABLE),
    ("does not exist", KIND_CONFIG_UNUSABLE),
    ("rate limit", KIND_RETRYABLE),
    ("too many requests", KIND_RETRYABLE),
    ("timeout", KIND_RETRYABLE),
    ("timed out", KIND_RETRYABLE),
    ("connection", KIND_RETRYABLE),
    ("overloaded", KIND_RETRYABLE),
)

_STATUS_RE = re.compile(r"error code:\s*(\d{3})", re.IGNORECASE)


def status_code_of(exc: BaseException) -> Optional[int]:
    """尽力取 HTTP 状态码：先看 SDK 属性，再退回解析错误文本。"""
    for attr in ("status_code", "status", "http_status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    match = _STATUS_RE.search(str(exc))
    if match is not None:
        return int(match.group(1))
    return None


def classify_error(exc: BaseException) -> str:
    """把一次调用异常归入 KIND_* 之一。无法判断时按可重试处理（保守）。"""
    status = status_code_of(exc)
    if status is not None:
        kind = _KIND_BY_STATUS.get(status)
        if kind is not None:
            return kind
        return KIND_RETRYABLE if status >= 500 else KIND_REQUEST_ERROR

    text = str(exc).lower()
    for needle, kind in _TEXT_HINTS:
        if needle in text:
            return kind
    return KIND_RETRYABLE


def describe_error(exc: BaseException) -> str:
    """给用户看的短原因，用于聚合提示与冷却记录（不含凭据）。"""
    status = status_code_of(exc)
    text = str(exc).lower()
    if status == 402 or "insufficient balance" in text or "insufficient_quota" in text:
        return "余额不足（402）"
    if status in (401, 403) or "invalid api key" in text or "authentication" in text \
            or "permission denied" in text:
        return "Key 无效或被拒绝（401/403）"
    if status == 404 or "model not found" in text or "does not exist" in text:
        return "模型不存在（404）"
    if status == 429 or "rate limit" in text or "too many requests" in text:
        return "触发限流（429）"
    if "timeout" in text or "timed out" in text:
        return "请求超时"
    if status is not None:
        return f"HTTP {status}"
    return "未知错误"


def is_retryable(kind: str) -> bool:
    """是否值得换下一条配置重试。配置不可用也算「值得换」（换的是别的配置）。"""
    return kind != KIND_REQUEST_ERROR


def blocks_chain(kind: str) -> bool:
    """该错误是否意味着「换别的配置也没用」，应当中止本条调用链。

    只有请求侧问题（400/422）属于此类；配置不可用不算 —— 换下一条配置才对。
    """
    return kind == KIND_REQUEST_ERROR


# ─────────────────────────────────────────────
# 配置冷却
# ─────────────────────────────────────────────

_DEFAULT_TTL_SECONDS = 300.0


def _ttl_seconds() -> float:
    """冷却时长。每次读取，便于测试与运行期调整环境变量。"""
    raw = os.environ.get("LLM_UNHEALTHY_TTL_SECONDS")
    if raw is None:
        return _DEFAULT_TTL_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_TTL_SECONDS
    return value if value > 0 else _DEFAULT_TTL_SECONDS


# config.id -> (过期时刻, 原因)
_unhealthy: dict[str, tuple[float, str]] = {}


def _cid(config) -> str:
    return str(getattr(config, "id", "") or "")


def is_available(config) -> bool:
    """该配置当前是否可用（不在冷却期内）。"""
    cid = _cid(config)
    if not cid:
        return True
    rec = _unhealthy.get(cid)
    if rec is None:
        return True
    if time.monotonic() >= rec[0]:
        _unhealthy.pop(cid, None)
        return True
    return False


def mark_unusable(config, reason: str) -> None:
    """把配置打入冷却。重复标记只刷新过期时刻，不重复打日志。"""
    cid = _cid(config)
    if not cid:
        return
    first = cid not in _unhealthy
    _unhealthy[cid] = (time.monotonic() + _ttl_seconds(), reason)
    if first:
        logger.warning(
            "配置 [%s/%s] 进入冷却 %.0fs：%s（后续调用将跳过它）",
            getattr(config, "provider", "?"), getattr(config, "model", "?"),
            _ttl_seconds(), reason,
        )


def note_failure(config, exc: BaseException) -> str:
    """记录一次失败：分类 + 必要时拉黑。返回分类结果供调用方决定是否继续。"""
    kind = classify_error(exc)
    if kind == KIND_CONFIG_UNUSABLE:
        mark_unusable(config, describe_error(exc))
    return kind


def available_configs(configs: list, rr_start_index: int = 0) -> list:
    """按原有轮询顺序返回当前可用的配置（跳过冷却中的）。

    全部处于冷却时返回 ``[]`` —— 调用方据此走「快速失败」路径，而不是把每条
    坏配置再试一遍。轮询语义与原来的 ``configs[(rr_start + i) % n]`` 一致，
    只是在过滤后的健康集合上取模。
    """
    usable = [c for c in configs if is_available(c)]
    n = len(usable)
    if n == 0:
        return []
    start = int(rr_start_index) % n
    return [usable[(start + i) % n] for i in range(n)]


def unavailable_counts(configs: list) -> dict[str, int]:
    """{原因: 条数}，用于聚合提示。"""
    counts: dict[str, int] = {}
    for c in configs:
        rec = _unhealthy.get(_cid(c))
        if rec is None:
            continue
        counts[rec[1]] = counts.get(rec[1], 0) + 1
    return counts


class AllConfigsUnavailableError(RuntimeError):
    """所有配置都在冷却中（或均被判定不可用）。"""


def exhausted_summary(configs: list, last_error: Optional[BaseException] = None) -> str:
    """「全部失败」时给用户/日志看的一句话，尽量可执行。"""
    counts = unavailable_counts(configs)
    parts: list[str] = []
    if counts:
        detail = "、".join(
            f"{n} 条{reason}" for reason, n in sorted(counts.items(), key=lambda kv: -kv[1])
        )
        parts.append(f"可用配置 {len(configs)} 条中 {sum(counts.values())} 条不可用（{detail}）")
    if last_error is not None:
        parts.append(f"最后一次错误：{type(last_error).__name__}: {str(last_error)[:150]}")
    if not parts:
        return f"全部 {len(configs)} 条配置均尝试失败"
    return "；".join(parts)


def exhausted_error(configs: list, last_error: Optional[BaseException] = None) -> BaseException:
    """给 ``raise`` 用的异常：有原始异常就原样返回（保留类型），否则构造聚合异常。"""
    if last_error is not None:
        return last_error
    return AllConfigsUnavailableError(exhausted_summary(configs))


def reset_health() -> None:
    """清空冷却状态（测试用）。"""
    _unhealthy.clear()


def unhealthy_snapshot() -> dict[str, tuple[float, str]]:
    """当前冷却快照（测试/诊断用）。"""
    return dict(_unhealthy)


__all__ = [
    "KIND_CONFIG_UNUSABLE",
    "KIND_REQUEST_ERROR",
    "KIND_RETRYABLE",
    "AllConfigsUnavailableError",
    "available_configs",
    "blocks_chain",
    "classify_error",
    "describe_error",
    "exhausted_error",
    "exhausted_summary",
    "is_available",
    "is_retryable",
    "mark_unusable",
    "note_failure",
    "reset_health",
    "status_code_of",
    "unavailable_counts",
    "unhealthy_snapshot",
]

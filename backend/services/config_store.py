"""
API Key 配置内存存储 + 文件持久化
支持多个 API 配置，轮询使用，失败自动 fallback。
配置保存在进程内存中，同时持久化到本地 JSON 文件；
服务重启后自动从文件加载，无需重新配置。
"""

import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 配置文件路径：
#   优先读取环境变量 CONFIG_PATH（适合 Docker + 持久化卷）
#   否则存放在项目根目录 tech-solution-generator/ 下
_config_dir = Path(os.environ.get("CONFIG_PATH", Path(__file__).parent.parent.parent))
CONFIG_FILE = _config_dir / ".api_config.json"


@dataclass
class LLMConfig:
    """LLM 提供商配置"""
    provider: str           # "openai" | "claude" | "doubao" | "kimi" | "minimax" | "deepseek"
    api_key: str
    base_url: str
    model: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    verified: bool = False  # 是否已通过验证

    def masked_key(self) -> str:
        """返回脱敏后的 API Key（仅显示首尾各4位）"""
        if len(self.api_key) <= 8:
            return "****"
        return f"{self.api_key[:4]}{'*' * (len(self.api_key) - 8)}{self.api_key[-4:]}"

    def to_safe_dict(self) -> dict:
        """返回不含敏感信息的配置字典"""
        return {
            "id": self.id,
            "provider": self.provider,
            "api_key_masked": self.masked_key(),
            "base_url": self.base_url,
            "model": self.model,
            "verified": self.verified,
        }

    def to_persist_dict(self) -> dict:
        """返回用于持久化的完整配置字典（含 API Key）"""
        return {
            "id": self.id,
            "provider": self.provider,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "model": self.model,
        }


# Provider 目录（**前后端唯一来源**）：前端通过 GET /api/model-defaults 读取。
# 这里既定义默认 base_url/model，也定义新建配置弹窗的预填文案；前端不再硬编码任何模型值。
# ui=False 的 provider 不出现在弹窗里（typesafe 只服务判断型调用，不走生成协议）。
PROVIDER_CATALOG = {
    "claude": {
        "name": "Anthropic Claude",
        "base_url": "https://api.anthropic.com",
        "model": "claude-3-5-sonnet-20241022",
        "placeholder": "sk-ant-…",
    },
    "openai": {
        "name": "OpenAI（含兼容接口）",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
        "placeholder": "sk-…",
    },
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "placeholder": "sk-…",
    },
    "kimi": {
        "name": "Kimi（月之暗面）",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-32k",
        "placeholder": "sk-…",
    },
    "minimax": {
        "name": "MiniMax",
        "base_url": "https://api.minimax.chat/v1",
        "model": "MiniMax-M2.7",
        "placeholder": "",
    },
    "doubao": {
        "name": "豆包（火山引擎）",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-pro-32k",
        "placeholder": "",
    },
    "typesafe": {
        "name": "TypeSafe（判断型）",
        "base_url": "https://api.typesafe.ai/v1",
        "model": "jev-latest",
        "placeholder": "",
        "ui": False,
    },
}

# 新建配置弹窗的默认 provider 与模型下拉档位（也由后端下发）
DEFAULT_PROVIDER = "claude"
MODEL_OPTIONS = [
    {"model": "claude-opus-4-8", "label": "Opus 4.8"},
    {"model": "claude-sonnet-4-6", "label": "Sonnet 4.6"},
    {"model": "claude-haiku-4-5-20251001", "label": "Haiku 4.5"},
]

# 兼容旧代码的派生映射（不要单独维护，改 PROVIDER_CATALOG 即可）
DEFAULT_BASE_URLS = {pid: meta["base_url"] for pid, meta in PROVIDER_CATALOG.items()}
DEFAULT_MODELS = {pid: meta["model"] for pid, meta in PROVIDER_CATALOG.items()}

# OpenAI 兼容的 provider 集合（共用 openai SDK 路径）
OPENAI_COMPATIBLE_PROVIDERS = {"openai", "doubao", "kimi", "minimax", "deepseek"}

# 判断型 provider（TypeSafe System One：POST /systemone 协议，不是 chat/completions）。
# 只服务素材重排 / 收敛校准等判断场景，**不参与生成链路的轮询**——生成侧若拿到
# 这种配置会按 claude 协议去调 systemone 端点，白白烧一次失败 fallback。
JUDGMENT_PROVIDERS = {"typesafe"}


class ConfigStore:
    """
    线程安全的多 API 配置存储单例，同时持久化到本地 JSON 文件。
    启动时自动从文件加载已保存的配置。
    支持轮询（round-robin）调度，故障时自动 fallback。
    """

    def __init__(self):
        self._configs: list[LLMConfig] = []
        self._rr_index: int = 0          # 轮询游标
        self._lock = threading.Lock()
        self._load_from_file()

    # ── 多配置接口 ────────────────────────────

    def get_all(self) -> list[LLMConfig]:
        """返回所有配置的副本列表"""
        with self._lock:
            return list(self._configs)

    def save_all(self, configs: list[LLMConfig]) -> None:
        """全量覆盖写入配置列表"""
        with self._lock:
            self._configs = list(configs)
            self._rr_index = 0
            self._persist()

    def add(self, config: LLMConfig) -> None:
        """追加一条新配置到列表末尾"""
        with self._lock:
            self._configs.append(config)
            self._persist()

    def remove(self, config_id: str) -> bool:
        """按 id 删除配置，返回是否删除成功"""
        with self._lock:
            before = len(self._configs)
            self._configs = [c for c in self._configs if c.id != config_id]
            removed = len(self._configs) < before
            if removed:
                self._rr_index = 0
                self._persist()
            return removed

    def get_by_id(self, config_id: str) -> Optional[LLMConfig]:
        """按 id 获取单条配置"""
        with self._lock:
            for c in self._configs:
                if c.id == config_id:
                    return c
            return None

    def get_configs_and_next_index(self) -> tuple[list["LLMConfig"], int]:
        """在同一锁内原子地返回配置列表副本和当前轮询起始索引，避免两次调用之间的 race condition

        优先返回 verified=True 的配置；若无任何 verified 项则 fallback 到全部
        （避免启动期未跑过 verify 时全部不可用）。
        判断型 provider（JUDGMENT_PROVIDERS）一律排除——它们不走生成协议，
        混进轮询只会白白烧失败 fallback。全部被排除时返回 ([], 0)，
        生成侧按"无 LLM 配置"降级（仅判断型配置本来也无法生成）。
        """
        with self._lock:
            if not self._configs:
                return [], 0
            verified = [c for c in self._configs if c.verified]
            pool = verified if verified else list(self._configs)
            pool = [c for c in pool if c.provider not in JUDGMENT_PROVIDERS]
            if not pool:
                return [], 0
            idx = self._rr_index % len(pool)
            self._rr_index = (self._rr_index + 1) % len(pool)
            return pool, idx

    async def verify_all(self) -> dict[str, bool]:
        """启动期对所有 config 跑一次 verify_api_key；
        把通过的标 verified=True，不通过的标 False 并打印日志。
        返回 {config_id: ok} 字典。
        """
        from infra.llm import verify_api_key

        results: dict[str, bool] = {}
        with self._lock:
            configs_snapshot = list(self._configs)
        for cfg in configs_snapshot:
            if cfg.provider in JUDGMENT_PROVIDERS:
                # 判断型 provider 不走 chat 协议，对话式 verify 必然失败——
                # 跳过并如实记 False（生成轮询本来就会排除它）。
                # 专属 verify 待 typesafe_client 落地时补。
                results[cfg.id] = False
                logger.info(
                    "config verify skipped (judgment provider): %s/%s",
                    cfg.provider, cfg.model,
                )
                continue
            exc_raised = False
            try:
                ok, msg = await verify_api_key(cfg)
            except Exception as e:
                ok, msg = False, f"verify exception: {e}"
                exc_raised = True
            with self._lock:
                for c in self._configs:
                    if c.id == cfg.id:
                        c.verified = ok
                        break
            results[cfg.id] = ok
            if ok:
                logger.info("config verify ok: %s/%s", cfg.provider, cfg.model)
            elif exc_raised:
                # 真异常时加 exc_info；正常 verify 返回 False 仍走普通 warning
                logger.warning(
                    "config verify FAIL (exception): %s/%s — %s",
                    cfg.provider, cfg.model, msg,
                    exc_info=True,
                )
            else:
                logger.warning(
                    "config verify FAIL: %s/%s — %s", cfg.provider, cfg.model, msg
                )
        return results

    def mark_verified(self, config_id: Optional[str] = None) -> None:
        """标记指定 id（或第一个）配置为已验证"""
        with self._lock:
            for c in self._configs:
                if config_id is None or c.id == config_id:
                    c.verified = True
                    if config_id is not None:
                        break

    def is_configured(self) -> bool:
        with self._lock:
            return len(self._configs) > 0

    # ── 文件 I/O ─────────────────────────────

    def _persist(self) -> None:
        """将当前配置列表写入 JSON 文件（调用方已持有锁）"""
        try:
            data = [c.to_persist_dict() for c in self._configs]
            CONFIG_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            CONFIG_FILE.chmod(0o600)
            logger.debug(f"配置已持久化到 {CONFIG_FILE}，共 {len(data)} 条")
        except Exception as e:
            logger.warning(f"配置文件写入失败（配置仍在内存中）：{e}")

    def _load_from_file(self) -> None:
        """启动时从 JSON 文件加载配置（在 __init__ 中调用，无需锁）"""
        try:
            if not CONFIG_FILE.exists():
                return
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))

            # 向后兼容：旧格式为单个 dict，新格式为 list
            if isinstance(raw, dict):
                raw = [raw]

            required = {"provider", "api_key", "base_url", "model"}
            loaded = []
            for item in raw:
                if not required.issubset(item.keys()):
                    logger.warning(f"配置项字段不完整，跳过：{item}")
                    continue
                loaded.append(LLMConfig(
                    id=item.get("id", str(uuid.uuid4())),
                    provider=item["provider"],
                    api_key=item["api_key"],
                    base_url=item["base_url"],
                    model=item["model"],
                    verified=False,  # 重启后需重新验证
                ))

            self._configs = loaded
            logger.info(f"已从文件加载 {len(loaded)} 条 API 配置")
        except Exception as e:
            logger.warning(f"配置文件加载失败（将忽略）：{e}")


# 全局单例
config_store = ConfigStore()

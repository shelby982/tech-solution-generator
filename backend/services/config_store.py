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


# 默认 Base URL 映射
DEFAULT_BASE_URLS = {
    "openai":    "https://api.openai.com/v1",
    "claude":    "https://api.anthropic.com",
    "doubao":    "https://ark.cn-beijing.volces.com/api/v3",
    "kimi":      "https://api.moonshot.cn/v1",
    "minimax":   "https://api.minimax.chat/v1",
    "deepseek":  "https://api.deepseek.com/v1",
}

# 默认模型映射
DEFAULT_MODELS = {
    "openai":    "gpt-4o",
    "claude":    "claude-3-5-sonnet-20241022",
    "doubao":    "doubao-pro-32k",
    "kimi":      "moonshot-v1-32k",
    "minimax":   "MiniMax-M2.7",
    "deepseek":  "deepseek-chat",
}

# OpenAI 兼容的 provider 集合（共用 openai SDK 路径）
OPENAI_COMPATIBLE_PROVIDERS = {"openai", "doubao", "kimi", "minimax", "deepseek"}


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

    def update(self, config_id: str, updated: LLMConfig) -> bool:
        """更新指定 id 的配置，返回是否找到并更新"""
        with self._lock:
            for i, c in enumerate(self._configs):
                if c.id == config_id:
                    updated.id = config_id  # 确保 id 不变
                    self._configs[i] = updated
                    self._persist()
                    return True
            return False

    def reorder(self, ordered_ids: list[str]) -> bool:
        """按传入的 id 顺序重排配置列表，返回是否成功"""
        with self._lock:
            id_map = {c.id: c for c in self._configs}
            if set(ordered_ids) != set(id_map.keys()):
                return False
            self._configs = [id_map[cid] for cid in ordered_ids]
            self._rr_index = 0
            self._persist()
            return True

    def get_next_index(self) -> int:
        """原子递增轮询游标，返回本次调用应使用的起始索引"""
        with self._lock:
            if not self._configs:
                return 0
            idx = self._rr_index % len(self._configs)
            self._rr_index = (self._rr_index + 1) % len(self._configs)
            return idx

    def get_configs_and_next_index(self) -> tuple[list["LLMConfig"], int]:
        """在同一锁内原子地返回配置列表副本和当前轮询起始索引，避免两次调用之间的 race condition"""
        with self._lock:
            if not self._configs:
                return [], 0
            configs = list(self._configs)
            idx = self._rr_index % len(self._configs)
            self._rr_index = (self._rr_index + 1) % len(self._configs)
            return configs, idx

    def mark_verified(self, config_id: Optional[str] = None) -> None:
        """标记指定 id（或第一个）配置为已验证"""
        with self._lock:
            for c in self._configs:
                if config_id is None or c.id == config_id:
                    c.verified = True
                    if config_id is not None:
                        break

    # ── 旧接口兼容 ────────────────────────────

    def get(self) -> Optional[LLMConfig]:
        """返回第一个配置（兼容旧代码）"""
        with self._lock:
            return self._configs[0] if self._configs else None

    def save(self, config: LLMConfig) -> None:
        """清空列表，仅保留这一个配置（兼容旧代码）"""
        with self._lock:
            # 保留 id，若已有相同 provider+model 则复用
            if not config.id or config.id == "":
                config.id = str(uuid.uuid4())
            self._configs = [config]
            self._rr_index = 0
            self._persist()

    def clear(self) -> None:
        """清除所有配置（写入空列表到文件，比 unlink 更安全——避免删除失败后重启复活旧配置）"""
        with self._lock:
            self._configs = []
            self._rr_index = 0
            self._persist()

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

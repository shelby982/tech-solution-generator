"""
API Key 配置内存存储 + 文件持久化
Key 保存在进程内存中，同时持久化到本地 JSON 文件；
服务重启后自动从文件加载，无需重新配置。
"""

import json
import logging
import os
import threading
from dataclasses import dataclass
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
    provider: str           # "openai" | "claude" | "doubao" | "kimi"
    api_key: str
    base_url: str
    model: str
    verified: bool = False  # 是否已通过验证

    def masked_key(self) -> str:
        """返回脱敏后的 API Key（仅显示首尾各4位）"""
        if len(self.api_key) <= 8:
            return "****"
        return f"{self.api_key[:4]}{'*' * (len(self.api_key) - 8)}{self.api_key[-4:]}"

    def to_safe_dict(self) -> dict:
        """返回不含敏感信息的配置字典"""
        return {
            "provider": self.provider,
            "api_key_masked": self.masked_key(),
            "base_url": self.base_url,
            "model": self.model,
            "verified": self.verified,
        }


# 默认 Base URL 映射
DEFAULT_BASE_URLS = {
    "openai":  "https://api.openai.com/v1",
    "claude":  "https://api.anthropic.com",
    "doubao":  "https://ark.cn-beijing.volces.com/api/v3",
    "kimi":    "https://api.moonshot.cn/v1",
}

# 默认模型映射
DEFAULT_MODELS = {
    "openai":  "gpt-4o",
    "claude":  "claude-3-5-sonnet-20241022",
    "doubao":  "doubao-pro-32k",
    "kimi":    "moonshot-v1-32k",
}

# OpenAI 兼容的 provider 集合（共用 openai SDK 路径）
OPENAI_COMPATIBLE_PROVIDERS = {"openai", "doubao", "kimi"}


class ConfigStore:
    """
    线程安全的配置内存存储单例，同时持久化到本地 JSON 文件。
    启动时自动从文件加载已保存的配置。
    """

    def __init__(self):
        self._config: Optional[LLMConfig] = None
        self._lock = threading.Lock()
        self._load_from_file()  # 启动时自动加载

    # ── 公开接口 ──────────────────────────────

    def save(self, config: LLMConfig) -> None:
        with self._lock:
            self._config = config
            self._persist()

    def get(self) -> Optional[LLMConfig]:
        with self._lock:
            return self._config

    def clear(self) -> None:
        with self._lock:
            self._config = None
            try:
                CONFIG_FILE.unlink(missing_ok=True)
            except Exception as e:
                logger.warning(f"删除配置文件失败：{e}")

    def is_configured(self) -> bool:
        with self._lock:
            return self._config is not None

    def mark_verified(self) -> None:
        with self._lock:
            if self._config:
                self._config.verified = True

    # ── 文件 I/O（调用方需持有锁） ──────────

    def _persist(self) -> None:
        """将当前配置写入 JSON 文件（调用方已持有锁）"""
        if self._config is None:
            return
        try:
            data = {
                "provider": self._config.provider,
                "api_key":  self._config.api_key,
                "base_url": self._config.base_url,
                "model":    self._config.model,
            }
            CONFIG_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.debug(f"配置已持久化到 {CONFIG_FILE}")
        except Exception as e:
            logger.warning(f"配置文件写入失败（配置仍在内存中）：{e}")

    def _load_from_file(self) -> None:
        """启动时从 JSON 文件加载配置（在 __init__ 中调用，无需锁）"""
        try:
            if not CONFIG_FILE.exists():
                return
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            required = {"provider", "api_key", "base_url", "model"}
            if not required.issubset(data.keys()):
                logger.warning("配置文件字段不完整，跳过加载")
                return
            self._config = LLMConfig(
                provider=data["provider"],
                api_key=data["api_key"],
                base_url=data["base_url"],
                model=data["model"],
                verified=False,  # 重启后需重新验证
            )
            logger.info(
                f"已从文件加载 API 配置：provider={self._config.provider}，"
                f"model={self._config.model}"
            )
        except Exception as e:
            logger.warning(f"配置文件加载失败（将忽略）：{e}")


# 全局单例
config_store = ConfigStore()

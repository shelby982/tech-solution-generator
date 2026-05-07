"""
API Key 配置与验证路由

POST /api/config        — 保存配置（不自动验证）
GET  /api/config        — 查询当前配置（脱敏）
GET  /api/config/check  — 验证 Key 有效性
DELETE /api/config      — 清除配置
GET  /api/config/providers — 列出支持的提供商

特殊 api_key 值：
  "__KEEP__" — 保留已存储的 API Key，仅更新 provider / base_url / model
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from services.config_store import (
    LLMConfig,
    ConfigStore,
    DEFAULT_BASE_URLS,
    DEFAULT_MODELS,
    OPENAI_COMPATIBLE_PROVIDERS,
    config_store,
)
from services.llm import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter(tags=["config"])

_KEEP_KEY = "__KEEP__"


# ─────────────────────────────────────────────
# 请求 / 响应模型
# ─────────────────────────────────────────────

class ConfigRequest(BaseModel):
    provider: str = "openai"
    api_key: str
    base_url: Optional[str] = None
    model: Optional[str] = None

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        allowed = {"openai", "claude", "doubao", "kimi"}
        v = v.lower().strip()
        if v not in allowed:
            raise ValueError(f"provider 必须是 {allowed} 之一，收到：{v!r}")
        return v

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        v = v.strip()
        if v == _KEEP_KEY:
            return v   # 特殊值：保留已有密钥，跳过校验
        if not v:
            raise ValueError("api_key 不能为空")
        if len(v) < 8:
            raise ValueError("api_key 长度异常，请检查是否填写完整")
        return v


# ─────────────────────────────────────────────
# 路由
# ─────────────────────────────────────────────

@router.post("/config")
async def save_config(body: ConfigRequest):
    """
    保存 LLM API 配置。
    - api_key 传 "__KEEP__" 时，保留已存储的密钥，仅更新其他字段。
    - 配置自动持久化到本地文件，服务重启后无需重新填写。
    """
    # 处理 __KEEP__ 逻辑
    if body.api_key == _KEEP_KEY:
        existing = config_store.get()
        if existing is None:
            raise HTTPException(
                status_code=400,
                detail="尚未保存任何 API Key，请输入完整密钥",
            )
        api_key = existing.api_key
    else:
        api_key = body.api_key

    # 填充默认值
    base_url = (body.base_url or "").strip() or DEFAULT_BASE_URLS.get(body.provider, "")
    model    = (body.model    or "").strip() or DEFAULT_MODELS.get(body.provider, "")

    config = LLMConfig(
        provider=body.provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        verified=False,
    )
    config_store.save(config)

    logger.info(
        f"配置已保存：provider={config.provider}, "
        f"model={config.model}, base_url={config.base_url}"
    )

    return JSONResponse(content={
        "success": True,
        "message": "配置已保存并持久化，建议调用 /api/config/check 验证 Key 有效性",
        "config": config.to_safe_dict(),
    })


@router.get("/config")
async def get_config():
    """查询当前配置（API Key 脱敏显示）"""
    cfg = config_store.get()
    if cfg is None:
        return JSONResponse(content={
            "configured": False,
            "message": "尚未配置 API Key，请先调用 POST /api/config",
        })
    return JSONResponse(content={
        "configured": True,
        "config": cfg.to_safe_dict(),
    })


@router.get("/config/check")
async def check_config():
    """
    验证当前配置的 API Key 是否有效。
    发送极小 test prompt，不产生实质费用。
    """
    cfg = config_store.get()
    if cfg is None:
        raise HTTPException(
            status_code=400,
            detail="尚未配置 API Key，请先调用 POST /api/config",
        )

    logger.info(f"开始验证 API Key：provider={cfg.provider}, model={cfg.model}")
    success, message = await verify_api_key(cfg)

    if success:
        config_store.mark_verified()
        logger.info("API Key 验证通过")
        return JSONResponse(content={
            "valid": True,
            "message": message,
            "config": cfg.to_safe_dict(),
        })
    else:
        logger.warning(f"API Key 验证失败：{message}")
        return JSONResponse(
            status_code=400,
            content={
                "valid": False,
                "message": message,
                "config": cfg.to_safe_dict(),
            },
        )


@router.delete("/config")
async def clear_config():
    """清除内存及文件中的 API Key 配置"""
    config_store.clear()
    logger.info("配置已清除")
    return JSONResponse(content={
        "success": True,
        "message": "配置已清除",
    })


@router.get("/config/providers")
async def list_providers():
    """返回支持的 LLM 提供商及其默认配置"""
    return JSONResponse(content={
        "providers": [
            {
                "id": "openai",
                "name": "OpenAI（含兼容接口）",
                "default_base_url": DEFAULT_BASE_URLS["openai"],
                "default_model": DEFAULT_MODELS["openai"],
                "model_examples": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"],
            },
            {
                "id": "claude",
                "name": "Anthropic Claude",
                "default_base_url": DEFAULT_BASE_URLS["claude"],
                "default_model": DEFAULT_MODELS["claude"],
                "model_examples": [
                    "claude-opus-4-6",
                    "claude-sonnet-4-6",
                    "claude-haiku-4-5-20251001",
                ],
            },
            {
                "id": "doubao",
                "name": "豆包（火山引擎）",
                "default_base_url": DEFAULT_BASE_URLS["doubao"],
                "default_model": DEFAULT_MODELS["doubao"],
                "model_examples": [
                    "doubao-pro-4k",
                    "doubao-pro-32k",
                    "doubao-pro-128k",
                    "doubao-lite-4k",
                    "doubao-lite-32k",
                ],
            },
            {
                "id": "kimi",
                "name": "Kimi（月之暗面）",
                "default_base_url": DEFAULT_BASE_URLS["kimi"],
                "default_model": DEFAULT_MODELS["kimi"],
                "model_examples": [
                    "moonshot-v1-8k",
                    "moonshot-v1-32k",
                    "moonshot-v1-128k",
                ],
            },
        ]
    })

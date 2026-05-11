"""
API Key 配置与验证路由

单配置（旧接口，向后兼容）：
  POST /api/config        — 保存配置（不自动验证）
  GET  /api/config        — 查询当前配置（脱敏）
  GET  /api/config/check  — 验证 Key 有效性
  DELETE /api/config      — 清除所有配置
  GET  /api/config/providers — 列出支持的提供商

多配置（新接口）：
  GET    /api/configs                    — 返回全部配置列表（脱敏）
  POST   /api/configs                    — 新增一条配置
  PUT    /api/configs/{config_id}        — 更新指定配置
  DELETE /api/configs/{config_id}        — 删除指定配置
  POST   /api/configs/reorder            — 重排优先级
  GET    /api/configs/{config_id}/check  — 验证指定配置的 Key

特殊 api_key 值：
  "__KEEP__" — 保留已存储的 API Key，仅更新 provider / base_url / model
"""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from services.config_store import (
    LLMConfig,
    DEFAULT_BASE_URLS,
    DEFAULT_MODELS,
    OPENAI_COMPATIBLE_PROVIDERS,
    config_store,
)
from services.llm import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter(tags=["config"])

_KEEP_KEY = "__KEEP__"
_ALLOWED_PROVIDERS = {"openai", "claude", "doubao", "kimi", "minimax", "deepseek"}


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
        v = v.lower().strip()
        if v not in _ALLOWED_PROVIDERS:
            raise ValueError(f"provider 必须是 {_ALLOWED_PROVIDERS} 之一，收到：{v!r}")
        return v

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        v = v.strip()
        if v == _KEEP_KEY:
            return v
        if not v:
            raise ValueError("api_key 不能为空")
        if len(v) < 8:
            raise ValueError("api_key 长度异常，请检查是否填写完整")
        return v


class ReorderRequest(BaseModel):
    ordered_ids: list[str]


# ─────────────────────────────────────────────
# 内部工具函数
# ─────────────────────────────────────────────

def _build_config(provider: str, api_key: str, base_url: Optional[str], model: Optional[str], config_id: Optional[str] = None) -> LLMConfig:
    resolved_base_url = (base_url or "").strip() or DEFAULT_BASE_URLS.get(provider, "")
    resolved_model    = (model    or "").strip() or DEFAULT_MODELS.get(provider, "")
    return LLMConfig(
        id=config_id or str(uuid.uuid4()),
        provider=provider,
        api_key=api_key,
        base_url=resolved_base_url,
        model=resolved_model,
        verified=False,
    )


# ─────────────────────────────────────────────
# 旧接口（向后兼容）
# ─────────────────────────────────────────────

@router.post("/config")
async def save_config(body: ConfigRequest):
    """
    保存 LLM API 配置（单配置模式，向后兼容）。
    会清除已有的所有配置，仅保留此一条。
    """
    if body.api_key == _KEEP_KEY:
        existing = config_store.get()
        if existing is None:
            raise HTTPException(status_code=400, detail="尚未保存任何 API Key，请输入完整密钥")
        api_key = existing.api_key
        config_id = existing.id
    else:
        api_key = body.api_key
        config_id = None

    config = _build_config(body.provider, api_key, body.base_url, body.model, config_id)
    config_store.save(config)

    logger.info(f"配置已保存（单配置模式）：provider={config.provider}, model={config.model}")
    return JSONResponse(content={
        "success": True,
        "message": "配置已保存并持久化，建议调用 /api/config/check 验证 Key 有效性",
        "config": config.to_safe_dict(),
    })


@router.get("/config")
async def get_config():
    """查询第一条配置（API Key 脱敏显示，向后兼容）"""
    cfg = config_store.get()
    if cfg is None:
        return JSONResponse(content={"configured": False, "message": "尚未配置 API Key"})
    return JSONResponse(content={"configured": True, "config": cfg.to_safe_dict()})


@router.get("/config/check")
async def check_config():
    """验证第一条配置的 API Key 是否有效"""
    cfg = config_store.get()
    if cfg is None:
        raise HTTPException(status_code=400, detail="尚未配置 API Key，请先调用 POST /api/config")

    logger.info(f"开始验证 API Key：provider={cfg.provider}, model={cfg.model}")
    success, message = await verify_api_key(cfg)

    if success:
        config_store.mark_verified(cfg.id)
        logger.info("API Key 验证通过")
        return JSONResponse(content={"valid": True, "message": message, "config": cfg.to_safe_dict()})
    else:
        logger.warning(f"API Key 验证失败：{message}")
        return JSONResponse(status_code=400, content={"valid": False, "message": message, "config": cfg.to_safe_dict()})


@router.delete("/config")
async def clear_config():
    """清除所有 API Key 配置"""
    config_store.clear()
    logger.info("所有配置已清除")
    return JSONResponse(content={"success": True, "message": "配置已清除"})


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
            {
                "id": "minimax",
                "name": "MiniMax",
                "default_base_url": DEFAULT_BASE_URLS["minimax"],
                "default_model": DEFAULT_MODELS["minimax"],
                "model_examples": [
                    "MiniMax-Text-01",
                    "abab6.5s-chat",
                    "abab6.5g-chat",
                ],
            },
            {
                "id": "deepseek",
                "name": "DeepSeek",
                "default_base_url": DEFAULT_BASE_URLS["deepseek"],
                "default_model": DEFAULT_MODELS["deepseek"],
                "model_examples": [
                    "deepseek-chat",
                    "deepseek-reasoner",
                ],
            },
        ]
    })


# ─────────────────────────────────────────────
# 新接口：多配置 CRUD
# ─────────────────────────────────────────────

@router.get("/configs")
async def list_configs():
    """返回所有配置列表（脱敏）"""
    configs = config_store.get_all()
    return JSONResponse(content={
        "configs": [c.to_safe_dict() for c in configs],
        "total": len(configs),
    })


@router.post("/configs")
async def add_config(body: ConfigRequest):
    """新增一条 API 配置，追加到列表末尾"""
    if body.api_key == _KEEP_KEY:
        raise HTTPException(status_code=400, detail="新增配置时请输入完整的 API Key")
    config = _build_config(body.provider, body.api_key, body.base_url, body.model)
    config_store.add(config)
    logger.info(f"新增 API 配置：id={config.id}, provider={config.provider}, model={config.model}")
    return JSONResponse(content={
        "success": True,
        "message": "配置已添加",
        "config": config.to_safe_dict(),
    })


@router.post("/configs/reorder")
async def reorder_configs(body: ReorderRequest):
    """按传入的 id 顺序重排配置优先级（固定路径须在 /{config_id} 之前注册）"""
    success = config_store.reorder(body.ordered_ids)
    if not success:
        raise HTTPException(status_code=400, detail="id 列表与已有配置不匹配，请传入完整的 id 列表")
    logger.info(f"重排 API 配置优先级：{body.ordered_ids}")
    configs = config_store.get_all()
    return JSONResponse(content={
        "success": True,
        "message": "配置顺序已更新",
        "configs": [c.to_safe_dict() for c in configs],
    })


@router.put("/configs/{config_id}")
async def update_config(config_id: str, body: ConfigRequest):
    """更新指定 id 的配置"""
    existing = config_store.get_by_id(config_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"配置不存在：{config_id}")

    if body.api_key == _KEEP_KEY:
        api_key = existing.api_key
    else:
        api_key = body.api_key

    updated = _build_config(body.provider, api_key, body.base_url, body.model, config_id)
    config_store.update(config_id, updated)

    logger.info(f"更新 API 配置：id={config_id}, provider={updated.provider}, model={updated.model}")
    return JSONResponse(content={
        "success": True,
        "message": "配置已更新",
        "config": updated.to_safe_dict(),
    })


@router.delete("/configs/{config_id}")
async def delete_config(config_id: str):
    """删除指定 id 的配置"""
    removed = config_store.remove(config_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"配置不存在：{config_id}")
    logger.info(f"删除 API 配置：id={config_id}")
    return JSONResponse(content={"success": True, "message": "配置已删除"})


@router.get("/configs/{config_id}/check")
async def check_config_by_id(config_id: str):
    """验证指定 id 的 API Key 是否有效"""
    cfg = config_store.get_by_id(config_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"配置不存在：{config_id}")

    logger.info(f"验证 API Key：id={config_id}, provider={cfg.provider}, model={cfg.model}")
    success, message = await verify_api_key(cfg)

    if success:
        config_store.mark_verified(config_id)
        logger.info(f"API Key 验证通过：id={config_id}")
        return JSONResponse(content={"valid": True, "message": message, "config": cfg.to_safe_dict()})
    else:
        logger.warning(f"API Key 验证失败：id={config_id}，{message}")
        return JSONResponse(status_code=400, content={"valid": False, "message": message, "config": cfg.to_safe_dict()})

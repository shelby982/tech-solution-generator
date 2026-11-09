"""
API Key 配置与验证路由

多配置接口：
  GET    /api/configs                    — 返回全部配置列表（脱敏）
  POST   /api/configs                    — 新增一条配置
  DELETE /api/configs/{config_id}        — 删除指定配置
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
    DEFAULT_PROVIDER,
    MODEL_OPTIONS,
    PROVIDER_CATALOG,
    config_store,
)
from infra.llm import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter(tags=["config"])

_KEEP_KEY = "__KEEP__"
_ALLOWED_PROVIDERS = {pid for pid, meta in PROVIDER_CATALOG.items() if meta.get("ui") is not False}


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
# 新接口：多配置 CRUD
# ─────────────────────────────────────────────

@router.get("/model-defaults")
async def get_model_defaults():
    """新建配置弹窗的唯一默认值来源（前端启动时读取，不在前端硬编码模型值）。"""
    providers = [
        {
            "id": pid,
            "name": meta["name"],
            "default_base_url": meta["base_url"],
            "default_model": meta["model"],
            "placeholder": meta.get("placeholder", ""),
        }
        for pid, meta in PROVIDER_CATALOG.items()
        if meta.get("ui") is not False
    ]
    return JSONResponse(content={
        "default_provider": DEFAULT_PROVIDER,
        "providers": providers,
        "model_options": MODEL_OPTIONS,
    })


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

"""编写模型分工设置 API（v3 §6.3）：项目默认 + 章节覆盖。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from db import get_db
from services.writing_settings import (ROLE_DEFAULTS, ROLE_LABELS, available_models,
                                       block_settings, project_settings, resolve_model,
                                       save_settings)

router = APIRouter(tags=["writing-settings"])


class SettingsBody(BaseModel):
    settings: dict[str, str] = Field(default_factory=dict)
    reset: bool = Field(default=False)


def _validate(payload: dict[str, str]) -> None:
    models = set(available_models())
    bad = [f"{role}={model}" for role, model in payload.items()
           if role in ROLE_LABELS and model and model not in models]
    if bad:
        raise HTTPException(422, f"模型不可用：{', '.join(bad)}")


@router.get("/projects/{project_id}/writing-settings")
async def get_project_settings(project_id: int):
    async with get_db() as db:
        values = await project_settings(db, project_id)
    return {"scope": "project", "settings": values, "defaults": ROLE_DEFAULTS,
            "labels": ROLE_LABELS, "available": available_models()}


@router.put("/projects/{project_id}/writing-settings")
async def put_project_settings(project_id: int, body: SettingsBody):
    _validate(body.settings)
    async with get_db() as db:
        values = await save_settings(db, "project", str(project_id), body.settings, reset=body.reset)
    return {"scope": "project", "settings": values}


@router.get("/blocks/{block_id}/writing-settings")
async def get_block_settings(block_id: int):
    async with get_db() as db:
        values = await block_settings(db, block_id)
        row = await (await db.execute("SELECT project_id FROM blocks WHERE id=?", (block_id,))).fetchone()
        project_id = int(row[0]) if row else 0
        effective = {}
        for role in ROLE_LABELS:
            effective[role] = await resolve_model(db, project_id, block_id, role)
    return {"scope": "block", "settings": values, "effective": effective,
            "labels": ROLE_LABELS, "available": available_models()}


@router.put("/blocks/{block_id}/writing-settings")
async def put_block_settings(block_id: int, body: SettingsBody):
    _validate(body.settings)
    async with get_db() as db:
        values = await save_settings(db, "block", str(block_id), body.settings, reset=body.reset)
    return {"scope": "block", "settings": values}

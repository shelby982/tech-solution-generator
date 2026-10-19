"""编写规范（P0.5）：L0 全局纪律 + L1 模块配方，可编辑、多版本、可回滚。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from db import get_db
from services.writing_specs import history, list_specs, rollback, save_spec

router = APIRouter(prefix="/writing-specs", tags=["writing-specs"])


class SpecBody(BaseModel):
    body: str = Field(min_length=1)


class RollbackBody(BaseModel):
    version: int = Field(ge=1)


@router.get("")
async def read_specs():
    async with get_db() as db:
        return await list_specs(db)


@router.put("/{key}")
async def write_spec(key: str, body: SpecBody):
    async with get_db() as db:
        try:
            return await save_spec(db, key, body.body)
        except ValueError as exc:
            raise HTTPException(409, str(exc))


@router.get("/{key}/history")
async def read_history(key: str):
    async with get_db() as db:
        return await history(db, key)


@router.post("/{key}/rollback")
async def rollback_spec(key: str, body: RollbackBody):
    async with get_db() as db:
        try:
            return await rollback(db, key, body.version)
        except ValueError as exc:
            raise HTTPException(409, str(exc))

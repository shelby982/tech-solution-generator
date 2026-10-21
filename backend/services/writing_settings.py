"""编写模型分工设置：项目默认 + 章节覆盖 → 解析出本次实际使用的模型。

解析顺序（v3 §6.2）：
  章节覆盖(block) → 项目默认(project) → 内置默认(§6.1) → 首个可用生成配置
"""
from __future__ import annotations

import json

ROLE_LABELS = {
    "chief": "主编",
    "text": "文字",
    "rewrite": "文字精修",
    "diagram": "图片",
    "table": "表格",
}
# 内置默认：用"模型名包含的关键字"表达，避免硬编码具体模型
ROLE_DEFAULTS = {
    "chief": "pro",
    "text": "flash",
    "rewrite": "pro",
    "diagram": "flash",
    "table": "flash",
}


async def _load(db, scope: str, scope_id: str) -> dict:
    row = await (await db.execute(
        "SELECT settings FROM writing_model_settings WHERE scope=? AND scope_id=?", (scope, scope_id)
    )).fetchone()
    if not row:
        return {}
    try:
        value = json.loads(row[0] or "{}")
    except Exception:
        return {}
    return {k: str(v) for k, v in (value or {}).items() if str(v or "").strip()}


async def project_settings(db, project_id: int) -> dict:
    return await _load(db, "project", str(project_id))


async def block_settings(db, block_id: int) -> dict:
    return await _load(db, "block", str(block_id))


async def save_settings(db, scope: str, scope_id: str, settings: dict, reset: bool = False) -> dict:
    # reset：清空该层设置（删除行），回到上一层解析
    if reset:
        await db.execute("DELETE FROM writing_model_settings WHERE scope=? AND scope_id=?",
                         (scope, str(scope_id)))
        await db.commit()
        return {}
    cleaned = {role: str(model).strip() for role, model in (settings or {}).items()
               if role in ROLE_LABELS and str(model or "").strip()}
    await db.execute(
        "INSERT INTO writing_model_settings (scope, scope_id, settings, updated_at) VALUES (?,?,?,CURRENT_TIMESTAMP) "
        "ON CONFLICT(scope, scope_id) DO UPDATE SET settings=excluded.settings, updated_at=CURRENT_TIMESTAMP",
        (scope, str(scope_id), json.dumps(cleaned, ensure_ascii=False)),
    )
    await db.commit()
    return cleaned


def _pick(configs: list, hint: str) -> str:
    """按关键字从可用配置里挑一个模型（'pro' / 'flash'）。"""
    for config in configs:
        if hint and hint in str(getattr(config, "model", "")).lower():
            return str(config.model)
    return ""


def available_models() -> list[str]:
    from services.config_store import config_store

    return [str(c.model) for c in (config_store.get_all() or []) if getattr(c, "model", "")]


async def resolve_model(db, project_id: int, block_id: int | None, role: str) -> dict:
    """返回 {role, requested, used, source, fallback, available}。"""
    from services.config_store import config_store

    configs = config_store.get_all() or []
    models = [str(c.model) for c in configs if getattr(c, "model", "")]
    chain: list[tuple[str, str]] = []
    if block_id is not None:
        block_map = await block_settings(db, block_id)
        if block_map.get(role):
            chain.append(("block", block_map[role]))
    project_map = await project_settings(db, project_id)
    if project_map.get(role):
        chain.append(("project", project_map[role]))
    hint = ROLE_DEFAULTS.get(role, "")
    builtin = _pick(configs, hint)
    if builtin:
        chain.append(("builtin", builtin))
    if models:
        chain.append(("fallback", models[0]))

    requested = chain[0][1] if chain else ""
    for source, model in chain:
        if model and model in models:
            return {"role": role, "requested": requested, "used": model, "source": source,
                    "fallback": source == "fallback", "available": models}
    return {"role": role, "requested": requested, "used": "", "source": "none",
            "fallback": False, "available": models}

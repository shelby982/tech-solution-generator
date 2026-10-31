"""设计版图表资产：DB 行 + 文件存储（HTML/PNG/SVG）。

资产是项目级文件；正文只通过 `/api/studio/diagrams/{id}/image` 引用 PNG。
所有路径都由服务端生成（uuid），不接受用户路径。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from db import get_db

ROOT = Path(__file__).resolve().parent.parent / "data" / "diagrams"


def _project_dir(project_id: int) -> Path:
    path = ROOT / str(int(project_id))
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_asset_id() -> str:
    return uuid.uuid4().hex


def html_path(project_id: int, asset_id: str) -> Path:
    return _project_dir(project_id) / f"{asset_id}.html"


def png_path(project_id: int, asset_id: str) -> Path:
    return _project_dir(project_id) / f"{asset_id}.png"


def svg_path(project_id: int, asset_id: str) -> Path:
    return _project_dir(project_id) / f"{asset_id}.svg"


async def create_job(project_id: int, block_id: int, brief: dict | None = None) -> str:
    job_id = uuid.uuid4().hex
    async with get_db() as db:
        await db.execute(
            "INSERT INTO diagram_jobs(id, project_id, block_id, status, stage, brief_json) VALUES(?,?,?,?,?,?)",
            (job_id, int(project_id), int(block_id), "queued", "排队中",
             json.dumps(brief or {}, ensure_ascii=False)),
        )
        await db.commit()
    return job_id


async def set_job(job_id: str, *, status: str | None = None, stage: str | None = None,
                  asset_id: str | None = None, error: str | None = None) -> None:
    fields, args = [], []
    for name, value in (("status", status), ("stage", stage), ("asset_id", asset_id), ("error", error)):
        if value is not None:
            fields.append(f"{name}=?")
            args.append(value)
    if not fields:
        return
    async with get_db() as db:
        await db.execute(f"UPDATE diagram_jobs SET {', '.join(fields)} WHERE id=?", [*args, job_id])
        await db.commit()


async def get_job(job_id: str) -> dict | None:
    async with get_db() as db:
        row = await (await db.execute("SELECT * FROM diagram_jobs WHERE id=?", (job_id,))).fetchone()
    return dict(row) if row else None


async def save_asset(project_id: int, block_id: int | None, *, section: str, title: str,
                     skill_type: str, size_preset: str, html: str, png: bytes, svg: str,
                     brief: dict, checks: dict, model: str, width: int, height: int,
                     asset_id: str | None = None, prompt_hash: str = "") -> str:
    asset_id = asset_id or new_asset_id()
    html_path(project_id, asset_id).write_text(html, encoding="utf-8")
    png_path(project_id, asset_id).write_bytes(png)
    if svg:
        svg_path(project_id, asset_id).write_text(svg, encoding="utf-8")
    async with get_db() as db:
        await db.execute(
            "INSERT INTO diagram_assets(id, project_id, block_id, section, title, skill_type, size_preset,"
            " html, svg, png_path, png_width, png_height, brief_json, checks_json, model,"
            " prompt_hash, version, status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (asset_id, int(project_id), block_id, section, title, skill_type, size_preset,
             html, svg, str(png_path(project_id, asset_id)), width, height,
             json.dumps(brief, ensure_ascii=False), json.dumps(checks, ensure_ascii=False),
             model, prompt_hash, 1, "done"),
        )
        await db.commit()
    return asset_id


async def find_by_prompt_hash(project_id: int, prompt_hash: str) -> dict | None:
    """同项目、同 prompt、同模型的历史资产直接复用（避免重复烧模型）。"""
    if not prompt_hash:
        return None
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT * FROM diagram_assets WHERE project_id=? AND prompt_hash=? AND status='done'"
            " AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 1",
            (int(project_id), prompt_hash),
        )).fetchone()
    return dict(row) if row else None


async def get_asset(asset_id: str) -> dict | None:
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT * FROM diagram_assets WHERE id=? AND deleted_at IS NULL", (asset_id,)
        )).fetchone()
    return dict(row) if row else None


def read_png(asset: dict) -> bytes:
    path = Path(asset.get("png_path") or "")
    if not path.is_file():
        raise FileNotFoundError("图表图片文件不存在")
    return path.read_bytes()


def read_html(asset: dict) -> str:
    text = str(asset.get("html") or "")
    if text:
        return text
    return html_path(int(asset["project_id"]), str(asset["id"])).read_text(encoding="utf-8")

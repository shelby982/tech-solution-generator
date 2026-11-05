"""节级写作结果：持久化 used/context/cited 证据与覆盖，供刷新后核对。"""
from __future__ import annotations

import json
import re

_CITE_RE = re.compile(r"【来源[:：]([^】]{1,100})】")


def cited_sources(text: str) -> list[str]:
    seen, out = set(), []
    for match in _CITE_RE.finditer(str(text or "")):
        label = match.group(1).strip()
        if label and label not in seen:
            seen.add(label)
            out.append(label)
    return out[:12]


async def save_result(block_id: int, section: str, result: dict) -> None:
    from db import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO section_write_results(block_id,section,result_json,updated_at) "
            "VALUES(?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(block_id,section) DO UPDATE SET "
            "result_json=excluded.result_json,updated_at=CURRENT_TIMESTAMP",
            (int(block_id), str(section or ""), json.dumps(result or {}, ensure_ascii=False)),
        )
        await db.commit()


async def list_results(block_id: int) -> dict[str, dict]:
    from db import get_db
    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT section, result_json, updated_at FROM section_write_results WHERE block_id=?",
            (int(block_id),))).fetchall()
    out = {}
    for row in rows:
        try:
            data = json.loads(row[1] or "{}")
        except ValueError:
            data = {}
        data["updated_at"] = row[2]
        out[str(row[0] or "")] = data
    return out

"""编写规范：L0 全局纪律 + L1 模块配方，多版本、可回滚、生成留痕。"""
from __future__ import annotations

import aiosqlite
import pytest

from db import init_db
from services.writing_specs import ensure_seeded, history, list_specs, rollback, save_spec, spec_for


@pytest.fixture
async def db(tmp_path):
    conn = await aiosqlite.connect(str(tmp_path / "specs.db"))
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    yield conn
    await conn.close()


async def test_seeds_global_and_all_recipes(db):
    await ensure_seeded(db)
    data = await list_specs(db)
    keys = set(data["specs"])
    assert {"global", "understanding", "solution", "implementation", "evidence", "point_to_point"} <= keys
    assert data["specs"]["global"]["version"] == 1
    assert "图表" in data["specs"]["global"]["body"]
    assert "必表" in data["specs"]["understanding"]["body"]


async def test_save_creates_new_version_and_history(db):
    await ensure_seeded(db)
    first = await save_spec(db, "understanding", "新版规范：先结论句，再分维度，再图表证据。")
    assert first["version"] == 2
    hist = await history(db, "understanding")
    assert [row["version"] for row in hist["versions"]] == [2, 1]
    # 历史只增不改：旧版本正文仍在
    assert any("必表" in row["body"] for row in hist["versions"])


async def test_rollback_writes_old_body_as_new_version(db):
    await ensure_seeded(db)
    await save_spec(db, "solution", "临时改动")
    restored = await rollback(db, "solution", 1)
    assert restored["version"] == 3
    assert "必图" in restored["body"]      # 回到 v1 的正文


async def test_spec_for_returns_global_plus_recipe_with_versions(db):
    spec = await spec_for(db, "understanding")
    assert spec["global"]["version"] >= 1 and "来源" in spec["global"]["body"]
    assert spec["recipe"]["version"] >= 1 and "骨架" in spec["recipe"]["body"]

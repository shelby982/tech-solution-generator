"""schema 版本记录与外键开关。"""
from __future__ import annotations

import aiosqlite
import pytest

from db import SCHEMA_VERSION, init_db


@pytest.mark.asyncio
async def test_init_records_schema_version(tmp_path):
    path = tmp_path / "schema.db"
    conn = await aiosqlite.connect(path)
    await init_db(conn)
    assert (await (await conn.execute("PRAGMA user_version")).fetchone())[0] == SCHEMA_VERSION
    rows = await (await conn.execute("SELECT version FROM schema_migrations")).fetchall()
    assert [r[0] for r in rows] == [SCHEMA_VERSION]
    await conn.close()


@pytest.mark.asyncio
async def test_foreign_key_enforcement_is_opt_in(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENFORCE_FK", "1")
    path = tmp_path / "fk.db"
    conn = await aiosqlite.connect(path)
    await init_db(conn)
    assert (await (await conn.execute("PRAGMA foreign_keys")).fetchone())[0] == 1
    assert await (await conn.execute("PRAGMA foreign_key_check")).fetchall() == []
    await conn.close()

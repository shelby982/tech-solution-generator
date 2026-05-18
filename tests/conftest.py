"""
pytest 配置：将 backend 目录加入 sys.path，
使得 `from main import app` 等导入可以正常工作。
"""
import sys
import pytest
import aiosqlite
from pathlib import Path

# backend/ 目录
BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture
async def db():
    """提供内存 SQLite 连接，建完表后 yield，测试结束自动关闭。"""
    import db as db_module
    original_path = db_module.DB_PATH
    db_module.DB_PATH = ":memory:"
    try:
        async with db_module.get_db() as conn:
            await db_module.init_db(conn)
            yield conn
    finally:
        db_module.DB_PATH = original_path

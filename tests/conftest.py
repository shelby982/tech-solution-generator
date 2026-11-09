"""pytest 全局配置：把 backend/ 加入 sys.path，让 `from services.*` / `from infra.*` 能被解析。"""
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


@pytest.fixture(autouse=True)
def _reset_llm_config_health():
    """清空「坏配置冷却」状态。

    ``infra.llm.errors`` 的冷却是进程级内存状态（真实运行里正是要跨小节保留），
    测试之间必须隔离 —— 否则某个用例把配置打成不可用，会静默影响后续用例的
    重试次数断言。
    """
    from infra.llm import errors

    errors.reset_health()
    yield
    errors.reset_health()


@pytest.fixture(scope="session")
def _db_schema_template(tmp_path_factory):
    """建一次库结构，后面每个用例**复制**它 —— 逐用例 init_db 太慢（750 个用例）。"""
    import asyncio

    import aiosqlite

    import db as db_module

    path = tmp_path_factory.mktemp("dbschema") / "schema.db"

    async def _init() -> None:
        conn = await aiosqlite.connect(str(path))
        conn.row_factory = aiosqlite.Row
        await db_module.init_db(conn)
        await conn.commit()
        await conn.close()

    asyncio.run(_init())
    return path


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch, _db_schema_template):
    """每个用例把业务库指到临时文件：**绝不允许测试写生产库**。

    背景（真实事故，2026-10-13）：parse 节点会 `sync_outline_placeholders` 写 blocks，
    而 `test_nodes` / `test_runner` 里 project_id=99 / 1 的用例没有隔离数据库，
    于是**每次 `pytest` 都往 `backend/data/ge_solution.db` 里塞孤儿章节**。
    实测：清空全部数据后跑一次测试，库里又出现 project_id=1（2 条）与 99（1 条）的 blocks
    —— 生产数据被测试污染，且极难察觉（表里有行、但没有对应项目）。
    """
    import shutil

    import db as db_module

    # 测试夹具经常故意造"没有父行的 reviews/block_revisions"来测聚合，
    # 默认生产已开启外键；测试进程显式关闭，FK 本身由 test_schema_migrations 覆盖。
    monkeypatch.setenv("APP_ENFORCE_FK", "0")
    path = tmp_path / "ge_solution_test.db"
    shutil.copyfile(_db_schema_template, path)
    monkeypatch.setattr(db_module, "DB_PATH", str(path))
    yield

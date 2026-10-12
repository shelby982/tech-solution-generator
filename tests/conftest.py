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

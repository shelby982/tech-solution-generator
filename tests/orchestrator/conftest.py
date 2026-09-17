"""orchestrator 测试的公共装配。

「要求与大纲」第二步（逐节 8 字段提炼）在应用里默认关闭
（``nodes.ENABLE_SECTION_EXTRACT=False``），因为目录结构还会反复调整，而提炼是
每个章节一次模型调用。但下游的匹配 / 生成 / 评审 / 收敛链路依赖提炼产出的矩阵，
这里绝大多数用例要跑完整条图，所以默认把第二步打开。

应用实际跑的那套（第二步关闭）由这两个用例单独锚住，它们会自己把开关关掉：
``test_graph.py::test_extract_edge_ends_when_section_extract_disabled`` 与
``test_graph.py::test_outline_gate_resume_ends_when_extract_disabled``。
"""

import pytest

from orchestrator import nodes


@pytest.fixture(autouse=True)
def _enable_section_extract(monkeypatch):
    monkeypatch.setattr(nodes, "ENABLE_SECTION_EXTRACT", True)

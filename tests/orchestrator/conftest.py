"""orchestrator 测试的公共装配。

「要求与大纲」第二步（逐节 8 字段提炼）由闸门 1 的「确认目录，开始提炼要点」触发
（``nodes.ENABLE_SECTION_EXTRACT=True``）。下游的匹配 / 生成 / 评审 / 收敛链路依赖
提炼产出的矩阵，而生产形态下提炼跑完会收尾、等素材库手动触发匹配，这里绝大多数
用例要一次跑完整条图，所以两个开关一起打开。

生产形态（提炼完收尾、不自动接匹配）由这两个用例单独锚住，它们会自己把开关关掉：
``test_graph.py::test_extract_ends_without_auto_match`` 与
``test_graph.py::test_outline_gate_resume_ends_when_extract_disabled``。
"""

import pytest

from orchestrator import graph as graph_module
from orchestrator import nodes


@pytest.fixture(autouse=True)
def _enable_section_extract(monkeypatch):
    monkeypatch.setattr(nodes, "ENABLE_SECTION_EXTRACT", True)
    # 生产形态下提炼跑完会停在「要求与大纲」，等素材库手动触发匹配；这里要一次
    # 跑完整条图，所以打开自动接匹配。
    monkeypatch.setattr(graph_module, "AUTO_MATCH_AFTER_EXTRACT", True)

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from services.diagram_quality import check_nodes_in_text


def test_nodes_found_in_text():
    text = "现状为未建立至部分建立，目标是基本建立至完整建立，并形成差距带。"
    nodes = ["现状：未建立至部分建立", "目标：基本建立至完整建立", "差距带"]
    assert check_nodes_in_text(nodes, text) == []


def test_invented_node_flagged():
    text = "现状为未建立至部分建立，目标是基本建立至完整建立。"
    assert check_nodes_in_text(["虚构的第三层能力"], text) == ["虚构的第三层能力"]


def test_empty_text_flags_all():
    assert check_nodes_in_text(["甲", "乙"], "") == ["甲", "乙"]

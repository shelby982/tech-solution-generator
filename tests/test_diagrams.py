"""信息图结构：解析 / 生成文本 / 兼容旧格式。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from services.diagrams import parse_diagram, render_diagram_markdown, normalize_model_output


def test_parse_layered():
    s = parse_diagram(":::diagram type=layered 业务框架", ["- 总览", "-- 供电区域", "-- 主要板块"])
    assert s["type"] == "layered"
    assert s["title"] == "业务框架"
    assert [n["level"] for n in s["nodes"]] == [0, 1, 1]


def test_parse_timeline():
    s = parse_diagram(":::diagram type=timeline 演进", ["2024 | 试点", "2025 | 推广", "2026 | 全域"])
    assert s["type"] == "timeline"
    assert s["nodes"][0]["when"] == "2024"


def test_parse_flow_backward_compatible():
    s = parse_diagram(":::diagram 旧图", ["采集层 -> 存储层 -> 展示层"])
    assert s["type"] == "flow"
    assert [n["label"] for n in s["nodes"]] == ["采集层", "存储层", "展示层"]
    assert len(s["edges"]) == 2


def test_render_roundtrip():
    s = {"type": "layered", "title": "框架", "nodes": [
        {"label": "总览", "level": 0}, {"label": "分层A", "level": 1}]}
    md = render_diagram_markdown(s)
    assert md.startswith(":::diagram type=layered 框架")
    back = parse_diagram(md.splitlines()[0], md.splitlines()[1:-1])
    assert [n["label"] for n in back["nodes"]] == ["总览", "分层A"]
    assert [n["level"] for n in back["nodes"]] == [0, 1]


def test_normalize_legacy_and_timeline_without_year():
    legacy = normalize_model_output({"nodes": ["A", "B", "C"]})
    assert legacy["type"] == "flow" and len(legacy["nodes"]) == 3
    t = normalize_model_output({"type": "timeline", "nodes": ["A", "B"]})
    assert t["type"] == "flow"          # 没有年份 → 退化为流程
    assert normalize_model_output({"nodes": ["单个"]}) is None

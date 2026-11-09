"""架构守卫：同一模块内不得出现重名顶层函数。

真实事故：`services/block_store.py` 里 `update_block_source` 定义了两次，后者静默
覆盖前者；`orchestrator/nodes.py` 仍按旧签名调用 → TypeError 被宽 `except` 吞掉，
匹配依据从不落库。Python 不会报错，只有运行时踩到才发现。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "backend"


def test_no_duplicate_top_level_functions():
    duplicates = []
    for path in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in path.parts or "skills" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        seen = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in seen:
                    duplicates.append(f"{path.relative_to(ROOT)}: {node.name} 定义于 "
                                      f"{seen[node.name]} 与 {node.lineno}")
                seen.setdefault(node.name, node.lineno)
    assert not duplicates, "同一模块出现重名顶层函数：\n" + "\n".join(duplicates)

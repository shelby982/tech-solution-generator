"""infra/llm/json_utils.py — LLM 返回值的 JSON 抽取工具。

原本散落在 dispatcher.py / rerank.py / wang_anshi.py / bao_zheng.py 四处，
Task 3.7 收敛到此处统一维护，避免后续修改 bracket-state 机时漏改。
"""

import json


def extract_json_object(text: str) -> dict:
    """
    从模型返回里抽出第一个完整 JSON 对象。先剥 ``` / ```json 围栏，
    再用栈匹配第一对 {...}（跳过字符串内的 { 和 }）。
    抛 ValueError 表示找不到合法 JSON。
    """
    s = text.strip()
    # 剥围栏
    if s.startswith("```"):
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1:]
        if s.endswith("```"):
            s = s[:-3]
        s = s.strip()

    start = s.find("{")
    if start == -1:
        raise ValueError(f"未找到 JSON 起始 {{：{text[:120]}")

    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(s[start:i + 1])
    raise ValueError(f"JSON 对象未闭合：{text[:120]}")


def salvage_nodes_array(text: str) -> list[dict] | None:
    """从被截断的模型输出里抢救出已经写完整的 nodes 元素。

    ``extract_json_object`` 要求最外层 {} 闭合，被 max_tokens 截断时整段报废。
    目录派生特意用「扁平数组 + 显式 parent」而非嵌套 children，就是为了这种情况
    还能降级成「部分成功」——嵌套结构截断会丢掉整棵子树，扁平数组只是少几个节点。
    本函数把数组里已经闭合的 {...} 逐个抠出来。

    返回 None 表示一个完整元素都没抠到，调用方应按整体失败处理。
    """
    s = text.strip()
    start = s.find("{")
    if start == -1:
        return None
    key = s.find('"nodes"', start)
    if key == -1:
        return None
    arr = s.find("[", key)
    if arr == -1:
        return None

    nodes: list[dict] = []
    depth = 0
    in_str = False
    esc = False
    elem_start = -1

    for i in range(arr + 1, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                elem_start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and elem_start != -1:
                try:
                    nodes.append(json.loads(s[elem_start:i + 1]))
                except json.JSONDecodeError:
                    pass
                elem_start = -1
        elif ch == "]" and depth == 0:
            break

    return nodes or None


__all__ = ["extract_json_object", "salvage_nodes_array"]

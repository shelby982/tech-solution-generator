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


__all__ = ["extract_json_object"]

"""引用补齐（系统先做）：把"文件名"补成"文件名（文号，施行日期）"，并补标准号。

这些是**确定信息**，不需要模型、也不需要用户动手：
- 三部法律的文号与施行日期是固定的
- 等级保护的标准号（GB/T 22239 / 28448）是固定的

只补"缺的部分"：正文已有文号的跳过；已有 GB/T 标准号的跳过。
"""
from __future__ import annotations

import re

# 法律 → (文号, 施行日期)
LAW_META = {
    "中华人民共和国网络安全法": ("主席令第五十三号", "2017年6月1日"),
    "中华人民共和国数据安全法": ("主席令第八十四号", "2021年9月1日"),
    "中华人民共和国个人信息保护法": ("主席令第九十一号", "2021年11月1日"),
}
# 等级保护标准
STANDARD_GB = "GB/T 22239-2019"
STANDARD_TEST = "GB/T 28448-2019"
STANDARD_SENTENCE = (
    f"网络安全等级保护相关技术与管理措施按 {STANDARD_GB}《信息安全技术 网络安全等级保护基本要求》"
    f"与 {STANDARD_TEST}《信息安全技术 网络安全等级保护测评要求》执行。"
)

_DOC_NO = re.compile(r"主席令|国务院令|第\s*[一二三四五六七八九十百零〇\d]+\s*号")
_STD = re.compile(r"GB/?T\s*\d{3,5}(?:[-—]\d{4})?")
_SECTION = re.compile(r"(?m)^#{2,6}\s+(.+?)\s*$")


def enrich(content: str) -> tuple[str, list[str]]:
    """返回 (新正文, 改动说明)。没有可补的返回原文。"""
    text = str(content or "")
    changes: list[str] = []
    has_doc_no = bool(_DOC_NO.search(text))

    # ① 政策文号：整篇没有文号时，给首次出现的三部法律补上（已是"（主席令…）"的不重复）
    if not has_doc_no:
        for law, (doc_no, effective) in LAW_META.items():
            pattern = re.compile(r"《" + re.escape(law) + r"》(?!（)")
            if not pattern.search(text):
                continue
            text = pattern.sub(f"《{law}》（{doc_no}，{effective}施行）", text, count=1)
            changes.append(f"补文号：《{law}》→ {doc_no}")

    # ② 标准号：提到"等级保护"却没有 GB/T → 在"政策依据"类小节末尾补一句
    if "等级保护" in text and not _STD.search(text):
        inserted = False
        for match in _SECTION.finditer(text):
            if "政策" in match.group(1) or "依据" in match.group(1) or "标准" in match.group(1):
                start = match.start()
                next_head = text.find("\n## ", match.end())
                insert_at = next_head if next_head > 0 else len(text)
                text = text[:insert_at].rstrip() + "\n\n" + STANDARD_SENTENCE + "\n" + text[insert_at:]
                inserted = True
                changes.append(f"补标准号：{STANDARD_GB} / {STANDARD_TEST}")
                break
        if not inserted:
            text = text.rstrip() + "\n\n" + STANDARD_SENTENCE + "\n"
            changes.append(f"补标准号：{STANDARD_GB} / {STANDARD_TEST}")
    return text, changes

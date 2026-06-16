"""公文类章节识别 — 用于决定 zhuge_liang agent 是否走 letter 分支。

承诺书 / 保证函 / 声明书 / 授权委托书等公文有专属生成模板（含落款占位），
与普通技术 block 走不同的 prompt 路径。

移自 backend/infra/llm/dispatcher.py（Phase 2.5），dispatcher 处保留 re-export
以兼容其它调用点；Phase 7 服务层删除时一并清理 re-export。
"""


# ─────────────────────────────────────────────
# 公文关键词词表
# ─────────────────────────────────────────────

LETTER_KEYWORDS: tuple[str, ...] = (
    "承诺书", "承诺函", "保证书", "保证函", "声明书", "声明函",
    "履约保证", "廉洁承诺", "廉政承诺", "诚信承诺", "授权委托书",
    "投标声明", "无违法承诺", "技术承诺", "服务承诺", "保密承诺",
    "质量承诺", "进度承诺", "供货承诺", "售后服务承诺",
)


def is_letter_section(title: str) -> bool:
    """根据章节标题判断是否为承诺书/保证函/声明书等公文类内容。

    判定规则：标题去首尾空白后，包含 LETTER_KEYWORDS 中任一关键词即视为公文。
    空标题或纯空白返回 False。
    """
    if not title:
        return False
    t = title.strip()
    return any(kw in t for kw in LETTER_KEYWORDS)


__all__ = [
    "LETTER_KEYWORDS",
    "is_letter_section",
]

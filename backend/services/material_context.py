"""素材接入编写（M5）：把"本章已选素材"按用法转成生成提示词。

用法（mode）：
  cite    只给来源标签 → 模型引用时标【来源：X】
  data    给片段内容（≤300 字）→ 用于写数字/事实
  context 给片段内容（≤500 字）→ 只影响写法，禁止出现在正文
"""
from __future__ import annotations

from db import get_db

MODE_LIMITS = {"cite": 0, "data": 300, "context": 500}
async def chapter_material_context(db, block_id: int, block_code: str = "",
                                   section: str = "") -> dict:
    """返回 {cite: [labels], data: [texts], context: [texts], count}。

    section 非空时：**只取绑定到该节**的素材；没有就返回空，不回退整章。
    这是为了避免"本节有素材"的错觉——别的节素材不能拿来写本节。
    """
    from services.material_cards import bound_cards, usable_card

    cards = [item for item in await bound_cards(db, block_id, section) if usable_card(item)]
    result: dict = {"cite": [], "data": [], "context": [], "count": 0,
                    "section": section, "section_missing": bool(section and not cards)}
    for item in cards:
        mode = item["mode"]
        if mode not in MODE_LIMITS:
            continue
        label = item["locator"]
        if item["source_type"] == "web":
            label = f"{label}（公开资料）"
        if mode == "cite":
            result["cite"].append(label)
        elif mode == "data":
            result["data"].append(f'{label}：{item["text"][:MODE_LIMITS[mode]]}')
        else:
            result["context"].append(item["text"][:MODE_LIMITS[mode]])
        result["count"] += 1
    return result


def render_for_prompt(context: dict) -> str:
    """把素材上下文渲染成提示词片段（空则返回空串）。"""
    if not context or not context.get("count"):
        return ""
    parts: list[str] = ["公开资料仅用于技术参考，不得证明我方业绩、资质、人员或项目承诺。"]
    if context["cite"]:
        parts.append("【可引用来源】（引用时必须标注【来源：X】，X 取下列名称）\n"
                     + "\n".join(f"- {label}" for label in context["cite"][:12]))
    if context["data"]:
        parts.append("【数据依据】（仅支撑来源直接证明的事实，引用时标【来源：来源名称】）\n"
                     + "\n".join(f"- {text}" for text in context["data"][:6]))
    if context["context"]:
        parts.append("【背景材料】（只用于把握口径与写法，**不得整段照抄**，也不要标注来源）\n"
                     + "\n".join(f"- {text}" for text in context["context"][:6]))
    return "\n\n".join(parts)


async def material_prompt_for_block(block_pk: int, block_code: str = "", section: str = "") -> str:
    async with get_db() as db:
        context = await chapter_material_context(db, block_pk, block_code, section)
    return render_for_prompt(context)

"""素材接入编写（M5）：把"本章已选素材"按用法转成生成提示词。

用法（mode）：
  cite    只给来源标签 → 模型引用时标【来源：X】
  data    给片段内容（≤300 字）→ 用于写数字/事实
  context 给片段内容（≤500 字）→ 只影响写法，禁止出现在正文
"""
from __future__ import annotations

from db import get_db

MODE_LIMITS = {"cite": 0, "data": 300, "context": 500}
MODE_LABELS = {"cite": "引用出处", "data": "数据依据", "context": "仅上下文"}


async def chapter_material_context(db, block_id: int, block_code: str = "",
                                   section: str = "") -> dict:
    """返回 {cite: [labels], data: [texts], context: [texts], count}。

    section 非空时：优先取"绑定到该节"的素材；该节没有绑定则回退整章素材（兼容历史数据）。
    """
    rows = []
    if section:
        rows = await (await db.execute(
            "SELECT b.chunk_id, COALESCE(b.mode,'cite'), COALESCE(b.chunk_kind,'file') "
            "FROM chapter_material_chunks b WHERE b.block_id=? AND b.section=?", (block_id, section))).fetchall()
    if not rows:
        rows = await (await db.execute(
            "SELECT b.chunk_id, COALESCE(b.mode,'cite'), COALESCE(b.chunk_kind,'file') "
            "FROM chapter_material_chunks b WHERE b.block_id=?", (block_id,))).fetchall()
    result: dict = {"cite": [], "data": [], "context": [], "count": 0}
    for chunk_id, mode, kind in rows:
        mode = mode if mode in MODE_LIMITS else "cite"
        if kind == "cite":
            row = await (await db.execute(
                "SELECT cc.heading, cc.content FROM citation_chunks cc WHERE cc.id=?",
                (abs(int(chunk_id)),))).fetchone()
            label = (row[0] if row and row[0] else "公开来源")[:40]
            text = (row[1] if row else "") or ""
        else:
            row = await (await db.execute(
                "SELECT mc.page, mc.content, m.filename FROM material_chunks mc "
                "JOIN materials m ON m.id=mc.material_id WHERE mc.id=?", (int(chunk_id),))).fetchone()
            if not row:
                continue
            label = f"{row[2] or '素材'}（第 {row[0]} 页）"[:60]
            text = row[1] or ""
        if mode == "cite":
            result["cite"].append(label)
        else:
            limit = MODE_LIMITS[mode]
            result[mode].append(text[:limit])
        result["count"] += 1
    return result


def render_for_prompt(context: dict) -> str:
    """把素材上下文渲染成提示词片段（空则返回空串）。"""
    if not context or not context.get("count"):
        return ""
    parts: list[str] = []
    if context["cite"]:
        parts.append("【可引用来源】（引用时必须标注【来源：X】，X 取下列名称）\n"
                     + "\n".join(f"- {label}" for label in context["cite"][:12]))
    if context["data"]:
        parts.append("【数据依据】（可据此写具体数字/事实，引用时标【来源】）\n"
                     + "\n".join(f"- {text}" for text in context["data"][:6]))
    if context["context"]:
        parts.append("【背景材料】（只用于把握口径与写法，**不得整段照抄**，也不要标注来源）\n"
                     + "\n".join(f"- {text}" for text in context["context"][:6]))
    return "\n\n".join(parts)


async def material_prompt_for_block(block_pk: int, block_code: str = "", section: str = "") -> str:
    async with get_db() as db:
        context = await chapter_material_context(db, block_pk, block_code, section)
    return render_for_prompt(context)


NO_MATERIAL_HINT = ("【本节无可用素材】不要编造具体项目名称、合同金额与精确数字；"
                    "用概括但完整的表述把内容写完（如「我方具备同类项目实施经验」）。")

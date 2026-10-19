"""编写规范（P0.5）：L0 全局纪律 + L1 模块配方，多版本、可回滚、生成留痕。

分层（产品确认）：
- **L0 全局纪律**：所有模块共用的写作方法（论证链、图表规则、来源合规、篇幅语言）
- **L1 模块配方**：每个章节类型一份（骨架 / 必表 / 必图 / 失分点），轻量、可在界面上编辑
- 生成时把两层一起注入 package（`writing_spec`），并把**版本号**记进 job.artifacts —— 每份成稿
  都能回答"这句是用哪版规范生成的"。

首版种子来自《对项目的理解》参考件的提炼（459,702 字 / 63 表 / 19 图），其余模块按行业惯例给出初版。
"""

from __future__ import annotations

import uuid

SEED_GLOBAL = """一、论证与结构
1. 每节先给结论句（判断），再给分维度事实，最后用图表证据支撑并落到本章要回应的问题。
2. 外部依据按"政策/标准 → 行业趋势 → 集团/省公司部署 → 本项目任务"的顺序推进，不堆政策清单。
3. 目标与"已实现"必须分开表述，不把目标写成已完成。

二、图表
1. 每节至少 1 张表；全章至少 3 张图（结构图 / 流程图 / 趋势图）。
2. 表前写一句"这张表说明什么"，图后写一句"因此…"，图表不独立成段。
3. 表格用维度化列（现状 / 差距 / 目标 / 措施），每格有依据；没有依据的格写【待补充】。

三、来源与合规
1. 外部信息必须有官方来源：发布机构 · 文号/日期 · 标题 · URL · 摘录≤200 字。
2. 禁止二手转载、自媒体、公众号、百科；找不到官方来源写【待补充：需官方来源】。
3. 我方荣誉、业绩、人员只写已核实材料；不夸大、不承诺未落地能力。

四、篇幅与语言
1. 篇幅按「应答策划」的分配；1 页约 250 字，按分配页数控制篇幅。
2. 判断句开头、量化优先；不用"非常/极大/极大提升"这类空词；引用数据要注明出处与口径。"""

SEED_RECIPES = {
    "understanding": """【骨架】政策依据 → 现状差距 → 目标指标 → 范围与内容 → 重难点与对策 → 业务需求特点
【必表】政策依据映射表（文件/要求/对应工作）｜现状维度表（维度/现状特征/主要挑战/影响范围）｜目标指标表（指标/当前/目标/说明）｜纳入-不纳入对照表｜交付物清单表（交付物/核心能力/验收）｜难点表（难点/复杂度/影响/风险等级）｜对策表（措施/技术路线/预期成效）｜需求特点表（维度/指标项/目标值/验证方式/保障）｜痛点表（痛点/表现/影响/紧迫度）
【必图】趋势或现状对比图｜范围边界图｜内容分解图｜难点-对策对应图｜需求-架构映射图
【失分点】只复述招标文件、没有现状数据、目标与现状混淆、范围越界承诺、重难点无对策""",
    "solution": """【骨架】需求回应 → 总体架构 → 分模块方案 → 关键技术专题 → 验证与交付
【必表】需求-响应矩阵｜模块能力表（模块/能力/关键技术/验证方式）｜技术路线表（层级/现有/目标/迁移策略）｜验证与交付表
【必图】总体架构图｜部署拓扑｜关键调用链或数据流
【失分点】架构图与文字不对应、只讲通用产品不讲本项目适配、验证方式缺失、接口与数据流讲不清""",
    "implementation": """【骨架】实施阶段 → 里程碑 → 资源与职责 → 风险与保障
【必表】阶段计划表（阶段/重点工作/产出物）｜里程碑表（里程碑/时间/验收标准）｜资源与职责表｜风险应对表
【必图】进度甘特图或阶段流程图｜关键路径
【失分点】时间不可行、里程碑无验收标准、资源与人员表不一致、风险无应对""",
    "evidence": """【骨架】角色与资质 → 业绩与案例 → 证明材料清单
【必表】人员表（角色/资质/年限/证明材料）｜业绩表（项目/金额/时间/证明）｜证书清单
【必图】组织与分工图
【失分点】无证明材料、资质与人员表不对应、业绩与本项目无关""",
    "point_to_point": """【骨架】原文条款 → 逐项响应 → 证明或关联章节
【必表】响应表（条款号/原文要点/响应/证明或关联章节）
【必图】无（按条款顺序组织，避免打乱原文结构）
【失分点】漏条、只写"满足"无方案、条款顺序与原文不一致""",
}

LAYOUT_KEYS = ["global", "understanding", "solution", "implementation", "evidence", "point_to_point"]


def _row_dict(row):
    return row if isinstance(row, dict) else dict(row)


async def ensure_seeded(db) -> None:
    """首次访问时把种子规范写入（幂等：已有该 key 的任何版本就跳过）。"""
    for key, body, kind, layout in [("global", SEED_GLOBAL, "global", "")] + [
        (layout, SEED_RECIPES[layout], "recipe", layout) for layout in SEED_RECIPES
    ]:
        existing = await (await db.execute(
            "SELECT 1 FROM writing_specs WHERE key=? LIMIT 1", (key,),
        )).fetchone()
        if existing:
            continue
        await db.execute(
            "INSERT INTO writing_specs (id, key, kind, layout_key, version, body, status) "
            "VALUES (?, ?, ?, ?, 1, ?, 'published')",
            (uuid.uuid4().hex, key, kind, layout, body),
        )
    await db.commit()


async def _latest(db, key: str) -> dict | None:
    row = await (await db.execute(
        "SELECT * FROM writing_specs WHERE key=? ORDER BY version DESC LIMIT 1", (key,),
    )).fetchone()
    return dict(row) if row else None


async def list_specs(db) -> dict:
    """当前生效的全局纪律 + 各模块配方（含版本号），供「编写规范」面板展示。"""
    await ensure_seeded(db)
    specs = {}
    for key in LAYOUT_KEYS:
        latest = await _latest(db, key)
        if latest:
            specs[key] = {"key": key, "version": latest["version"], "body": latest["body"],
                          "kind": latest["kind"], "layout_key": latest["layout_key"],
                          "updated_at": latest["created_at"]}
    return {"specs": specs}


async def spec_for(db, layout_key: str) -> dict:
    """生成时注入的两层规范：全局纪律 + 当前章节类型的配方（带宽本号，供留痕）。"""
    await ensure_seeded(db)
    global_spec = await _latest(db, "global")
    recipe = await _latest(db, layout_key) or await _latest(db, "solution")
    return {
        "global": {"version": global_spec["version"] if global_spec else 0,
                   "body": global_spec["body"] if global_spec else ""},
        "recipe": {"version": recipe["version"] if recipe else 0,
                   "body": recipe["body"] if recipe else ""},
    }


async def save_spec(db, key: str, body: str) -> dict:
    """编辑规范 → 追加一个新版本（不覆盖历史，可回滚）。"""
    await ensure_seeded(db)
    if key not in LAYOUT_KEYS:
        raise ValueError("未知的规范键")
    if not str(body or "").strip():
        raise ValueError("规范内容不能为空")
    latest = await _latest(db, key)
    kind = latest["kind"] if latest else ("global" if key == "global" else "recipe")
    layout = latest["layout_key"] if latest else ("" if key == "global" else key)
    version = (latest["version"] if latest else 0) + 1
    await db.execute(
        "INSERT INTO writing_specs (id, key, kind, layout_key, version, body, status) "
        "VALUES (?, ?, ?, ?, ?, ?, 'published')",
        (uuid.uuid4().hex, key, kind, layout, version, str(body)),
    )
    await db.commit()
    return {"key": key, "version": version, "body": str(body)}


async def history(db, key: str) -> dict:
    await ensure_seeded(db)
    rows = await (await db.execute(
        "SELECT version, body, created_at FROM writing_specs WHERE key=? ORDER BY version DESC", (key,),
    )).fetchall()
    return {"key": key, "versions": [dict(r) for r in rows]}


async def rollback(db, key: str, version: int) -> dict:
    """回到历史版本：把该版本正文写成**新版本**（历史只增不改，可追溯）。"""
    row = await (await db.execute(
        "SELECT body FROM writing_specs WHERE key=? AND version=?", (key, version),
    )).fetchone()
    if not row:
        raise ValueError("找不到该版本")
    return await save_spec(db, key, row["body"])


__all__ = ["list_specs", "spec_for", "save_spec", "history", "rollback", "ensure_seeded"]

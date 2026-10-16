"""把评分表解析出的「标的」补进包记录（唯一命中才写，幂等）。

背景（实测 project 29）：
  packages.target_code / project_packages.target / workflow_runs.target **都是空**，
  而评分表解析出 target='标的1'（在 split-report 的 package_matrix 里）。
  前端 resolvePackageScope 按 target+package_no 匹配 → 对不上 → 页面显示
  「当前标包与评分表的对应关系尚未明确」+「该标包没有解析到技术评分点」，
  看上去像"招标文件解析失败"，其实评分点都在。

为什么用"读报告时自愈"而不是启动迁移：标的的权威来源是评分表解析结果，
只有跑 build_split_report 时才拿得到（需要 run 的 spec）。这里在同一份数据上顺手补齐，
只填空值、只在唯一命中时写，因此幂等且不会覆盖用户/人工的修改。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_TARGET_PREFIX = re.compile(r"^(标的|标包|标段|第)+\s*")


def target_code_of(target: str) -> str:
    """'标的1' → '1'（packages.target_code 用编号）；已是编号则原样返回。"""
    code = _TARGET_PREFIX.sub("", str(target or "").strip()).strip()
    return code or str(target or "").strip()


async def backfill_package_targets(db, project_id: int, report: dict) -> int:
    """按 split-report 的 package_matrix 补齐缺失的"标的"。返回写入条数。"""
    wanted: dict[str, set[str]] = {}
    for row in (report or {}).get("package_matrix") or []:
        pkg = str(row.get("package_no") or "").strip()
        tgt = str(row.get("target") or "").strip()
        if pkg and tgt:
            wanted.setdefault(pkg, set()).add(tgt)

    written = 0
    for pkg, targets in wanted.items():
        if len(targets) != 1:
            continue                                   # 同一包号对应多个标的：不猜
        target = next(iter(targets))
        code = target_code_of(target)
        # 三处都补：包实体 / 标包投影 / 运行记录（都只填空值）
        statements = (
            ("packages", "target_code=?", "package_code=?", code, pkg),
            ("project_packages", "target=?", "package_no=?", target, pkg),
            ("workflow_runs", "target=?", "package_no=?", code, pkg),
        )
        for table, set_clause, where_col, value, key in statements:
            try:
                cur = await db.execute(
                    f"UPDATE {table} SET {set_clause} WHERE project_id=? AND {where_col} "
                    f"AND ({set_clause.split('=')[0]} IS NULL OR {set_clause.split('=')[0]}='')",
                    (value, project_id, key),
                )
                written += max(0, int(getattr(cur, "rowcount", 0) or 0))
            except Exception as exc:                   # 唯一约束冲突等：不影响报告
                logger.warning("标的回填跳过 %s(%s/%s)：%s", table, pkg, target, exc)
    if written:
        try:
            await db.commit()
        except Exception as exc:
            logger.warning("标的回填提交失败：%s", exc)
        logger.info("标的回填：project=%s 写入 %s 处", project_id, written)
    return written

# 一次性脚本归档

这些脚本均自述"一次性"、无 import/调度/CI 入口，仅用于历史数据修复或一次性重切。
保留是为了可追溯与必要时重跑；**不要**把它们接进启动或定时任务。

| 脚本 | 用途 |
|---|---|
| `normalize_extract_fields.py` | 把已提炼的「评分口径」从 requirement 归位到 score_items |
| `recut_old_materials.py` | 用新切片逻辑重切旧的 scoring/evaluation 材料 |
| `cleanup_pending_placeholders.py` | 清理正文里的占位内容（产品红线已前移到写入链路） |
| `normalize_diagram_blocks.py` | 规范正文里的 `:::diagram` 块格式 |
| `recover_gaps_from_revisions.py` | 从清理前 revision 恢复缺项待办 |
| `add_table_captions.py` | 给正文表格补表名 |

统一用法：从仓库根执行，如 `python scripts/oneoff/cleanup_pending_placeholders.py --help`。

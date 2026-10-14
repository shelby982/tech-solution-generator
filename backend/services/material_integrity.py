"""素材文件丢失检测。

背景（早就记下的遗留问题）：`materials.parse_status='done'`，但磁盘上的文件不见了 ——
界面一切正常，直到真正要用原文时才发现"素材是空的"。这类"记录在、文件没了"必须显式暴露。

判定只做**事实检查**，不做修复：
  · ``no_path``   记录里没有路径（历史数据）
  · ``missing``   路径不在磁盘上
  · ``empty``     文件是 0 字节
  · ``no_chunks`` 解析标记为完成/进行中，但**一个正文片段都没有**（最隐蔽的一种：
                  界面显示"已解析"，实际素材是空的，直到要用原文才暴露）
其余一律算 ok（可读性/权限问题不在这里判 —— 那是解析阶段的事）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

# 相对 backend/data 的根（materials.file_path 形如 uploads/{pid}/{name}）
DATA_ROOT = Path(__file__).resolve().parent.parent / "data"


def resolve_path(file_path: str, data_root: Path | None = None) -> Path:
    """把 ``uploads/{pid}/{name}`` 解析成绝对路径（也容忍已经是绝对路径的历史数据）。"""
    raw = str(file_path or "").strip()
    root = Path(data_root) if data_root is not None else DATA_ROOT
    p = Path(raw)
    return p if p.is_absolute() else (root / raw)


def check_materials(materials: Iterable[dict], data_root: Path | None = None,
                    chunk_counts: dict | None = None) -> dict:
    """检查每份素材是否可用。返回 ``{ok, total, lost: [...]}``。

    ``chunk_counts``: ``{material_id: 片段数}``（给了才判 ``no_chunks``）。
    ``lost`` 里每项：``{id, filename, role, file_path, reason}``（reason 见模块说明）。
    """
    lost: list[dict] = []
    total = 0
    counts = chunk_counts or {}
    for m in materials or []:
        total += 1
        fp = str(m.get("file_path") or "")
        # 先判"解析标记完成却没有正文"——比文件丢失更常见、更隐蔽
        if counts and str(m.get("parse_status") or "") in ("done", "parsing") \
                and int(counts.get(str(m.get("id"))) or counts.get(m.get("id")) or 0) == 0:
            lost.append({**{k: m.get(k) for k in ("id", "filename", "role")},
                         "file_path": fp, "reason": "no_chunks"})
            continue
        if not fp:
            lost.append({**{k: m.get(k) for k in ("id", "filename", "role")},
                         "file_path": "", "reason": "no_path"})
            continue
        p = resolve_path(fp, data_root)
        if not p.exists():
            lost.append({**{k: m.get(k) for k in ("id", "filename", "role")},
                         "file_path": fp, "reason": "missing"})
        elif p.is_file() and p.stat().st_size == 0:
            lost.append({**{k: m.get(k) for k in ("id", "filename", "role")},
                         "file_path": fp, "reason": "empty"})
    return {"ok": total - len(lost), "total": total, "lost": lost}

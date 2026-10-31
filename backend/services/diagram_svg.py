"""把设计版图表 HTML 导出为独立 SVG（调用 vendored 技能脚本，无第三方依赖）。"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "diagram_design"


def export_svg_sync(html_path: Path, svg_path: Path) -> str:
    script = SKILL_DIR / "scripts" / "export_svg.py"
    if not script.is_file():
        raise RuntimeError("技能缺少 export_svg.py")
    proc = subprocess.run(
        [sys.executable, str(script), str(html_path), str(svg_path), "--system-fonts"],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "SVG 导出失败").strip()[:500])
    return Path(svg_path).read_text(encoding="utf-8")


async def export_svg(html_path: Path, svg_path: Path) -> str:
    return await asyncio.to_thread(export_svg_sync, html_path, svg_path)

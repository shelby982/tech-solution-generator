"""pytest 全局配置：把 backend/ 加入 sys.path，让 `from services.*` / `from infra.*` 能被解析。"""
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

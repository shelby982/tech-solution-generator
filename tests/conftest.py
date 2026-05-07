"""
pytest 配置：将 backend 目录加入 sys.path，
使得 `from main import app` 等导入可以正常工作。
"""
import sys
from pathlib import Path

# backend/ 目录
BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

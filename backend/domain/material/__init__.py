"""素材聚合：Material、Chunk、MaterialRepository。"""

from .models import Chunk, Material
from .repository import MaterialRepository

__all__ = ["Material", "Chunk", "MaterialRepository"]

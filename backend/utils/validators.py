import re
from fastapi import HTTPException

_UUID4_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
)


def validate_uuid(value: str, label: str = "ID") -> None:
    if not _UUID4_RE.match(value):
        raise HTTPException(status_code=400, detail=f"无效的 {label} 格式")

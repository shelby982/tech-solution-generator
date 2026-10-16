"""文件模板及版本标识。修改模板不影响已创建任务的模板快照。"""
import hashlib
from pathlib import Path

ROOT = Path(__file__).parent / 'templates'


def load_template(key):
    from services.chapter_studio import TEMPLATES
    if key not in TEMPLATES: raise ValueError('未知交付格式')
    data = (ROOT / f'{key}.docx').read_bytes()
    return data, hashlib.sha256(data).hexdigest()

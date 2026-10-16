"""固定示例，仅用于比较交付格式，不包含项目数据。"""
from functools import lru_cache


SAMPLE_SECTIONS = [
    {'title':'技术方案示例','level':1,'done':True,'content':
     '本样张用于核对交付格式中的正文字体、标题层级、表格和图题。正式导出采用相同样式，正文内容不因切换格式而改变。\n\n'
     '## 编排原则\n\n正文围绕**评分要求**展开，将拟实施方案和需要补充的证明材料分别说明。\n\n'
     '1. 说明项目目标和响应范围。\n   - 关联技术规范原文。\n   - 明确验证方法。\n2. 核对图表与证明材料。\n\n'
     '| 核对项目 | 排版要求 |\n| --- | --- |\n| 标题 | 跟随章节层级，避免孤立在页尾 |\n| 表格 | 统一边框，跨页重复表头 |\n| 图片 | 适配版心宽度，附带题注 |\n\n'
     '## 关系图示例\n\n:::diagram 方案组成\n需求分析 -> 技术方案 -> 验证与交付\n:::'},
]


@lru_cache(maxsize=6)
def sample_docx(key, version):
    from . import sections_to_docx
    return sections_to_docx(SAMPLE_SECTIONS, '交付格式样张', template=key)


@lru_cache(maxsize=6)
def sample_pdf(key, version):
    from .preview import render_pdf
    return render_pdf(sample_docx(key, version))

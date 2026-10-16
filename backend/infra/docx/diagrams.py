"""简单架构／流程关系图。输入仅为节点文本，不执行代码或访问 URL。"""
import io
from pathlib import Path


def diagram_png(value):
    from PIL import Image, ImageDraw, ImageFont
    nodes = [n.strip() for n in value.replace('→', '->').split('->') if n.strip()]
    if not 2 <= len(nodes) <= 10 or any(len(n) > 60 for n in nodes):
        raise ValueError('图示需包含 2—10 个节点，以 -> 连接，每个节点不超过 60 字')
    # 竖向排列适配 A4，避免横向缩放后文字无法阅读。
    image = Image.new('RGB', (1000, 150 * len(nodes)), 'white')
    draw = ImageDraw.Draw(image)
    fonts = ['/System/Library/Fonts/STHeiti Light.ttc', '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
             '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc']
    font_path = next((p for p in fonts if Path(p).is_file()), None)
    if not font_path:
        raise ValueError('缺少中文图示字体，请安装 Noto Sans CJK 后重试')
    font = ImageFont.truetype(font_path, 28)
    for index, node in enumerate(nodes):
        y = index * 150 + 15
        draw.rounded_rectangle((80, y, 920, y + 98), radius=15, fill='#eff6ff', outline='#3b82f6', width=2)
        lines = [node[i:i + 25] for i in range(0, len(node), 25)]
        draw.multiline_text((500, y + 49), '\n'.join(lines), font=font, fill='#1e3a8a', anchor='mm', align='center')
        if index < len(nodes) - 1:
            draw.line((500, y + 100, 500, y + 137), fill='#64748b', width=3)
            draw.polygon(((493, y + 130), (507, y + 130), (500, y + 140)), fill='#64748b')
    output = io.BytesIO()
    image.save(output, 'PNG')
    return output.getvalue()

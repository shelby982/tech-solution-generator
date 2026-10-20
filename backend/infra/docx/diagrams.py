"""信息图导出为 PNG：按图型画不同版式（不执行代码、不访问 URL）。"""
import io
from pathlib import Path

_FONT_CANDIDATES = ['/System/Library/Fonts/STHeiti Light.ttc',
                    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
                    '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc']


def _font(size: int):
    from PIL import ImageFont
    path = next((p for p in _FONT_CANDIDATES if Path(p).is_file()), None)
    if not path:
        raise ValueError('缺少中文图示字体，请安装 Noto Sans CJK 后重试')
    return ImageFont.truetype(path, size)


def _structure(value: str):
    """把 `type=… 标题` + 正文行解析成结构（与 services/diagrams 口径一致）。"""
    from services.diagrams import parse_diagram
    lines = [line for line in str(value or '').splitlines() if line.strip()]
    if not lines:
        raise ValueError('图示内容为空')
    return parse_diagram(':::diagram ' + lines[0] if not lines[0].startswith(':::') else lines[0], lines[1:])


def _wrap(draw, text, font, max_width):
    lines, current = [], ''
    for char in str(text or ''):
        if draw.textlength(current + char, font=font) <= max_width:
            current += char
        else:
            lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines or ['']


def _box(draw, box, text, font, fill='#eff6ff', outline='#3b82f6', text_fill='#1e3a8a'):
    draw.rounded_rectangle(box, radius=12, fill=fill, outline=outline, width=2)
    x0, y0, x1, y1 = box
    lines = _wrap(draw, text, font, (x1 - x0) - 24)
    draw.multiline_text(((x0 + x1) / 2, (y0 + y1) / 2), '\n'.join(lines), font=font,
                        fill=text_fill, anchor='mm', align='center')


def diagram_png(value):
    """`:::diagram` 的内容（type= 行 + 结构行）→ PNG。"""
    from PIL import Image, ImageDraw
    structure = _structure(value)
    dtype, nodes = structure.get('type') or 'flow', structure.get('nodes') or []
    if not 2 <= len(nodes) <= 12:
        raise ValueError('图示需包含 2—12 个节点')
    font = _font(24)
    if dtype == 'layered':
        return _layered_png(structure, font)
    if dtype == 'timeline':
        return _timeline_png(structure, font)
    return _flow_png(structure, font)


def _to_png(image):
    output = io.BytesIO()
    image.save(output, 'PNG')
    return output.getvalue()


def _flow_png(structure, font):
    """横向流程：一列放不下就换行（每行最多 4 个）。"""
    from PIL import Image, ImageDraw
    nodes = structure['nodes']
    box_w, box_h, gap, margin = 220, 92, 56, 40
    per_row = min(4, len(nodes))
    rows = [nodes[i:i + per_row] for i in range(0, len(nodes), per_row)]
    row_h = box_h + 70
    width = margin * 2 + per_row * box_w + (per_row - 1) * gap
    height = margin * 2 + len(rows) * row_h
    image = Image.new('RGB', (max(720, width), max(200, height)), 'white')
    draw = ImageDraw.Draw(image)
    for r, row in enumerate(rows):
        y = margin + r * row_h
        for c, node in enumerate(row):
            x = margin + c * (box_w + gap)
            _box(draw, (x, y, x + box_w, y + box_h), node.get('label') or '', font)
            if c < len(row) - 1:
                draw.line((x + box_w + 6, y + box_h / 2, x + box_w + gap - 10, y + box_h / 2),
                          fill='#64748b', width=3)
                draw.polygon(((x + box_w + gap - 18, y + box_h / 2 - 7),
                              (x + box_w + gap - 18, y + box_h / 2 + 7),
                              (x + box_w + gap - 8, y + box_h / 2)), fill='#64748b')
    return _to_png(image)


def _layered_png(structure, font):
    """分层：同层横排一行，层与层之间留白，左侧标注层级。"""
    from PIL import Image, ImageDraw
    nodes = structure['nodes']
    levels: dict[int, list] = {}
    for node in nodes:
        levels.setdefault(int(node.get('level') or 0), []).append(node)
    box_w, box_h, gap, margin, layer_gap = 210, 78, 26, 40, 54
    max_cols = max(len(v) for v in levels.values())
    width = margin * 2 + max_cols * box_w + (max_cols - 1) * gap
    height = margin * 2 + len(levels) * box_h + (len(levels) - 1) * layer_gap
    image = Image.new('RGB', (max(720, width), max(180, height)), 'white')
    draw = ImageDraw.Draw(image)
    palette = [('#1e3a8a', '#dbeafe'), ('#1d4ed8', '#eff6ff'), ('#3b82f6', '#f8fbff')]
    for index, level in enumerate(sorted(levels)):
        y = margin + index * (box_h + layer_gap)
        items = levels[level]
        total = len(items) * box_w + (len(items) - 1) * gap
        x = max(margin, (width - total) // 2)
        text_fill, fill = palette[min(index, 2)]
        for node in items:
            _box(draw, (x, y, x + box_w, y + box_h), node.get('label') or '', font,
                 fill=fill, outline='#93c5fd', text_fill=text_fill)
            x += box_w + gap
    return _to_png(image)


def _timeline_png(structure, font):
    """时间线：横向轴 + 交替上下的事件框。"""
    from PIL import Image, ImageDraw
    nodes = structure['nodes']
    box_w, box_h, step, margin = 200, 74, 230, 50
    width = margin * 2 + step * (len(nodes) - 1) + box_w
    height = 320
    image = Image.new('RGB', (max(760, width), height), 'white')
    draw = ImageDraw.Draw(image)
    axis_y = height // 2
    draw.line((margin, axis_y, width - margin, axis_y), fill='#94a3b8', width=3)
    for i, node in enumerate(nodes):
        cx = margin + box_w / 2 + i * step
        draw.ellipse((cx - 7, axis_y - 7, cx + 7, axis_y + 7), fill='#1d4ed8')
        up = i % 2 == 0
        box = (cx - box_w / 2, axis_y - 24 - box_h if up else axis_y + 24,
               cx + box_w / 2, axis_y - 24 if up else axis_y + 24 + box_h)
        _box(draw, box, node.get('label') or '', font)
        when = str(node.get('when') or '')
        if when:
            label_font = _font(20)
            ty = box[1] - 24 if up else box[3] + 6
            draw.text((cx, ty), when, font=label_font, fill='#1d4ed8', anchor='mm')
    return _to_png(image)

"""CommonMark 语法树到可持久化文档块。解析阶段不访问文件或网络。"""
from markdown_it import MarkdownIt
import re

MODEL_VERSION = 1


def _diagram(state, start, end, silent):
    position = state.bMarks[start] + state.tShift[start]
    line = state.src[position:state.eMarks[start]]
    if not line.startswith(':::diagram ') or state.sCount[start] - state.blkIndent >= 4:
        return False
    if silent:
        return True
    stop = start + 1
    while stop < end and state.src[state.bMarks[stop]:state.eMarks[stop]].strip() != ':::':
        stop += 1
    if stop == end:
        raise ValueError('关系图缺少结束标记 :::')
    token = state.push('fence', 'code', 0)
    token.info = 'response-diagram ' + line[len(':::diagram '):]
    token.content = state.getLines(start + 1, stop, state.blkIndent, False)
    token.map = [start, stop + 1]
    state.line = stop + 1
    return True


def inline_runs(tokens):
    runs, bold, italic, strike = [], False, False, False
    for token in tokens or []:
        if token.type == 'strong_open': bold = True
        elif token.type == 'strong_close': bold = False
        elif token.type == 'em_open': italic = True
        elif token.type == 'em_close': italic = False
        elif token.type == 's_open': strike = True
        elif token.type == 's_close': strike = False
        elif token.type == 'image':
            url = token.attrGet('src') or ''
            runs.append({'type': 'image', 'url': url, 'caption': token.content})
        elif token.type in ('text', 'code_inline', 'softbreak', 'hardbreak', 'html_inline'):
            text = ' ' if token.type == 'softbreak' else '\n' if token.type == 'hardbreak' else token.content
            if text.lower() in ('<br>', '<br/>', '<br />'): text = '\n'
            runs.append({'type': 'text', 'text': text, 'bold': bold, 'italic': italic,
                         'strike': strike, 'code': token.type == 'code_inline'})
    return runs


def _cells_count(line: str) -> int:
    body = line.strip().strip('|')
    return len([part for part in body.split('|')])


def _normalize_tables(value):
    """表格规整：
    ① 表格前后补空行（`表：xxx` 紧跟 `| 头 |` 时 markdown-it 不识别表格）
    ② 分隔行 `|---|` 的列数与表头不一致时，按表头列数重建（否则整张表退化成段落）
    """
    lines = str(value or '').splitlines()
    result = []
    for index, line in enumerate(lines):
        is_table = line.strip().startswith('|')
        is_prev_table = bool(result) and result[-1].strip().startswith('|')
        if is_table and result and not is_prev_table and result[-1].strip():
            result.append('')
        if is_prev_table and not is_table and line.strip():
            result.append('')
        # 修正分隔行列数
        if is_table and set(line.strip().replace('|', '').replace('-', '').replace(':', '').strip()) == set():
            header = next((item for item in reversed(result) if item.strip().startswith('|')), '')
            header_cols = _cells_count(header) if header else 0
            if header_cols and _cells_count(line) != header_cols:
                line = '|' + '|'.join(['---'] * header_cols) + '|'
        result.append(line)
    return "\n".join(result)


def parse_markdown(value):
    value = _normalize_tables(value)
    parser = MarkdownIt('commonmark', {'html': False}).enable(['table', 'strikethrough'])
    parser.block.ruler.before('fence', 'response_diagram', _diagram,
                             {'alt': ['paragraph', 'reference', 'blockquote', 'list']})
    tokens = parser.parse(value or '')
    result, lists, quote = [], [], 0
    heading, table, row, cell, item_first = None, None, None, None, False
    for token in tokens:
        kind = token.type
        if kind == 'heading_open': heading = int(token.tag[1:])
        elif kind == 'heading_close': heading = None
        elif kind in ('bullet_list_open', 'ordered_list_open'):
            lists.append({'ordered': kind == 'ordered_list_open', 'next': int(token.attrGet('start') or 1)})
        elif kind in ('bullet_list_close', 'ordered_list_close'): lists.pop()
        elif kind == 'list_item_open': item_first = True
        elif kind == 'blockquote_open': quote += 1
        elif kind == 'blockquote_close': quote -= 1
        elif kind == 'table_open': table = {'type': 'table', 'rows': []}
        elif kind == 'tr_open': row = []
        elif kind in ('th_open', 'td_open'): cell = []
        elif kind in ('th_close', 'td_close'): row.append(cell); cell = None
        elif kind == 'tr_close': table['rows'].append(row); row = None
        elif kind == 'table_close': result.append(table); table = None
        elif kind in ('fence', 'code_block'):
            if token.info.startswith('response-diagram '):
                result.append({'type': 'diagram', 'caption': token.info[len('response-diagram '):], 'text': token.content})
            else: result.append({'type': 'code', 'text': token.content})
        elif kind == 'hr': result.append({'type': 'separator'})
        elif kind == 'inline':
            runs = inline_runs(token.children)
            if table is not None:
                cell.extend(runs)
                continue
            block = {'type': 'heading' if heading else 'paragraph', 'runs': runs}
            if heading: block['level'] = heading
            if lists:
                block['list'] = {'depth': len(lists), 'ordered': lists[-1]['ordered'],
                                 'number': lists[-1]['next'], 'first': item_first}
                if item_first: lists[-1]['next'] += 1
                item_first = False
            if quote: block['quote_depth'] = quote
            result.append(block)
    headings = [b['level'] for b in result if b['type'] == 'heading']
    if headings:
        minimum = min(headings)
        for block in result:
            if block['type'] == 'heading': block['relative_level'] = block['level'] - minimum + 1
    return result


def render_blocks(doc, blocks, *, chapter_level, images):
    import re
    from docx.shared import Cm
    from docx.oxml import OxmlElement
    from . import _add_studio_image, _add_placeholder_image, _PH_IMG_BLOCK_PAT, _add_runs_with_placeholders
    from .diagrams import diagram_png

    def add_runs(paragraph, runs):
        for part in runs:
            if part['type'] == 'image':
                url = part['url']
                if not re.fullmatch(r'/api/studio/materials/\d+/image', url):
                    raise ValueError('图片必须上传到本项目后引用，不能引用外部 URL')
                if url not in (images or {}): raise ValueError(f"图片未纳入快照：{part['caption']}")
                _add_studio_image(doc, images[url], part['caption'])
                continue
            start = len(paragraph.runs)
            _add_runs_with_placeholders(paragraph, part['text'])
            for run in paragraph.runs[start:]:
                run.bold = run.bold or part.get('bold', False)
                run.italic, run.font.strike = part.get('italic', False), part.get('strike', False)
                if part.get('code'): run.font.name = 'Consolas'

    for block in blocks:
        kind = block['type']
        if kind == 'separator': continue
        if kind == 'diagram': _add_studio_image(doc, diagram_png(block['text']), block['caption']); continue
        if kind == 'code':
            paragraph = doc.add_paragraph(style='Code Block' if 'Code Block' in doc.styles else 'Normal')
            paragraph.add_run(block['text'].rstrip()).font.name = 'Consolas'
            continue
        if kind == 'table':
            rows = block['rows']
            if not rows: continue
            table = doc.add_table(rows=len(rows), cols=max(len(row) for row in rows))
            table.style = 'Response Table' if 'Response Table' in doc.styles else 'Table Grid'
            repeat = OxmlElement('w:tblHeader'); table.rows[0]._tr.get_or_add_trPr().append(repeat)
            for i, row in enumerate(rows):
                for j, runs in enumerate(row):
                    add_runs(table.cell(i,j).paragraphs[0], runs)
                    if i == 0:
                        for run in table.cell(i,j).paragraphs[0].runs: run.bold = True
            continue
        text = ''.join(p.get('text','') for p in block.get('runs', []))
        placeholder = _PH_IMG_BLOCK_PAT.match(text)
        if placeholder:
            _add_placeholder_image(doc, placeholder.group(1), placeholder.group(2)); continue
        if kind == 'heading':
            paragraph = doc.add_paragraph(style=f"Heading {min(9, chapter_level + block.get('relative_level',1))}")
        else:
            paragraph = doc.add_paragraph(style='Quote' if block.get('quote_depth') else 'Normal')
        listing = block.get('list')
        if listing:
            paragraph.paragraph_format.left_indent = Cm(.6 * listing['depth'])
            if listing['first']:
                paragraph.add_run(f"{listing['number']}. " if listing['ordered'] else '• ')
        add_runs(paragraph, block.get('runs', []))


def image_urls(blocks):
    for block in blocks:
        groups = [block.get('runs', [])]
        if block['type'] == 'table': groups += [cell for row in block['rows'] for cell in row]
        for group in groups:
            for part in group:
                if part['type'] == 'image':
                    if not re.fullmatch(r'/api/studio/materials/\d+/image', part['url']):
                        raise ValueError('请先上传图片，再使用本项目内部图片地址')
                    yield part['url']


def model_html(blocks):
    from html import escape
    def inline(runs):
        out=[]
        for part in runs:
            if part['type']=='image':
                if not re.fullmatch(r'/api/studio/materials/\d+/image',part['url']): raise ValueError('不支持外部图片引用')
                out.append(f'<figure><img src="{escape(part["url"],quote=True)}" alt="{escape(part["caption"],quote=True)}"><figcaption>{escape(part["caption"])}</figcaption></figure>')
            else:
                text=escape(part['text']).replace('\n','<br>')
                for key,tag in [('code','code'),('bold','strong'),('italic','em'),('strike','s')]:
                    if part.get(key):text=f'<{tag}>{text}</{tag}>'
                out.append(text)
        return ''.join(out)
    html=[]
    for block in blocks:
        kind=block['type']
        if kind=='table':
            html.append('<div class="spec-table-scroll"><table>'+''.join('<tr>'+''.join(f'<{"th" if i==0 else "td"}>{inline(cell)}</{"th" if i==0 else "td"}>' for cell in row)+'</tr>' for i,row in enumerate(block['rows']))+'</table></div>')
        elif kind=='code':html.append(f'<pre><code>{escape(block["text"])}</code></pre>')
        elif kind=='diagram':
            # 统一走 services.diagrams 的解析：支持 type=layered/flow/timeline 与多行写法
            # 图示：头部用 caption（含 type=），节点用正文行 → 不丢第一个节点
            from services.diagrams import parse_diagram
            caption = str(block.get('caption') or '').strip()
            body_lines=[line.strip() for line in str(block.get('text') or '').splitlines() if line.strip()]
            try:
                structure = parse_diagram(':::diagram ' + caption, body_lines)
            except Exception:
                structure = {}
            nodes=[str(n.get('label') or '').strip() for n in structure.get('nodes') or [] if str(n.get('label') or '').strip()]
            if not nodes:
                nodes = body_lines[:12]
            if not nodes:
                html.append('<p class="cs-muted">（本节图示待补充）</p>');continue
            if len(nodes)>12: nodes=nodes[:12]
            kind='layered'
            m=__import__('re').search(r'type=([a-z]+)', caption)
            if m: kind=m.group(1)
            arrow = '→' if kind in ('flow','timeline') else '↓'
            html.append('<figure class="cs-diagram">'+''.join(('' if i==0 else f'<span class="cs-diagram-arrow">{arrow}</span>')+f'<div>{escape(n)}</div>' for i,n in enumerate(nodes))+f'<figcaption>{escape(block["caption"])}</figcaption></figure>')
        elif kind=='separator':html.append('<hr>')
        else:
            tag=f'h{min(6,block.get("relative_level",1)+2)}' if kind=='heading' else 'p'
            listing=block.get('list');prefix=''
            if listing and listing['first']:prefix=f'{listing["number"]}. ' if listing['ordered'] else '• '
            indent=f' style="margin-left:{min(10,listing["depth"])*16}px"' if listing else ''
            content=f'<{tag}{indent}>{prefix}{inline(block.get("runs",[]))}</{tag}>'
            html.append(f'<blockquote>{content}</blockquote>' if block.get('quote_depth') else content)
    return ''.join(html)

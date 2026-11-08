"""Persistable writing progress projected into editable text/chart blocks."""
import re


def progress_blocks(state: dict, base: str) -> list[dict]:
    sections = state.get('sections') or []
    if state.get('scope') == 'revision' and sections:
        section = sections[0]
        return [{'id': 'revision-text', 'kind': 'text', 'title': section['title'], 'heading': '',
                 'content': state.get('before', '') + section.get('text', '') + state.get('after', ''),
                 'state': section.get('state') or 'running'}]
    draft = base
    for index, section in enumerate(sections):
        title = section['title']
        pattern = re.compile(r'^##[ \t]+' + re.escape(title) + r'[ \t]*\n[\s\S]*?(?=^##[ \t]+|\Z)', re.M)
        marker = f'\x00SECTION{index}\x00'
        draft = pattern.sub(lambda _: marker, draft, count=1) if pattern.search(draft) else draft.rstrip() + '\n\n' + marker
    blocks = []
    for part_index, part in enumerate(re.split(r'(\x00SECTION\d+\x00)', draft)):
        match = re.fullmatch(r'\x00SECTION(\d+)\x00', part)
        if not match:
            if part.strip():
                blocks.append({'id': f'base-{part_index}', 'kind': 'text', 'title': '已有正文', 'heading': '', 'content': part, 'state': 'done'})
            continue
        index = int(match[1])
        section = sections[index]
        text = section.get('text') or ''
        if state.get('directory_locked'):
            from services.confirmed_directory import body_only
            text = body_only(text, section['title'])
        text = re.sub(r'^##\s+' + re.escape(section['title']) + r'\s*\n', '', text)
        charts = section.get('charts') or []
        pieces = re.split(r'(\[\[(?:TABLE|FIGURE):[^\]]+\]\])', text)
        seen = set()
        blocks.append({'id': f's{index}-heading', 'kind': 'heading', 'title': section['title'], 'heading': '## ',
                       'content': f"## {section['title']}\n\n", 'state': 'done'})
        for piece_index, piece in enumerate(pieces):
            marker = re.fullmatch(r'\[\[(TABLE|FIGURE):([^\]]+)\]\]', piece)
            if marker:
                title = marker[2]
                chart = next((item for item in charts if item['title'] == title), None)
                if chart:
                    seen.add(title)
                    blocks.append(chart_block(index, chart))
            elif piece.strip():
                blocks.append({'id': f's{index}-text-{piece_index}', 'kind': 'text', 'title': section['title'], 'heading': '',
                               'content': piece, 'state': section.get('state') or 'running'})
        for chart in charts:
            if chart['title'] not in seen:
                blocks.append(chart_block(index, chart))
    return blocks


def chart_block(index, chart):
    return {'id': f"s{index}-{chart['role']}-{chart['title']}", 'kind': chart['role'], 'title': chart['title'],
            'purpose': chart.get('error') or '按章节要求编制', 'content': chart.get('markdown') or '',
            'state': chart.get('state') or 'queued', 'section_index': index}

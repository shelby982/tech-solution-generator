"""按来源文档的标题树展示要求，不以语义主题替代原文目录。"""
import re

from services.spec_attention import original_topic_title


def build_spec_structure(requirements, sections, source_files=None):
    files = list(dict.fromkeys(source_files if source_files is not None else (r.get('source_file', '') for r in requirements)))
    roots, index = [], {}
    for filename in files:
        stack = []
        for section in sections or []:
            if section.get('source_file', '') != filename:
                continue
            level = max(1, int(section.get('level') or 1))
            while stack and stack[-1]['level'] >= level:
                stack.pop()
            title = section.get('title') or ''
            node = dict(topic=title, display_title=original_topic_title(title, [filename], [section]),
                        level=level, source_files=[filename], requirements=[], children=[],
                        original_content=section.get('raw_content') or '')
            path = tuple(n['topic'] for n in stack) + (title,)
            node['source_path'] = list(path)
            node['special_marks'] = list(section.get('special_marks') or [])
            index.setdefault((filename, path), []).append(node)
            (stack[-1]['children'] if stack else roots).append(node)
            stack.append(node)
    unmatched = []
    for row in requirements:
        location = re.sub(r'\s*·\s*第\s*\d+\s*条.*$', '', row.get('title') or '')
        parts = tuple(p.strip() for p in location.split(' > '))
        candidates = []
        # 表格交付项等在章节路径后有附加定位，归入最长匹配的原文章节。
        for length in range(len(parts), 0, -1):
            candidates = index.get((row.get('source_file', ''), parts[:length]), [])
            if candidates:
                break
        if len(candidates) == 1:
            candidates[0]['requirements'].append(row)
        else:
            unmatched.append(row)
    if unmatched:
        roots.append(dict(topic='原文位置待核对', display_title='原文位置待核对', level=1,
                          source_files=files, requirements=unmatched, children=[], original_content=''))

    def count(node):
        node['count'] = len(node['requirements']) + sum(count(c) for c in node['children'])
        return node['count']
    for root in roots:
        count(root)
    return roots
